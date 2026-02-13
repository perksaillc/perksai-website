#!/usr/bin/env python3
"""Restaurant pipeline orchestrator.

Runs restaurants in a fixed order, advancing each through steps.
Currently implemented step:
- scrape -> KB files -> Desktop copy

Future steps placeholders:
- retell agent create/update
- moveo pitch deck create/fill

State file:
  /Users/gioalers/clawd/memory/restaurant-orchestrator-state.json

Prints one-line JSON summary.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ORDER = [
    {"slug": "sushi_hana_valrico", "name": "Sushi Hana", "city": "Valrico"},
    {"slug": "sushi_ushi_valrico", "name": "Sushi Ushi", "city": "Valrico"},
    {"slug": "kanji_sushi_ramen_brandon", "name": "Kanji Sushi & Ramen", "city": "Brandon"},
    {"slug": "robongi_valrico", "name": "Robongi Sushi Wok&Grill", "city": "Valrico"},
    {"slug": "sticky_rice_sushi_riverview", "name": "Sticky Rice Sushi", "city": "Riverview"},
]

STATE_PATH = Path("/Users/gioalers/clawd/memory/restaurant-orchestrator-state.json")


def now_ms() -> int:
    return int(time.time() * 1000)


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"version": 1, "restaurants": {}, "lastRunAtMs": None, "lastError": None}


def save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, indent=2, sort_keys=True))


def run_scrape(slug: str) -> dict:
    cfg = Path(f"/Users/gioalers/clawd/tmp/retail_agents/{slug}/restaurant_profile.json")
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
    if p.returncode != 0 and j.get("ok") is True:
        j["ok"] = False
        j["error"] = j.get("error") or (p.stderr or "unknown error").strip()[:200]
    if p.returncode != 0 and not j.get("error"):
        j["error"] = (p.stderr or "unknown error").strip()[:200]
    return j


def main() -> int:
    st = load_state()
    st["lastRunAtMs"] = now_ms()

    # choose next restaurant not completed
    for r in ORDER:
        slug = r["slug"]
        rs = st["restaurants"].get(slug, {"steps": {}, "completed": False})
        if rs.get("completed"):
            continue

        # Step: scrape
        if not rs.get("steps", {}).get("scrape_ok"):
            result = run_scrape(slug)
            rs.setdefault("steps", {})
            rs["steps"]["scrape_last"] = result
            if result.get("ok"):
                rs["steps"]["scrape_ok"] = True
                rs["steps"]["kb_hash"] = result.get("hash")
                rs["lastOkAtMs"] = now_ms()
            else:
                rs["lastErrorAtMs"] = now_ms()
                rs["lastError"] = result.get("error")
                st["restaurants"][slug] = rs
                save_state(st)
                print(json.dumps({"ok": False, "slug": slug, "step": "scrape", "error": result.get("error"), "result": result}))
                return 1

        # For now, mark completed when scrape ok.
        rs["completed"] = True
        st["restaurants"][slug] = rs
        save_state(st)
        print(json.dumps({"ok": True, "slug": slug, "completed": True, "kb_hash": rs.get("steps", {}).get("kb_hash")}))
        return 0

    # all complete
    save_state(st)
    print(json.dumps({"ok": True, "allCompleted": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
