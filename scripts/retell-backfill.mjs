import fs from 'node:fs/promises';
import path from 'node:path';

const API_KEY = process.env.RETELL_API_KEY;
const WORKSPACE_DIR = process.env.CLAWDBOT_WORKSPACE_DIR || process.cwd();
const MEMORY_DIR = path.join(WORKSPACE_DIR, 'memory');

// Required by default: scope backfill to a single agent (avoid importing other agents' calls)
const AGENT_ID = process.env.RETELL_AGENT_ID || null;
const ALLOW_ALL = process.env.RETELL_ALLOW_ALL === 'true';

if (!API_KEY) {
  console.error('Missing RETELL_API_KEY in env. Add it to .env.retell (gitignored).');
  process.exit(1);
}

if (!AGENT_ID && !ALLOW_ALL) {
  console.error('Missing RETELL_AGENT_ID. Refusing to backfill across ALL agents. Set RETELL_AGENT_ID, or set RETELL_ALLOW_ALL=true if you really want everything.');
  process.exit(1);
}

function nyDateStampFromMs(ms) {
  const d = new Date(ms);
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(d);
}

async function appendDaily(dateStamp, text) {
  await fs.mkdir(MEMORY_DIR, { recursive: true });
  const file = path.join(MEMORY_DIR, `${dateStamp}.md`);
  await fs.appendFile(file, text, 'utf8');
}

function joinTranscriptObject(call) {
  const turns = call?.transcript_object;
  if (!Array.isArray(turns) || !turns.length) return '';
  return turns
    .map((t) => {
      const role = t.role || t.speaker || '';
      const content = t.content || t.text || '';
      const line = `${role ? role + ': ' : ''}${content}`.trim();
      return line;
    })
    .filter(Boolean)
    .join('\n');
}

async function retellListCalls({ paginationKey = undefined, lower = undefined, upper = undefined, limit = 200 }) {
  const url = 'https://api.retellai.com/v2/list-calls';

  const filter_criteria = {};
  if (AGENT_ID) filter_criteria.agent_id = [AGENT_ID];
  if (lower || upper) {
    filter_criteria.start_timestamp = {};
    if (typeof lower === 'number') filter_criteria.start_timestamp.lower_threshold = lower;
    if (typeof upper === 'number') filter_criteria.start_timestamp.upper_threshold = upper;
  }

  const body = {
    filter_criteria,
    sort_order: 'descending',
    limit,
    ...(paginationKey ? { pagination_key: paginationKey } : {}),
  };

  const res = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const t = await res.text().catch(() => '');
    throw new Error(`Retell list-calls failed: ${res.status} ${res.statusText}\n${t}`);
  }

  return res.json();
}

async function main() {
  const now = Date.now();
  const days = Number(process.env.RETELL_BACKFILL_DAYS || 30);
  const lower = now - days * 24 * 60 * 60 * 1000;
  const upper = now;

  await fs.mkdir(path.join(MEMORY_DIR, 'retell-calls'), { recursive: true });

  let paginationKey = undefined;
  let total = 0;

  for (let page = 0; page < 20; page++) {
    const calls = await retellListCalls({ paginationKey, lower, upper, limit: 200 });
    if (!Array.isArray(calls) || calls.length === 0) break;

    for (const call of calls) {
      const callId = call.call_id;
      if (!callId) continue;

      const start = call.start_timestamp;
      const stamp = typeof start === 'number' ? nyDateStampFromMs(start) : nyDateStampFromMs(now);

      const rawPath = path.join(MEMORY_DIR, 'retell-calls', `${callId}.json`);
      const transcriptPath = path.join(MEMORY_DIR, 'retell-calls', `${callId}.transcript.txt`);

      // Write/refresh per-call JSON
      await fs.writeFile(rawPath, JSON.stringify(call, null, 2), 'utf8');

      // Best-effort transcript
      const transcript =
        (typeof call.transcript === 'string' && call.transcript.trim())
          ? call.transcript.trim()
          : joinTranscriptObject(call);

      await fs.writeFile(transcriptPath, (transcript || '(no transcript)') + '\n', 'utf8');

      // Append a compact index entry into daily memory
      const header = `\n\n---\n[retell api backfill] ${new Date().toISOString()}\nCall ID: ${callId}\n`;
      const metaBits = [];
      if (call.call_type) metaBits.push(`Type: ${call.call_type}`);
      if (call.direction) metaBits.push(`Direction: ${call.direction}`);
      if (call.call_status) metaBits.push(`Status: ${call.call_status}`);
      if (call.agent_name) metaBits.push(`Agent: ${call.agent_name}`);
      if (call.agent_id) metaBits.push(`Agent ID: ${call.agent_id}`);
      if (typeof call.start_timestamp === 'number') metaBits.push(`Start: ${new Date(call.start_timestamp).toISOString()}`);
      if (typeof call.end_timestamp === 'number') metaBits.push(`End: ${new Date(call.end_timestamp).toISOString()}`);

      const text =
        header +
        (metaBits.length ? metaBits.join('\n') + '\n\n' : '\n') +
        `Transcript:\n${transcript || '(no transcript)'}\n`;

      await appendDaily(stamp, text);
      total++;
    }

    paginationKey = calls[calls.length - 1]?.call_id;
    if (!paginationKey) break;
    if (calls.length < 200) break;
  }

  console.log(`Backfill complete. Calls processed: ${total}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
