#!/usr/bin/env node
/**
 * Create a Retell "single prompt" agent (retell-llm) via API.
 *
 * Why: The Retell UI sometimes creates a Conversation Flow agent by default.
 * This script guarantees we create a retell-llm agent with a general_prompt.
 *
 * Usage:
 *   node scripts/retell_create_single_prompt_agent.mjs \
 *     --agent-name "Cali Cafe (Riverview, FL)" \
 *     --prompt-file /path/to/prompt.md \
 *     --voice-id minimax-Cimo \
 *     --language en-US
 *
 * Reads RETELL_API_KEY from /Users/gioalers/clawd/.env.retell (gitignored).
 */

import fs from 'node:fs/promises';
import path from 'node:path';

const WORKSPACE_DIR = process.env.CLAWDBOT_WORKSPACE_DIR || '/Users/gioalers/clawd';
const ENV_PATH = path.join(WORKSPACE_DIR, '.env.retell');

function parseArgs(argv) {
  const out = { language: 'en-US', model: 'gpt-4o-mini', startSpeaker: 'agent' };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--agent-name') out.agentName = argv[++i];
    else if (a === '--prompt-file') out.promptFile = argv[++i];
    else if (a === '--voice-id') out.voiceId = argv[++i];
    else if (a === '--language') out.language = argv[++i];
    else if (a === '--model') out.model = argv[++i];
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
    const k = s.slice(0, idx).trim();
    let v = s.slice(idx + 1).trim();
    v = v.replace(/^"|"$/g, '');
    env[k] = v;
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

async function main() {
  const args = parseArgs(process.argv);
  if (!args.agentName || !args.promptFile || !args.voiceId) {
    console.error(
      'Usage: node scripts/retell_create_single_prompt_agent.mjs --agent-name <name> --prompt-file <path> --voice-id <voice> [--language en-US] [--model gpt-4o-mini] [--dry-run]',
    );
    process.exit(2);
  }

  const env = await loadEnvFile(ENV_PATH);
  const apiKey = env.RETELL_API_KEY || process.env.RETELL_API_KEY;
  if (!apiKey) throw new Error('RETELL_API_KEY missing (.env.retell)');

  const prompt = (await fs.readFile(args.promptFile, 'utf8')).trim();
  if (!prompt) throw new Error('Prompt file was empty');

  if (args.dryRun) {
    console.log(
      JSON.stringify(
        {
          ok: true,
          dryRun: true,
          agent_name: args.agentName,
          voice_id: args.voiceId,
          language: args.language,
          model: args.model,
          promptChars: prompt.length,
        },
        null,
        2,
      ),
    );
    return;
  }

  // 1) Create LLM
  const llm = await retellJson({
    apiKey,
    method: 'POST',
    urlPath: '/create-retell-llm',
    body: { model: args.model, general_prompt: prompt, start_speaker: args.startSpeaker },
  });

  // 2) Create agent (retell-llm)
  const agent = await retellJson({
    apiKey,
    method: 'POST',
    urlPath: '/create-agent',
    body: {
      agent_name: args.agentName,
      voice_id: args.voiceId,
      language: args.language,
      response_engine: { type: 'retell-llm', llm_id: llm.llm_id },
    },
  });

  console.log(
    JSON.stringify(
      {
        ok: true,
        agent_id: agent.agent_id,
        agent_name: args.agentName,
        llm_id: llm.llm_id,
      },
      null,
      2,
    ),
  );
}

main().catch((err) => {
  console.error('ERROR', err?.message || err);
  if (err?.details) console.error(JSON.stringify(err.details, null, 2));
  process.exit(1);
});
