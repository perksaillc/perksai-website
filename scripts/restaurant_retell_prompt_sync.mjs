#!/usr/bin/env node
/**
 * Bulk-audit and sync Retell general prompts for restaurant agents.
 *
 * Goal: make all restaurant agents match the Sunflower/Hokkaido quality bar:
 * - clear concierge role
 * - KB-first rules
 * - explicit timezone/current_time variable usage
 * - strong open-now behavior
 * - ordering guardrails
 *
 * Reads:
 * - /Users/gioalers/clawd/memory/restaurant-workflow-state.json
 * - /Users/gioalers/clawd/tmp/retail_agents/<slug>/restaurant_profile.json
 * - RETELL_API_KEY from /Users/gioalers/clawd/.env.retell
 *
 * Usage:
 *   node scripts/restaurant_retell_prompt_sync.mjs --dry-run
 *   node scripts/restaurant_retell_prompt_sync.mjs --apply
 */

import fs from 'node:fs/promises';
import path from 'node:path';

const WORKSPACE_DIR = '/Users/gioalers/clawd';
const ENV_PATH = path.join(WORKSPACE_DIR, '.env.retell');
const STATE_PATH = path.join(WORKSPACE_DIR, 'memory', 'restaurant-workflow-state.json');
const RETAIL_AGENTS_DIR = path.join(WORKSPACE_DIR, 'tmp', 'retail_agents');

const TZ = 'America/New_York';
const CURRENT_TIME_VAR = `{{current_time_${TZ}}}`;

const EXCLUDE_AGENT_IDS = new Set([
  // Keep the user's gold-standard prompts untouched unless explicitly asked.
  'agent_304129f863e0b5394fd9b73ee7', // Sunflower Cafe Inc (KAI)
  // Hokkaido agent id not in this workflow state, but we exclude by slug elsewhere too.
]);

const EXCLUDE_SLUGS = new Set([
  'sunflower_cafe',
  'hokkaido_lithia',
]);

function parseArgs(argv) {
  const out = { dryRun: false, apply: false, only: null };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--dry-run') out.dryRun = true;
    else if (a === '--apply') out.apply = true;
    else if (a === '--only') out.only = argv[++i];
  }
  if (!out.dryRun && !out.apply) out.dryRun = true;
  return out;
}

async function loadDotEnv(filePath) {
  const raw = await fs.readFile(filePath, 'utf8');
  const env = {};
  for (const line of raw.split(/\r?\n/)) {
    const s = line.trim();
    if (!s || s.startsWith('#')) continue;
    const idx = s.indexOf('=');
    if (idx === -1) continue;
    const k = s.slice(0, idx).trim();
    const v = s.slice(idx + 1).trim().replace(/^"|"$/g, '');
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

function normalizeSpace(s) {
  return String(s ?? '').replace(/\r\n/g, '\n').trim();
}

function renderPrompt({ name, address, phone, website, menuUrl, orderUrl, hours }) {
  const lines = [];
  lines.push(`# Retell AI — Global Prompt (${name} — ${address?.includes('FL') ? 'FL' : 'Florida'})`);
  lines.push('');
  lines.push(`You are **${name} Concierge**, the friendly, efficient customer assistant for:`);
  lines.push(`**${name}**`);
  if (address) lines.push(`**${address}**`);
  if (phone) lines.push(`Phone: **${phone}**`);
  if (website) lines.push(`Website: ${website}`);
  if (orderUrl) lines.push(`Online ordering (if available): ${orderUrl}`);
  if (menuUrl && menuUrl !== website) lines.push(`Menu: ${menuUrl}`);
  lines.push('');

  lines.push('## Mission');
  lines.push('Help guests quickly with:');
  lines.push('1) **Hours** (today’s hours, “are you open now?”, next opening time)');
  lines.push('2) **Menu questions** (prices, what’s in an item, raw vs cooked, spicy level, fried vs not, recommendations)');
  lines.push('3) **Location & contact** (address, phone, links)');
  lines.push('4) **General guidance** (ordering options, catering/custom orders if mentioned in KB)');
  lines.push('');

  lines.push('## Knowledge-base first (source of truth)');
  lines.push('- Treat the attached **Knowledge Base (KB)** as the source of truth for hours, contact info, menu items, and prices.');
  lines.push('- **Do not guess** prices, ingredients, hours, or policies.');
  lines.push('- If something isn’t in the KB, say you don’t have it in front of you and offer the **phone number** (and ordering link if available).');
  lines.push('');

  lines.push('## Ordering & payments');
  lines.push('- You do **not** take payment or finalize orders.');
  lines.push('- If the guest wants to place an order: say “Ordering through chat is coming soon.”');
  if (orderUrl) lines.push(`- Then provide: **${orderUrl}**`);
  if (phone) lines.push(`- And provide: **${phone}**`);
  lines.push('- Never request or store card numbers.');
  lines.push('');

  lines.push('## Allergy / dietary safety (important)');
  lines.push('- If allergies are mentioned, always say:');
  lines.push('  - “For allergy safety, please confirm ingredients and cross-contact with the restaurant.”');
  lines.push('');

  lines.push('## Time / date (Retell system variables)');
  lines.push(`- Retell provides the current time via: **${CURRENT_TIME_VAR}**.`);
  lines.push(`- Use timezone: **${TZ}**.`);
  lines.push('- If this variable is missing/unresolved, do **not** claim the restaurant is open “right now.”');
  lines.push('');

  if (hours) {
    lines.push('## Hours (from KB / profile)');
    lines.push(hours.trim());
    lines.push('');
  }

  lines.push('## Open-now / open-today rules (non-negotiable)');
  lines.push('When the caller asks: “Are you open?” / “Open now?” / “Are you open today?” / “What time do you close?”');
  lines.push('');
  lines.push('Do this:');
  lines.push(`1) Read **${CURRENT_TIME_VAR}**.`);
  lines.push('2) Determine the current day + time.');
  lines.push('3) Compare strictly against the **Hours** in the KB (respect any midday breaks if listed).');
  lines.push('');
  lines.push('Response templates (use exactly ONE):');
  lines.push('- OPEN: “Yes — we’re open right now. Today’s hours are <TODAY HOURS>. How can I help you?”');
  lines.push('- CLOSED: “No — we’re closed right now. Today’s hours are <TODAY HOURS>. Next opening is <NEXT OPEN DAY> at <NEXT OPEN TIME>. How can I help you?”');
  lines.push('');
  lines.push('Rules:');
  lines.push('- Never say “open right now” unless you can verify the current time variable and it is within an open window.');
  lines.push('- If you can’t verify the current time variable: say you can’t confirm “open right now,” then provide today’s hours + the phone number.');
  lines.push('- Never invent holiday/exception hours.');
  lines.push('');

  lines.push('## Conversation style');
  lines.push('- **Answer first**, then ask **one** helpful follow-up question.');
  lines.push('- Keep responses short, warm, confident.');
  lines.push('- Ask only one clarifying question at a time.');
  lines.push('');

  lines.push('## Never do');
  lines.push('- Don’t invent items, prices, hours, discounts, delivery availability, or reservation policies.');
  lines.push('- Don’t claim to be a human staff member.');
  lines.push('');

  lines.push('## Closing');
  lines.push('If the guest seems done, close politely and provide the phone number if relevant.');

  return lines.join('\n');
}

async function loadJson(p) {
  const raw = await fs.readFile(p, 'utf8');
  return JSON.parse(raw);
}

async function main() {
  const args = parseArgs(process.argv);
  const env = await loadDotEnv(ENV_PATH);
  const apiKey = env.RETELL_API_KEY || process.env.RETELL_API_KEY;
  if (!apiKey) throw new Error('RETELL_API_KEY missing (.env.retell)');

  const st = await loadJson(STATE_PATH);
  const restaurants = st?.restaurants || {};

  const targets = [];
  for (const [slug, r] of Object.entries(restaurants)) {
    if (EXCLUDE_SLUGS.has(slug)) continue;
    const agentId = r?.retell?.agentId;
    if (!agentId) continue;
    if (EXCLUDE_AGENT_IDS.has(agentId)) continue;
    if (args.only && slug !== args.only && agentId !== args.only) continue;
    targets.push({ slug, agentId });
  }

  const results = [];

  for (const t of targets) {
    const slug = t.slug;
    const agentId = t.agentId;

    const profilePath = path.join(RETAIL_AGENTS_DIR, slug, 'restaurant_profile.json');
    let profile = null;
    try {
      profile = await loadJson(profilePath);
    } catch {
      // ok
    }

    let agent;
    try {
      agent = await retellJson({ apiKey, method: 'GET', urlPath: `/get-agent/${agentId}` });
    } catch (err) {
      results.push({ slug, agentId, ok: false, error: 'get-agent failed', details: err?.details || String(err?.message || err) });
      continue;
    }

    const llmId = agent?.response_engine?.llm_id;
    if (!llmId) {
      results.push({ slug, agentId, ok: false, error: 'missing llm_id' });
      continue;
    }

    const llm = await retellJson({ apiKey, method: 'GET', urlPath: `/get-retell-llm/${llmId}` });
    const currentPrompt = normalizeSpace(llm?.general_prompt || '');

    const prompt = renderPrompt({
      name: profile?.name || rNameFromState(restaurants?.[slug]) || slug,
      address: profile?.address || restaurants?.[slug]?.address || restaurants?.[slug]?.location || '',
      phone: profile?.phone || '',
      website: profile?.website || '',
      menuUrl: profile?.menu_url || profile?.website || '',
      orderUrl: profile?.order_url || null,
      hours: profile?.hours || null,
    });

    const needsUpdate = currentPrompt.length < 200 || !currentPrompt.includes(CURRENT_TIME_VAR) || currentPrompt.includes('Type in a universal prompt');

    if (args.dryRun) {
      results.push({ slug, agentId, llmId, needsUpdate, currentPromptChars: currentPrompt.length, nextPromptChars: prompt.length });
      continue;
    }

    if (!needsUpdate) {
      results.push({ slug, agentId, llmId, updated: false, reason: 'already_ok', currentPromptChars: currentPrompt.length });
      continue;
    }

    await retellJson({ apiKey, method: 'PATCH', urlPath: `/update-retell-llm/${llmId}`, body: { general_prompt: prompt } });
    const verify = await retellJson({ apiKey, method: 'GET', urlPath: `/get-retell-llm/${llmId}` });
    const saved = normalizeSpace(verify?.general_prompt || '');

    results.push({ slug, agentId, llmId, updated: true, savedPromptChars: saved.length });
  }

  console.log(JSON.stringify({ ok: true, mode: args.dryRun ? 'dry-run' : 'apply', count: targets.length, results }, null, 2));
}

function rNameFromState(r) {
  if (!r) return '';
  return r.displayName || r.name || r.retell?.agentName || '';
}

main().catch((err) => {
  console.error('ERROR', err?.message || err);
  if (err?.details) console.error(JSON.stringify(err.details, null, 2));
  process.exit(1);
});
