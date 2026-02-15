#!/usr/bin/env python3
"""Reminder/monitor for the end-to-end restaurant workflow.

This does NOT do browser automation. It simply reads a workflow state file and
prints a single-line status summary suitable for a cron message.

State file:
  /Users/gioalers/clawd/memory/restaurant-workflow-state.json

Expected state shape:
{
  "version": 1,
  "activeOrder": ["sushi_hana_valrico", ...],
  "current": {
    "slug": "sushi_hana_valrico",
    "step": "retell_agent"|"moveo_doc"|"done",
    "updatedAtMs": 123
  },
  "restaurants": {
    "sushi_hana_valrico": {"status":"in_progress"|"done", "step":"...", "updatedAtMs":123}
  }
}

If state is missing, prints a hint.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

STATE_PATH = Path("/Users/gioalers/clawd/memory/restaurant-workflow-state.json")


def now_ms() -> int:
    return int(time.time() * 1000)


def main() -> int:
    if not STATE_PATH.exists():
        print("Restaurant workflow: no state file yet (restaurant-workflow-state.json).")
        return 0

    try:
        st = json.loads(STATE_PATH.read_text())
    except Exception as e:
        print(f"Restaurant workflow: state unreadable: {e}")
        return 1

    cur = (st.get("current") or {})
    slug = cur.get("slug")
    step = cur.get("step")
    upd = cur.get("updatedAtMs")

    if not slug:
        print("Restaurant workflow: state has no current.slug")
        return 0

    age_min = None
    if isinstance(upd, int):
        age_min = max(0, int((now_ms() - upd) / 60000))

    age_txt = f" (~{age_min}m ago)" if age_min is not None else ""

    if step == "done":
        print(f"Restaurant workflow: {slug} marked done{age_txt}. Next restaurant should be started.")
        return 0

    print(f"Restaurant workflow: continue {slug} at step={step}{age_txt}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
