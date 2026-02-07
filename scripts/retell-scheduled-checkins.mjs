#!/usr/bin/env node
/**
 * Retell scheduled check-in calls.
 *
 * Designed to be run frequently (e.g., every 60s) and only place calls
 * at specific daily times in America/New_York.
 *
 * Secrets:
 * - Reads RETELL_API_KEY from /Users/gioalers/clawd/.env.retell (not committed).
 */

import fs from 'fs';
import path from 'path';

const WORKSPACE_DIR = '/Users/gioalers/clawd';
const ENV_PATH = path.join(WORKSPACE_DIR, '.env.retell');
const STATE_PATH = path.join(WORKSPACE_DIR, 'data', 'retell-scheduled-checkins.json');

const TZ = 'America/New_York';

const DEFAULT_FROM_NUMBER = '+14482333096'; // Retell-owned number (verified via /list-phone-numbers)
const DEFAULT_TO_NUMBER = '+18136757606';   // User requested: 813-675-7606

const SLOTS = [
  { key: '15:00', hour: 15, minute: 0 },
  { key: '21:00', hour: 21, minute: 0 },
];

function parseArgs(argv) {
  const args = { dryRun: false, force: false, from: null, to: null, windowMin: 1 };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--dry-run') args.dryRun = true;
    else if (a === '--force') args.force = true;
    else if (a === '--from') args.from = argv[++i];
    else if (a === '--to') args.to = argv[++i];
    else if (a === '--window-min') args.windowMin = Number(argv[++i] ?? '1');
  }
  if (!Number.isFinite(args.windowMin) || args.windowMin < 0 || args.windowMin > 10) args.windowMin = 1;
  return args;
}

function loadDotEnv(filePath) {
  const text = fs.readFileSync(filePath, 'utf8');
  const env = {};
  for (const line of text.split(/\r?\n/)) {
    if (!line || line.startsWith('#')) continue;
    const idx = line.indexOf('=');
    if (idx === -1) continue;
    const k = line.slice(0, idx).trim();
    const v = line.slice(idx + 1).trim();
    if (k) env[k] = v;
  }
  return env;
}

function nowParts(tz) {
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
  const parts = Object.fromEntries(dtf.formatToParts(new Date()).map((p) => [p.type, p.value]));
  const date = `${parts.year}-${parts.month}-${parts.day}`;
  return {
    date,
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    second: Number(parts.second),
    isoLocal: `${date}T${parts.hour}:${parts.minute}:${parts.second}`,
  };
}

function loadState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'));
  } catch {
    return { slots: {} };
  }
}

function saveState(state) {
  fs.mkdirSync(path.dirname(STATE_PATH), { recursive: true });
  fs.writeFileSync(STATE_PATH, JSON.stringify(state, null, 2));
}

function shouldCall({ now, slot, state, windowMin, force }) {
  if (force) return true;
  // Only call during [slot time, slot time + windowMin] minutes.
  const withinWindow =
    now.hour === slot.hour &&
    now.minute >= slot.minute &&
    now.minute <= slot.minute + windowMin;
  if (!withinWindow) return false;

  const lastDate = state?.slots?.[slot.key]?.date;
  if (lastDate === now.date) return false;
  return true;
}

async function createPhoneCall({ apiKey, from_number, to_number }) {
  const resp = await fetch('https://api.retellai.com/v2/create-phone-call', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ from_number, to_number }),
  });

  const text = await resp.text();
  if (!resp.ok) {
    const err = new Error(`Retell create-phone-call failed: HTTP ${resp.status} ${resp.statusText}: ${text.slice(0, 500)}`);
    err.status = resp.status;
    err.body = text;
    throw err;
  }

  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const now = nowParts(TZ);

  const env = loadDotEnv(ENV_PATH);
  const apiKey = env.RETELL_API_KEY;
  if (!apiKey) throw new Error('RETELL_API_KEY missing in .env.retell');

  const from = args.from || env.RETELL_FROM_NUMBER || DEFAULT_FROM_NUMBER;
  const to = args.to || env.RETELL_TO_NUMBER || DEFAULT_TO_NUMBER;

  const state = loadState();

  const dueSlots = SLOTS.filter((slot) => shouldCall({ now, slot, state, windowMin: args.windowMin, force: args.force }));

  if (dueSlots.length === 0) {
    // Intentionally quiet to avoid noisy cron logs.
    return;
  }

  for (const slot of dueSlots) {
    if (args.dryRun) {
      console.log(`[dry-run] would call at slot ${slot.key} (${TZ}) now=${now.isoLocal} from=${from} to=${to}`);
      continue;
    }

    console.log(`placing Retell call for slot ${slot.key} (${TZ}) now=${now.isoLocal} from=${from} to=${to}`);
    const result = await createPhoneCall({ apiKey, from_number: from, to_number: to });

    state.slots = state.slots || {};
    state.slots[slot.key] = {
      date: now.date,
      at: now.isoLocal,
      call_id: result.call_id,
      call_status: result.call_status,
      from_number: result.from_number || from,
      to_number: result.to_number || to,
    };
    saveState(state);

    console.log(`OK slot ${slot.key}: call_id=${result.call_id} status=${result.call_status}`);
  }
}

main().catch((err) => {
  console.error(String(err?.stack || err));
  process.exitCode = 1;
});
