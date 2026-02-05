import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { GatewayChatClient } from '/opt/homebrew/lib/node_modules/clawdbot/dist/tui/gateway-chat.js';

const PORT = process.env.RETELL_SYNC_PORT ? Number(process.env.RETELL_SYNC_PORT) : 3335;
const SHARED_SECRET = process.env.RETELL_SYNC_SECRET;
const RETELL_AGENT_ID = process.env.RETELL_AGENT_ID || null; // if set, only log webhook events for this agent

if (!SHARED_SECRET) {
  console.error('Missing RETELL_SYNC_SECRET env var');
  process.exit(1);
}

const SESSION_KEY = process.env.CLAWDBOT_SESSION_KEY || 'agent:main:main';

// --- Status notifications (Telegram) ---
// Goal: when Retell triggers work in Clawdbot, mirror status updates to Telegram so the user
// sees progress even if they're currently on a call.
const STATUS_NOTIFY_ENABLED = process.env.STATUS_NOTIFY_ENABLED !== '0';
const STATUS_NOTIFY_CHANNEL = (process.env.STATUS_NOTIFY_CHANNEL || 'telegram').toLowerCase();
const STATUS_NOTIFY_TO = process.env.STATUS_NOTIFY_TO || ''; // optional override (e.g. telegram:123456)
const STATUS_NOTIFY_MAX_CHARS = process.env.STATUS_NOTIFY_MAX_CHARS
  ? Number(process.env.STATUS_NOTIFY_MAX_CHARS)
  : 3500;

const WORKSPACE_DIR = process.env.CLAWDBOT_WORKSPACE_DIR || process.cwd();
const MEMORY_DIR = path.join(WORKSPACE_DIR, 'memory');

function nyDateStamp(d = new Date()) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(d); // YYYY-MM-DD
}

async function appendToDailyMemory(text) {
  const stamp = nyDateStamp();
  const file = path.join(MEMORY_DIR, `${stamp}.md`);
  await fs.mkdir(MEMORY_DIR, { recursive: true });
  await fs.appendFile(file, text, 'utf8');
}

const gw = new GatewayChatClient({});
gw.start();
await gw.waitForReady();

let cachedNotifyTarget = null;
let cachedNotifyTargetAtMs = 0;

// Retell call lifecycle (best-effort) so we can be more aggressive with status updates while on a call.
let activeCallId = '';
let callOngoing = false;
let lastCallStartedAtMs = 0;

function clampText(text, maxChars) {
  const s = String(text ?? '');
  if (s.length <= maxChars) return s;
  return s.slice(0, Math.max(0, maxChars - 1)) + '…';
}

function oneLine(s) {
  return String(s ?? '')
    .replace(/[\r\n\t]+/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

function summarizeInstruction(message) {
  const s = oneLine(message);
  const stripped = s.replace(/^update system instructions\s*:\s*/i, '').trim();
  return clampText(stripped || s, 160);
}

function shortRun(runId) {
  const s = String(runId ?? '').trim();
  return s.length > 10 ? s.slice(0, 8) : s;
}

async function resolveNotifyTarget() {
  if (!STATUS_NOTIFY_ENABLED) return null;

  const now = Date.now();
  if (cachedNotifyTarget && now - cachedNotifyTargetAtMs < 60_000) return cachedNotifyTarget;

  if (STATUS_NOTIFY_TO.trim()) {
    cachedNotifyTarget = {
      channel: STATUS_NOTIFY_CHANNEL,
      to: STATUS_NOTIFY_TO.trim(),
      accountId: process.env.STATUS_NOTIFY_ACCOUNT_ID?.trim() || undefined,
    };
    cachedNotifyTargetAtMs = now;
    return cachedNotifyTarget;
  }

  // Best-effort: use the session's last delivery target (usually the Telegram chat this session is bound to).
  const listed = await gw.client.request('sessions.list', {
    search: SESSION_KEY,
    limit: 10,
    includeGlobal: true,
    includeUnknown: true,
  });

  const session = Array.isArray(listed?.sessions)
    ? listed.sessions.find((s) => s?.key === SESSION_KEY)
    : null;

  const to = session?.lastTo || session?.deliveryContext?.to || session?.origin?.to || '';
  const accountId = session?.lastAccountId || session?.deliveryContext?.accountId || session?.origin?.accountId;

  if (!to) {
    cachedNotifyTarget = null;
    cachedNotifyTargetAtMs = now;
    return null;
  }

  cachedNotifyTarget = {
    channel: STATUS_NOTIFY_CHANNEL,
    to,
    accountId: typeof accountId === 'string' && accountId.trim() ? accountId.trim() : undefined,
  };
  cachedNotifyTargetAtMs = now;
  return cachedNotifyTarget;
}

async function sendStatusNotification(text) {
  try {
    const target = await resolveNotifyTarget();
    if (!target?.to) return;

    await gw.client.request('send', {
      channel: target.channel,
      to: target.to,
      accountId: target.accountId,
      message: clampText(String(text ?? '').trim(), STATUS_NOTIFY_MAX_CHARS),
      sessionKey: SESSION_KEY, // mirror into the transcript
      idempotencyKey: randomUUID(),
    });
  } catch (err) {
    // Never block Retell / never crash the bridge for notification failures.
    console.error('status_notify_failed', err);
  }
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString('utf8') || '{}';
  try { return JSON.parse(raw); } catch { return null; }
}

function send(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
  });
  res.end(body);
}

async function getLastAssistantText() {
  const hist = await gw.loadHistory({ sessionKey: SESSION_KEY, limit: 10 });
  const messages = hist?.messages || hist?.history || hist || [];
  // Walk backwards for assistant text.
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m) continue;
    const role = m.role || m.kind;
    if (role === 'assistant') {
      const text = m.text ?? m.content ?? m.message ?? '';
      if (typeof text === 'string' && text.trim()) return text.trim();
      if (Array.isArray(text)) return text.map(x => (typeof x === 'string' ? x : '')).join('').trim();
    }
  }
  return '';
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'method_not_allowed' });

    const url = new URL(req.url, `http://${req.headers.host}`);

    const token = req.headers['x-retell-token'] || url.searchParams.get('token');
    if (token !== SHARED_SECRET) return send(res, 401, { ok: false, error: 'unauthorized' });

    const body = await readJson(req);
    if (!body) return send(res, 400, { ok: false, error: 'invalid_json' });

    // 1) Retell voice call → webhook logging (Persona/Retell events, transcripts, etc)
    if (url.pathname === '/retell/webhook') {
      // Don’t block Retell on heavy work.
      send(res, 200, { ok: true });

      const now = new Date();
      const header = `\n\n---\n[retell webhook] ${now.toISOString()}\n`;

      // Best-effort: identify event + call id + agent id fields (Retell varies by product/event type)
      const eventType =
        body.event ||
        body.event_type ||
        body.type ||
        body.name ||
        body?.data?.event ||
        body?.data?.event_type ||
        'unknown_event';

      const callId =
        body.call_id ||
        body?.data?.call_id ||
        body?.data?.call?.call_id ||
        body?.call?.call_id ||
        '';

      const agentId =
        body.agent_id ||
        body?.call?.agent_id ||
        body?.data?.agent_id ||
        body?.data?.call?.agent_id ||
        '';

      // If configured, only log events for the Iris agent.
      // If agent id is missing on an event, treat it as non-Iris and ignore.
      if (RETELL_AGENT_ID && agentId !== RETELL_AGENT_ID) {
        return;
      }

      // Call lifecycle (notify in Telegram + track whether we're currently on a call)
      try {
        const t = String(eventType || '').toLowerCase();
        if (t.includes('call_started')) {
          callOngoing = true;
          activeCallId = String(callId || '');
          lastCallStartedAtMs = Date.now();
          void sendStatusNotification(
            `📞 Call started${activeCallId ? ` (${activeCallId})` : ''}. I’ll post task status updates in this chat.`
          );
        }
        if (t.includes('call_ended')) {
          // Mark ended if it's the active call, otherwise just best-effort.
          if (!activeCallId || String(callId || '') === activeCallId) {
            callOngoing = false;
            activeCallId = '';
          }
          void sendStatusNotification(
            `📞 Call ended${callId ? ` (${callId})` : ''}. Transcript/log saved.`
          );
        }
      } catch {}

      // Transcript extraction:
      // - sometimes a plain string
      // - sometimes a list of utterances
      // - sometimes nested under data
      let transcript = '';
      const maybeTranscript =
        body.transcript ||
        body.call_transcript ||
        body?.data?.transcript ||
        body?.data?.call_transcript;

      if (typeof maybeTranscript === 'string') {
        transcript = maybeTranscript;
      } else {
        const turns =
          body?.data?.transcript_object ||
          body?.transcript_object ||
          body?.data?.transcript?.utterances ||
          body?.transcript?.utterances ||
          body?.data?.messages ||
          body?.messages;

        if (Array.isArray(turns) && turns.length) {
          transcript = turns
            .map((t) => {
              const role = t.role || t.speaker || t.participant || '';
              const text = t.text || t.content || t.utterance || t.message || '';
              const line = `${role ? role + ': ' : ''}${text}`.trim();
              return line;
            })
            .filter(Boolean)
            .join('\n');
        }
      }

      const safeTranscript = transcript || '(no transcript on this event)';
      const raw = JSON.stringify(body, null, 2);

      const text =
        `${header}` +
        `Event: ${eventType}${callId ? `\nCall ID: ${callId}` : ''}\n\n` +
        `Transcript:\n${safeTranscript}\n\n` +
        `Raw:\n\n\`\`\`json\n${raw}\n\`\`\`\n`;

      // 1) Always append to daily memory
      appendToDailyMemory(text).catch((err) => console.error('failed_to_append_memory', err));

      // 2) Also store per-call artifacts (best effort) so transcripts are easy to find later.
      // Only write if we have a call id.
      if (callId) {
        const callDir = path.join(MEMORY_DIR, 'retell-calls');
        const base = path.join(callDir, callId);
        fs.mkdir(callDir, { recursive: true })
          .then(() => Promise.all([
            fs.writeFile(`${base}.json`, raw, 'utf8'),
            fs.writeFile(`${base}.transcript.txt`, safeTranscript + '\n', 'utf8'),
          ]))
          .catch(() => {});
      }

      return;
    }

    // 2) Retell custom function → Clawdbot agent execution
    if (url.pathname !== '/retell/sync') return send(res, 404, { ok: false, error: 'not_found' });

    // Retell custom function payload conventions: either args-only or wrapped.
    const args = body.args ?? body;
    const message = args.message || args.instruction || args.text;
    if (!message || typeof message !== 'string') {
      return send(res, 400, { ok: false, error: 'missing_message' });
    }

    const deliver = args.deliver ?? false; // Retell expects a fast tool response; Telegram status updates are handled here.
    const thinking = args.thinking || 'low';

    const idempotencyKey = randomUUID();
    const summary = summarizeInstruction(message);

    // Smart notification logic:
    // - While you're ON a call: send "Working" immediately.
    // - Off-call: only send "Working" if it takes longer than ~1.2s (avoid spam).
    // - Always send a final "Done" / "Error".
    // - If the tool call times out (run continues), post "In progress" and follow up on completion.
    let workingNotified = false;
    let workingTimer = null;

    const startedAt = Date.now();

    // Kick off the agent.
    const agentAck = await gw.client.request('agent', {
      sessionKey: SESSION_KEY,
      message,
      thinking,
      deliver,
      idempotencyKey,
    });

    const runId = agentAck?.runId || idempotencyKey;
    const runTag = runId ? ` (#${shortRun(runId)})` : '';

    if (callOngoing) {
      workingNotified = true;
      void sendStatusNotification(`⏳ Working${runTag}: ${summary}`);
    } else {
      workingTimer = setTimeout(() => {
        workingNotified = true;
        void sendStatusNotification(`⏳ Working${runTag}: ${summary}`);
      }, 1200);
    }

    // Wait for completion (cap to keep voice UX snappy).
    // Retell tool calls should return quickly; long-running work can complete asynchronously.
    const timeoutMs = Math.min(12_000, Math.max(1_000, Number(args.timeoutMs || 8_000)));

    const wait = await Promise.race([
      gw.client.request('agent.wait', { runId, timeoutMs }),
      new Promise((resolve) => setTimeout(() => resolve({ runId, status: 'timeout' }), timeoutMs + 250)),
    ]);

    if (workingTimer) clearTimeout(workingTimer);

    let assistantText = '';
    if (wait?.status === 'ok') {
      assistantText = await getLastAssistantText();
    }

    // Log tool usage to daily memory for continuity.
    appendToDailyMemory(
      `\n\n---\n[retell tool] ${new Date().toISOString()}\nMessage:\n${message}\nStatus: ${wait?.status || 'unknown'}\nRunId: ${runId}\n`
    ).catch(() => {});

    // Telegram status notification
    const elapsedMs = Date.now() - startedAt;
    if (wait?.status === 'ok') {
      const body = assistantText ? `\n${assistantText}` : '';
      const finalMsg = `✅ Done${runTag}: ${summary}${body}`;
      // If it finished very quickly (and we were off-call), we likely never sent "Working". That’s fine.
      if (!workingNotified && elapsedMs < 1200) {
        void sendStatusNotification(finalMsg);
      } else {
        // In case the delayed timer never fired (race), ensure there's at least one working-ish update.
        if (!workingNotified) void sendStatusNotification(`⏳ Working${runTag}: ${summary}`);
        void sendStatusNotification(finalMsg);
      }
    } else if (wait?.status === 'timeout') {
      // Ensure user sees that it's still running.
      if (!workingNotified) void sendStatusNotification(`⏳ Working${runTag}: ${summary}`);
      void sendStatusNotification(
        `🟡 In progress${runTag}: ${summary} (I’ll ping you here when it’s finished.)`
      );

      // Follow up asynchronously when the run completes.
      void (async () => {
        const maxMs = 30 * 60 * 1000; // 30 minutes
        const pollTimeoutMs = 60_000;
        const start = Date.now();
        while (Date.now() - start < maxMs) {
          try {
            const w = await gw.client.request('agent.wait', { runId, timeoutMs: pollTimeoutMs });
            if (w?.status === 'ok') {
              const text = await getLastAssistantText();
              const body = text ? `\n${text}` : '';
              await sendStatusNotification(`✅ Done${runTag}: ${summary}${body}`);
              return;
            }
            if (w?.status && w.status !== 'timeout') {
              await sendStatusNotification(`❌ Status (${w.status})${runTag}: ${summary}`);
              return;
            }
          } catch (err) {
            await sendStatusNotification(`❌ Error while tracking status${runTag}: ${summary}`);
            console.error('status_followup_failed', err);
            return;
          }
        }
        await sendStatusNotification(`⚠️ Still running after 30 minutes${runTag}: ${summary}`);
      })();
    } else {
      // Some other status (error/unknown)
      void sendStatusNotification(`❌ Status (${wait?.status || 'unknown'})${runTag}: ${summary}`);
    }

    return send(res, 200, {
      ok: true,
      runId,
      status: wait?.status || 'unknown',
      text:
        assistantText ||
        (wait?.status === 'timeout'
          ? 'Working on that in the background. What would you like to do next?'
          : 'Done.'),
    });

  } catch (err) {
    console.error(err);
    return send(res, 500, { ok: false, error: 'internal_error' });
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`retell-sync-bridge listening on http://127.0.0.1:${PORT}/retell/sync`);
  console.log(`retell-sync-bridge webhook logger on http://127.0.0.1:${PORT}/retell/webhook`);
});

process.on('SIGINT', () => {
  server.close(() => process.exit(0));
});
