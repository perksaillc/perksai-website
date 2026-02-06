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

// Persist a tiny bit of state so "check status" can work without spawning a new agent run.
const STATE_FILE = path.join(WORKSPACE_DIR, 'data', 'retell-sync-state.json');

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

async function readState() {
  try {
    await fs.mkdir(path.dirname(STATE_FILE), { recursive: true });
    const raw = await fs.readFile(STATE_FILE, 'utf8');
    const json = JSON.parse(raw);
    if (!json || typeof json !== 'object') return { runs: [] };
    if (!Array.isArray(json.runs)) json.runs = [];
    return json;
  } catch {
    return { runs: [] };
  }
}

async function writeState(next) {
  try {
    await fs.mkdir(path.dirname(STATE_FILE), { recursive: true });
    await fs.writeFile(STATE_FILE, JSON.stringify(next, null, 2) + '\n', 'utf8');
  } catch (err) {
    // Never crash for state write errors.
    console.error('state_write_failed', err);
  }
}

async function recordRunStart({ runId, summary, message, startedAtMs }) {
  const state = await readState();
  const now = Date.now();
  const item = {
    runId,
    summary,
    message: clampText(oneLine(message), 500),
    startedAtMs: startedAtMs || now,
    updatedAtMs: now,
    status: 'running',
  };
  state.runs = [item, ...state.runs.filter((r) => r?.runId && r.runId !== runId)].slice(0, 50);
  await writeState(state);
}

async function recordRunUpdate({ runId, patch }) {
  const state = await readState();
  let found = false;
  state.runs = (state.runs || []).map((r) => {
    if (r?.runId !== runId) return r;
    found = true;
    return { ...r, ...patch, updatedAtMs: Date.now() };
  });
  if (!found) {
    state.runs = [{ runId, updatedAtMs: Date.now(), ...(patch || {}) }, ...(state.runs || [])].slice(0, 50);
  }
  await writeState(state);
}

function extractRunIdFromText(s) {
  const text = String(s || '');
  // Accept full UUIDs, or a short 8-char run like "#a1b2c3d4".
  const uuid = text.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
  if (uuid) return uuid[0];
  const short = text.match(/#([0-9a-f]{8})\b/i);
  if (short) return short[1];
  return '';
}

function isStatusQuery(message) {
  const s = oneLine(message).toLowerCase();
  return (
    s === 'check status' ||
    s.startsWith('check status ') ||
    s === 'status' ||
    s.startsWith('status ') ||
    s.startsWith('job status') ||
    s.includes('status update') ||
    s.includes('are you done')
  );
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

function chunkText(text, maxChars) {
  const s = String(text ?? '').trim();
  if (!s) return [];
  if (s.length <= maxChars) return [s];

  // Try to split on paragraph boundaries first, then lines, then hard-split.
  const chunks = [];
  let remaining = s;

  while (remaining.length > maxChars) {
    // Prefer splitting at a boundary within the window.
    const window = remaining.slice(0, maxChars + 1);

    const paragraphIdx = window.lastIndexOf('\n\n');
    const lineIdx = window.lastIndexOf('\n');
    const spaceIdx = window.lastIndexOf(' ');

    let cut = -1;
    if (paragraphIdx > maxChars * 0.5) cut = paragraphIdx + 2;
    else if (lineIdx > maxChars * 0.6) cut = lineIdx + 1;
    else if (spaceIdx > maxChars * 0.8) cut = spaceIdx + 1;
    else cut = maxChars;

    const part = remaining.slice(0, cut).trimEnd();
    if (part) chunks.push(part);
    remaining = remaining.slice(cut).trimStart();
  }

  if (remaining) chunks.push(remaining);
  return chunks;
}

async function sendStatusNotification(text) {
  try {
    const target = await resolveNotifyTarget();
    if (!target?.to) return;

    const raw = String(text ?? '').trim();
    if (!raw) return;

    // If the message is too long, chunk it and annotate parts.
    const parts = chunkText(raw, STATUS_NOTIFY_MAX_CHARS);
    const n = parts.length;

    for (let i = 0; i < n; i++) {
      const prefix = n > 1 ? `(${i + 1}/${n}) ` : '';
      const payload = clampText(prefix + parts[i], STATUS_NOTIFY_MAX_CHARS);
      await gw.client.request('send', {
        channel: target.channel,
        to: target.to,
        accountId: target.accountId,
        message: payload,
        sessionKey: SESSION_KEY, // mirror into the transcript
        idempotencyKey: randomUUID(),
      });
    }
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

function coerceTimestampToMs(ts) {
  if (!ts) return null;
  if (typeof ts === 'number' && Number.isFinite(ts)) {
    // Heuristic: seconds vs ms.
    return ts < 1e12 ? ts * 1000 : ts;
  }
  if (typeof ts === 'string') {
    const n = Number(ts);
    if (Number.isFinite(n)) return n < 1e12 ? n * 1000 : n;
    const d = Date.parse(ts);
    return Number.isFinite(d) ? d : null;
  }
  return null;
}

function extractText(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;

  // Common "content" formats in Clawdbot/OpenAI Responses style:
  // - [{type:"text", text:"..."}, {type:"toolCall", ...}]
  // - [{type:"output_text", text:"..."}, ...]
  if (Array.isArray(value)) {
    const parts = value
      .map((v) => {
        if (!v) return '';
        if (typeof v === 'string') return v;
        if (typeof v === 'object') {
          if (typeof v.text === 'string') return v.text;
          if (typeof v.content === 'string') return v.content;
          if (Array.isArray(v.content)) return extractText(v.content);
        }
        return '';
      })
      .filter(Boolean);
    return parts.join('');
  }

  if (typeof value === 'object') {
    if (typeof value.text === 'string') return value.text;
    if (typeof value.message === 'string') return value.message;
    if (typeof value.content === 'string') return value.content;
    if (Array.isArray(value.content)) return extractText(value.content);
  }

  return '';
}

async function getLastAssistantText({ sinceMs } = {}) {
  const hist = await gw.loadHistory({ sessionKey: SESSION_KEY, limit: 50 });
  const messages = hist?.messages || hist?.history || hist || [];

  // Walk backwards for assistant text, optionally constrained by time.
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m) continue;

    const role = m.role || m.kind;
    if (role !== 'assistant') continue;

    if (sinceMs) {
      const t = coerceTimestampToMs(m.timestamp || m.time || m.createdAt);
      if (t && t < sinceMs - 2000) continue;
    }

    const text = extractText(m.text ?? m.content ?? m.message ?? m);
    if (typeof text === 'string' && text.trim()) return text.trim();
  }

  return '';
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'method_not_allowed' });

    const url = new URL(req.url, `http://${req.headers.host}`);
    const pathname = url.pathname.replace(/\/+$/, '') || '/';

    const token = req.headers['x-retell-token'] || url.searchParams.get('token');
    if (token !== SHARED_SECRET) return send(res, 401, { ok: false, error: 'unauthorized' });

    const body = await readJson(req);
    if (!body) return send(res, 400, { ok: false, error: 'invalid_json' });

    // 1) Retell voice call → webhook logging (Persona/Retell events, transcripts, etc)
    if (pathname === '/retell/webhook') {
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

      const eventTypeNorm = String(eventType || '').toLowerCase();
      const isCallStartedEvent = eventTypeNorm.includes('call_started');
      const isCallEndedEvent = eventTypeNorm.includes('call_ended');

      // Call lifecycle (notify in Telegram + track whether we're currently on a call)
      try {
        if (isCallStartedEvent) {
          callOngoing = true;
          activeCallId = String(callId || '');
          lastCallStartedAtMs = Date.now();
          void sendStatusNotification(
            `📞 Call started${activeCallId ? ` (${activeCallId})` : ''}. I’ll post task status updates in this chat.`
          );
        }
        if (isCallEndedEvent) {
          // Mark ended if it's the active call, otherwise just best-effort.
          if (!activeCallId || String(callId || '') === activeCallId) {
            callOngoing = false;
            activeCallId = '';
          }
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

      // On call end, push the transcript into Telegram (truncated) so it always lands in chat.
      if (isCallEndedEvent) {
        const head = `📞 Call ended${callId ? ` (${callId})` : ''}. Transcript (truncated):`;
        const clip = clampText(safeTranscript, Math.min(STATUS_NOTIFY_MAX_CHARS, 3500));
        void sendStatusNotification(`${head}\n${clip}`);
      }

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
    if (pathname !== '/retell/sync') return send(res, 404, { ok: false, error: 'not_found' });

    // Retell custom function payload conventions: either args-only or wrapped.
    const args = body.args ?? body;
    const message = args.message || args.instruction || args.text;
    if (!message || typeof message !== 'string') {
      return send(res, 400, { ok: false, error: 'missing_message' });
    }

    // Special case: "check status" should NOT spawn a new agent run.
    if (isStatusQuery(message)) {
      const state = await readState();
      const wantRun = extractRunIdFromText(message);

      let item = null;
      if (wantRun) {
        item = (state.runs || []).find((r) => r?.runId === wantRun || shortRun(r?.runId) === wantRun);
      }
      if (!item) {
        // Prefer the newest running job; otherwise the newest job.
        item = (state.runs || []).find((r) => r?.status === 'running') || (state.runs || [])[0] || null;
      }

      if (!item?.runId) {
        return send(res, 200, {
          ok: true,
          status: 'no_jobs',
          message: 'No active jobs to report.',
          text: 'No active jobs to report.',
        });
      }

      // Quick poll: if it completed, grab the latest assistant output.
      let polled = null;
      try {
        polled = await gw.client.request('agent.wait', { runId: item.runId, timeoutMs: 1000 });
      } catch {
        polled = null;
      }

      const isDone = polled?.status === 'ok' || item.status === 'done';
      const status = isDone ? 'done' : (item.status || polled?.status || 'running');
      const runTag = item.runId ? ` (#${shortRun(item.runId)})` : '';

      let bodyText = '';
      if (isDone) {
        const t = await getLastAssistantText({ sinceMs: item.startedAtMs });
        bodyText = t ? `\n${t}` : '';
        await recordRunUpdate({
          runId: item.runId,
          patch: { status: 'done', completedAtMs: Date.now() },
        });
      }

      const responseText = `Status${runTag}: ${status}. ${item.summary || summarizeInstruction(item.message || '')}`.trim() + bodyText;
      return send(res, 200, {
        ok: true,
        runId: item.runId,
        status,
        message: responseText,
        text: responseText,
      });
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

    // Record run so later "check status" can report it without spawning a new job.
    await recordRunStart({ runId, summary, message, startedAtMs: startedAt });

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
    // Wait long enough that Retell can usually speak a real result, but cap to keep the voice UX snappy.
    // (Retell's own tool timeout is typically much higher; this cap is our server-side guardrail.)
    const timeoutMs = Math.min(25_000, Math.max(1_000, Number(args.timeoutMs || 12_000)));

    const wait = await Promise.race([
      gw.client.request('agent.wait', { runId, timeoutMs }),
      new Promise((resolve) => setTimeout(() => resolve({ runId, status: 'timeout' }), timeoutMs + 250)),
    ]);

    if (workingTimer) clearTimeout(workingTimer);

    let assistantText = '';
    if (wait?.status === 'ok') {
      assistantText = await getLastAssistantText({ sinceMs: startedAt });
      void recordRunUpdate({ runId, patch: { status: 'done', completedAtMs: Date.now() } });
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
      void recordRunUpdate({ runId, patch: { status: 'running' } });
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
              const text = await getLastAssistantText({ sinceMs: startedAt });
              const body = text ? `\n${text}` : '';
              await recordRunUpdate({ runId, patch: { status: 'done', completedAtMs: Date.now() } });
              await sendStatusNotification(`✅ Done${runTag}: ${summary}${body}`);
              return;
            }
            if (w?.status && w.status !== 'timeout') {
              await recordRunUpdate({ runId, patch: { status: String(w.status || 'unknown') } });
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

    const responseText =
      assistantText ||
      (wait?.status === 'timeout'
        ? `Still working (run ${shortRun(runId)}). I’ll post progress in Telegram; ask me “check status” if you want the final result read out loud.`
        : 'Done.');

    // Retell will pass the function response back into the LLM; some prompts/tools prefer a `message` field.
    return send(res, 200, {
      ok: true,
      runId,
      status: wait?.status || 'unknown',
      text: responseText,
      message: responseText,
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
