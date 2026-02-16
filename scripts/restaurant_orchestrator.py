#!/usr/bin/env python3
"""Restaurant pipeline orchestrator.

Goal: keep the restaurant pipeline moving continuously.

Behavior:
- Runs ALL restaurants in ORDER per invocation.
- Each run executes the scrape pipeline (which generates KB files + copies to Desktop)
  for every active restaurant.
- Does NOT permanently "complete" restaurants; it runs forever.

Why:
- If this cron runs every 15 minutes, then each restaurant is refreshed every 15 minutes
  (instead of round-robin where each restaurant would refresh every N*15 minutes).

State file:
  /Users/gioalers/clawd/memory/restaurant-orchestrator-state.json

Outputs a single-line JSON summary.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

WORKFLOW_PATH = Path("/Users/gioalers/clawd/memory/restaurant-workflow-state.json")
STATE_PATH = Path("/Users/gioalers/clawd/memory/restaurant-orchestrator-state.json")


def load_workflow() -> dict:
    if WORKFLOW_PATH.exists():
        try:
            wf = json.loads(WORKFLOW_PATH.read_text())
            if isinstance(wf, dict):
                return wf
        except Exception:
            pass
    return {"activeOrder": [], "restaurants": {}}


def compute_order() -> list[dict]:
    """Compute the active ORDER from restaurant-workflow-state.json.

    Filters:
    - Exclude restaurants marked status=complete or status=skipped.
    - Keep the remaining in activeOrder sequence.

    Returns: list of dicts with at least {slug}.
    """
    wf = load_workflow()
    active = wf.get("activeOrder") or []
    restaurants = wf.get("restaurants") or {}

    order: list[dict] = []
    for slug in active:
        r = restaurants.get(slug, {})
        status = (r.get("status") or "").lower()
        step = (r.get("step") or "").lower()
        if status in {"complete", "skipped"} or step in {"done", "skipped"}:
            continue
        order.append({
            "slug": slug,
            "name": r.get("displayName"),
            "location": r.get("location"),
        })
    return order


def now_ms() -> int:
    return int(time.time() * 1000)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            st = json.loads(STATE_PATH.read_text())
            if isinstance(st, dict):
                return st
        except Exception:
            pass
    return {
        "version": 3,
        "restaurants": {},
        "lastRunAtMs": None,
        "lastError": None,
    }


def save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, indent=2, sort_keys=True))


def run_scrape(slug: str) -> dict:
    cfg = Path(f"/Users/gioalers/clawd/tmp/retail_agents/{slug}/restaurant_profile.json")
    if not cfg.exists():
        return {"ok": True, "skipped": True, "reason": f"missing config: {cfg}"}

    cmd = [
        "python3",
        "/Users/gioalers/clawd/scripts/restaurant_scrape_generic.py",
        "--slug",
        slug,
        "--config",
        str(cfg),
    ]

    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "").strip().splitlines()[-1] if p.stdout else ""

    try:
        j = json.loads(out) if out else {"ok": False, "error": "no output"}
    except Exception:
        j = {"ok": False, "error": f"bad json output: {out[:200]}"}

    if p.returncode != 0:
        j["ok"] = False
        j["error"] = j.get("error") or (p.stderr or "unknown error").strip()[:200]

    if p.returncode != 0 and not j.get("error"):
        j["error"] = (p.stderr or "unknown error").strip()[:200]

    return j


def main() -> int:
    st = load_state()
    st.setdefault("version", 3)
    st.setdefault("restaurants", {})

    st["lastRunAtMs"] = now_ms()
    st["lastError"] = None

    ORDER = compute_order()

    if not ORDER:
        print(json.dumps({"ok": False, "error": "No active restaurants to run (all complete/skipped or activeOrder empty)"}))
        return 1

    refreshed = []
    skipped = []
    errors = []

    for r in ORDER:
        slug = r["slug"]
        rs = st["restaurants"].get(slug, {"steps": {}, "lastOkAtMs": None, "lastErrorAtMs": None})
        rs.setdefault("steps", {})

        result = run_scrape(slug)
        rs["steps"]["scrape_last"] = result

        if result.get("ok") and result.get("skipped"):
            # Not an error: just not configured yet.
            skipped.append({"slug": slug, "reason": result.get("reason")})
        elif result.get("ok"):
            rs["steps"]["scrape_ok"] = True
            rs["steps"]["kb_hash"] = result.get("hash")
            rs["lastOkAtMs"] = now_ms()
            rs.pop("lastError", None)
            refreshed.append({"slug": slug, "kb_hash": result.get("hash")})
        else:
            rs["lastErrorAtMs"] = now_ms()
            rs["lastError"] = result.get("error")
            st["lastError"] = result.get("error")
            errors.append({"slug": slug, "step": "scrape", "error": result.get("error")})

        st["restaurants"][slug] = rs

    save_state(st)

    ok = len(errors) == 0
    print(
        json.dumps(
            {
                "ok": ok,
                "refreshed": refreshed,
                "skipped": skipped,
                "errors": errors,
            }
        )
    )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
