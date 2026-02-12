# Restaurant AI Pipeline — SOP (Scrape → KB → Retell Agent → Moveo Pitch Deck)

## Definition of COMPLETE (Boss)
A restaurant is **COMPLETE** only when ALL are true:
1) Retell AI agent is created and configured (prompt present; tested; ready).
2) Knowledge Base (KB) HTML (and optional MD) is generated and validated.
3) KB files are **clearly labeled** and copied to **Desktop** for manual upload.
4) Moveo.net (Google Docs) pitch deck + notes doc is created and organized using the standard tab structure.

## Standard outputs (per restaurant)
Workspace folder:
- `tmp/retail_agents/<slug>/`

Required files:
- `restaurant_profile.json`
- `global_prompt_retell_<slug>.md`
- `knowledge_base_<slug>_full_latest.html` (primary)
- `knowledge_base_<slug>_full_latest.md` (optional backup)
- `product_agent_outline_<slug>.md` (outline/notes)

Desktop deliverables:
- Create folder: `~/Desktop/KB Uploads/<slug>/`
- Copy in:
  - `knowledge_base_<slug>_full_latest.html`
  - (optional) `knowledge_base_<slug>_full_latest.md`
  - (optional) `kb_addendum_<slug>.html`

## Validation (sanity checks)
KB HTML must:
- exist and be > 5KB
- include restaurant name and city
- include phone number
- include Hours section
- include Menu section headings

Retell agent must:
- load without auth error in clawd browser
- show the correct agent name
- have a **non-empty** global prompt
- have KB attached (manual upload is OK; verify attachment present)

## Moveo Pitch Deck doc standard (tabs)
- Pitch Deck
- Pricing
- Strategy / Ops
- SHARE AND TEST LINK
- Facts (KB)
- Notes / Open Questions

Account default: `giovannie@moveo.net`.

## Automation architecture
### A) Per-restaurant pipeline job (state machine)
A cron job that advances steps and writes a state JSON:
- `memory/<slug>-pipeline-state.json`

Steps:
1. scrape → regenerate KB files
2. validate outputs
3. copy labeled KB files to Desktop
4. ensure Retell agent prompt present (never publish unless told)
5. ensure Moveo doc created + tabs filled
6. final validation summary → mark COMPLETE → disable job

### B) Global Cron Supervisor
A separate cron that monitors ALL jobs for errors/stalls, force-runs once with backoff, and only alerts when action is required.

## Escalation rules
- Telegram alert only if: repeated failure OR human action needed (auth/permissions/UI changed).
- Outbound call only when explicitly requested AND blocked.
