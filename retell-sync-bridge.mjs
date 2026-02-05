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
      if (RETELL_AGENT_ID && agentId && agentId !== RETELL_AGENT_ID) {
        return;
      }

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

    const deliver = args.deliver ?? false; // For sync tool calls, prefer returning text; Telegram delivery is handled separately.
    const thinking = args.thinking || 'low';

    const idempotencyKey = randomUUID();

    // Kick off the agent.
    const agentAck = await gw.client.request('agent', {
      sessionKey: SESSION_KEY,
      message,
      thinking,
      deliver,
      idempotencyKey,
    });

    const runId = agentAck?.runId || idempotencyKey;

    // Wait for completion (cap to keep voice UX snappy).
    // Retell tool calls should return quickly; long-running work can complete asynchronously.
    const timeoutMs = Math.min(12_000, Math.max(1_000, Number(args.timeoutMs || 8_000)));

    const wait = await Promise.race([
      gw.client.request('agent.wait', { runId, timeoutMs }),
      new Promise((resolve) => setTimeout(() => resolve({ runId, status: 'timeout' }), timeoutMs + 250)),
    ]);

    let assistantText = '';
    if (wait?.status === 'ok') {
      assistantText = await getLastAssistantText();
    }

    // Log tool usage to daily memory for continuity.
    appendToDailyMemory(`\n\n---\n[retell tool] ${new Date().toISOString()}\nMessage:\n${message}\nStatus: ${wait?.status || 'unknown'}\nRunId: ${runId}\n`).catch(() => {});

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
