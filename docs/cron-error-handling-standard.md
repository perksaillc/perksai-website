# Cron / Automation Error Handling Standard (Clawdbot)

Goal: **No silent failures.** If a job claims success, it must have verified outputs.

## Principles
1) **Stateful by default**
   - Every automation job must use a state file in `memory/`.
   - State must include: `lastRunAt`, `lastOkAt`, `lastError`, `attemptCount`, and any step-specific checkpoints.

2) **Verify after write** (especially UI automation)
   - If a job writes files: verify existence + size + required strings.
   - If a job edits a web UI (Google Docs/Retell/etc.): verify by reading back content (e.g., text length > threshold) before reporting success.

3) **Retry with backoff**
   - On transient errors: retry 2–3 times with small waits (e.g., 2s → 5s → 15s).
   - Cap retries per run; persist next retry time in state.

4) **Fail loudly with context**
   - If still failing after retries, send ONE concise alert including:
     - job name
     - step that failed
     - error class (timeout/auth/ui-change/parse)
     - what was attempted
     - what you need from the human (if anything)

5) **Never lie about completion**
   - Only say COMPLETE when verification passed.

## Required state fields (minimum)
```json
{
  "version": 1,
  "lastRunAt": 0,
  "lastOkAt": 0,
  "attemptCount": 0,
  "lastError": {"at": 0, "step": "", "message": ""},
  "checkpoints": {}
}
```

## UI automation (Google Docs/Retell)
Recommended pattern per tab/section:
- Select target tab
- Write content
- Read back content length
- If length < threshold: refresh + retry

## Cron Supervisor expectations
- Detect stalls/errors
- Force-run once with cooldown
- If repeated failures: alert with summarized failure
- If auth/UI blocker: alert immediately and stop spamming retries
