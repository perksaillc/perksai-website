#!/usr/bin/env node
/**
 * Set/merge pronunciation_dictionary entries on a Retell voice agent.
 *
 * Usage:
 *   node scripts/retell_set_pronunciation.mjs --agent-id <agent_id> --entries '<json-array>'
 *
 * Example:
 *   node scripts/retell_set_pronunciation.mjs --agent-id agent_x --entries '[{"word":"Moreno","alphabet":"ipa","phoneme":"məˈreɪnoʊ"}]'
 */

import fs from 'node:fs/promises';
import path from 'node:path';

const WORKSPACE_DIR = process.env.CLAWDBOT_WORKSPACE_DIR || '/Users/gioalers/clawd';
const ENV_PATH = path.join(WORKSPACE_DIR, '.env.retell');

function parseArgs(argv) {
  const out = {};
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--agent-id') out.agentId = argv[++i];
    else if (a === '--entries') out.entries = argv[++i];
    else if (a === '--dry-run') out.dryRun = true;
  }
  return out;
}

async function loadEnvFile(filePath) {
  const raw = await fs.readFile(filePath, 'utf8');
  const env = {};
  for (const line of raw.split(/\r?\n/)) {
    const s = line.trim();
    if (!s || s.startsWith('#')) continue;
    const idx = s.indexOf('=');
    if (idx === -1) continue;
    const key = s.slice(0, idx).trim();
    let val = s.slice(idx + 1).trim();
    val = val.replace(/^"|"$/g, '');
    env[key] = val;
  }
  return env;
}

async function retellJson({ apiKey, method, urlPath, body }) {
  const resp = await fetch(`https://api.retellai.com${urlPath}`, {
    method,
    headers: {
      Authorization: `Bearer ${apiKey}`,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await resp.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    json = { raw: text };
  }
  if (!resp.ok) {
    const err = new Error(`Retell API ${method} ${urlPath} failed: ${resp.status}`);
    err.details = json;
    throw err;
  }
  return json;
}

function normalizeEntry(e) {
  return {
    word: String(e?.word || '').trim(),
    alphabet: String(e?.alphabet || 'ipa').trim(),
    phoneme: String(e?.phoneme || '').trim(),
  };
}

function keyOf(e) {
  return `${e.word.toLowerCase()}|${e.alphabet.toLowerCase()}`;
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.agentId || !args.entries) {
    console.error('Usage: node scripts/retell_set_pronunciation.mjs --agent-id <agent_id> --entries \'<json-array>\' [--dry-run]');
    process.exit(2);
  }

  const env = await loadEnvFile(ENV_PATH);
  const apiKey = env.RETELL_API_KEY || process.env.RETELL_API_KEY;
  if (!apiKey) throw new Error('RETELL_API_KEY missing (.env.retell)');

  let incoming;
  try {
    incoming = JSON.parse(args.entries);
  } catch {
    throw new Error('entries must be valid JSON array');
  }
  if (!Array.isArray(incoming)) throw new Error('entries must be a JSON array');

  const add = incoming.map(normalizeEntry).filter((e) => e.word && e.phoneme);
  if (!add.length) throw new Error('No valid entries after normalization');

  const agent = await retellJson({ apiKey, method: 'GET', urlPath: `/get-agent/${args.agentId}` });
  const existing = Array.isArray(agent?.pronunciation_dictionary) ? agent.pronunciation_dictionary : [];

  const map = new Map(existing.map((e) => [keyOf(normalizeEntry(e)), normalizeEntry(e)]));
  for (const e of add) map.set(keyOf(e), e);
  const merged = Array.from(map.values());

  if (args.dryRun) {
    console.log(JSON.stringify({ ok: true, dryRun: true, agent_id: args.agentId, before: existing.length, after: merged.length, merged }, null, 2));
    return;
  }

  await retellJson({
    apiKey,
    method: 'PATCH',
    urlPath: `/update-agent/${args.agentId}`,
    body: { pronunciation_dictionary: merged },
  });

  const verify = await retellJson({ apiKey, method: 'GET', urlPath: `/get-agent/${args.agentId}` });
  const saved = Array.isArray(verify?.pronunciation_dictionary) ? verify.pronunciation_dictionary : [];

  console.log(JSON.stringify({ ok: true, agent_id: args.agentId, savedCount: saved.length, saved }, null, 2));
}

main().catch((err) => {
  console.error('ERROR', err?.message || err);
  if (err?.details) console.error(JSON.stringify(err.details, null, 2));
  process.exit(1);
});
