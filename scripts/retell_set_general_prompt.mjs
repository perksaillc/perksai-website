#!/usr/bin/env node
/**
 * Set Retell LLM general_prompt for a voice agent.
 *
 * Usage:
 *   node scripts/retell_set_general_prompt.mjs --agent-id <agent_id> --prompt-file <path>
 *
 * Reads RETELL_API_KEY from /Users/gioalers/clawd/.env.retell (gitignored).
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
    else if (a === '--prompt-file') out.promptFile = argv[++i];
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

function oneLine(s) {
  return String(s ?? '').replace(/[\r\n\t]+/g, ' ').replace(/\s{2,}/g, ' ').trim();
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.agentId || !args.promptFile) {
    console.error('Usage: node scripts/retell_set_general_prompt.mjs --agent-id <agent_id> --prompt-file <path> [--dry-run]');
    process.exit(2);
  }

  const env = await loadEnvFile(ENV_PATH);
  const apiKey = env.RETELL_API_KEY || process.env.RETELL_API_KEY;
  if (!apiKey) throw new Error('RETELL_API_KEY missing (.env.retell)');

  const prompt = (await fs.readFile(args.promptFile, 'utf8')).trim();
  if (!prompt) throw new Error('Prompt file was empty');

  // 1) Get agent to find llm_id
  const agent = await retellJson({ apiKey, method: 'GET', urlPath: `/get-agent/${args.agentId}` });
  const llmId = agent?.response_engine?.llm_id;
  if (!llmId) throw new Error(`No response_engine.llm_id on agent ${args.agentId}`);

  if (args.dryRun) {
    console.log(JSON.stringify({ ok: true, dryRun: true, agent_id: args.agentId, llm_id: llmId, promptChars: prompt.length }, null, 2));
    return;
  }

  // 2) Update LLM general_prompt
  await retellJson({
    apiKey,
    method: 'PATCH',
    urlPath: `/update-retell-llm/${llmId}`,
    body: { general_prompt: prompt },
  });

  // 3) Verify
  const llm = await retellJson({ apiKey, method: 'GET', urlPath: `/get-retell-llm/${llmId}` });
  const saved = (llm?.general_prompt || '').trim();
  const ok = saved.length > 0;

  console.log(
    JSON.stringify(
      {
        ok,
        agent_id: args.agentId,
        llm_id: llmId,
        savedPromptChars: saved.length,
        savedPromptPreview: oneLine(saved).slice(0, 180),
      },
      null,
      2,
    ),
  );
  if (!ok) process.exit(1);
}

main().catch((err) => {
  console.error('ERROR', err?.message || err);
  if (err?.details) console.error(JSON.stringify(err.details, null, 2));
  process.exit(1);
});
