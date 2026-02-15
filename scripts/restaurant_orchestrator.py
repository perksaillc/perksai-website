#!/usr/bin/env python3
"""Restaurant pipeline orchestrator.

Goal: keep the restaurant pipeline moving continuously.

Behavior:
- Runs ONE restaurant per invocation in a fixed round-robin order.
- Each run executes the scrape pipeline (which generates KB files + copies to Desktop).
- Does NOT permanently "complete" restaurants; it cycles forever.

State file:
  /Users/gioalers/clawd/memory/restaurant-orchestrator-state.json

Outputs a single-line JSON summary.
"""

from __future__ import annotations

import json
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
        try:
            st = json.loads(STATE_PATH.read_text())
            if isinstance(st, dict):
                return st
        except Exception:
            pass
    return {
        "version": 2,
        "cursor": 0,
        "restaurants": {},
        "lastRunAtMs": None,
        "lastError": None,
    }


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

    if p.returncode != 0:
        j["ok"] = False
        j["error"] = j.get("error") or (p.stderr or "unknown error").strip()[:200]

    if p.returncode != 0 and not j.get("error"):
        j["error"] = (p.stderr or "unknown error").strip()[:200]

    return j


def main() -> int:
    st = load_state()
    st.setdefault("version", 2)
    st.setdefault("cursor", 0)
    st.setdefault("restaurants", {})

    st["lastRunAtMs"] = now_ms()

    if not ORDER:
        print(json.dumps({"ok": False, "error": "ORDER is empty"}))
        return 1

    cursor = int(st.get("cursor") or 0) % len(ORDER)
    r = ORDER[cursor]
    slug = r["slug"]

    # advance cursor for next run (round-robin)
    st["cursor"] = (cursor + 1) % len(ORDER)

    rs = st["restaurants"].get(slug, {"steps": {}, "lastOkAtMs": None, "lastErrorAtMs": None})
    rs.setdefault("steps", {})

    result = run_scrape(slug)
    rs["steps"]["scrape_last"] = result

    if result.get("ok"):
        rs["steps"]["scrape_ok"] = True
        rs["steps"]["kb_hash"] = result.get("hash")
        rs["lastOkAtMs"] = now_ms()
        rs.pop("lastError", None)
    else:
        rs["lastErrorAtMs"] = now_ms()
        rs["lastError"] = result.get("error")
        st["lastError"] = result.get("error")

    st["restaurants"][slug] = rs
    save_state(st)

    if result.get("ok"):
        print(
            json.dumps(
                {
                    "ok": True,
                    "slug": slug,
                    "kb_hash": rs.get("steps", {}).get("kb_hash"),
                    "cursor": st.get("cursor"),
                }
            )
        )
        return 0

    print(json.dumps({"ok": False, "slug": slug, "step": "scrape", "error": result.get("error"), "cursor": st.get("cursor"), "result": result}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
