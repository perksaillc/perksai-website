#!/usr/bin/env python3
"""End-to-end restaurant workflow runner.

Runs ONE small step per invocation so it can be scheduled every 15 minutes.
This is intentionally conservative and avoids heavy/fragile automation.

Current implementation:
- Ensures KB files exist on Desktop for current restaurant (one-time).
- Reports what to do next (Retell + Google Doc steps are interactive and should
  be driven by the main agent session/browser).

State:
  /Users/gioalers/clawd/memory/restaurant-workflow-state.json

This script prints a single-line JSON summary:
  { ok, slug, step, did, next }
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

STATE_PATH = Path("/Users/gioalers/clawd/memory/restaurant-workflow-state.json")
DESKTOP_BASE = Path.home() / "Desktop" / "KB Uploads"
TMP_BASE = Path("/Users/gioalers/clawd/tmp/retail_agents")


def now_ms() -> int:
    return int(time.time() * 1000)


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    raise SystemExit("missing state file: restaurant-workflow-state.json")


def save_state(st: dict) -> None:
    st.setdefault("version", 1)
    st.setdefault("restaurants", {})
    st.setdefault("current", {})
    st["current"]["updatedAtMs"] = now_ms()
    STATE_PATH.write_text(json.dumps(st, indent=2, sort_keys=True))


def ensure_desktop_kb(slug: str) -> dict:
    src_dir = TMP_BASE / slug
    html = src_dir / f"knowledge_base_{slug}_full_latest.html"
    md = src_dir / f"knowledge_base_{slug}_full_latest.md"

    if not html.exists() or not md.exists():
        return {"ok": False, "error": "missing kb source files", "src": str(src_dir)}

    dst_dir = DESKTOP_BASE / slug
    dst_dir.mkdir(parents=True, exist_ok=True)

    dst_html = dst_dir / html.name
    dst_md = dst_dir / md.name

    shutil.copy2(html, dst_html)
    shutil.copy2(md, dst_md)

    return {
        "ok": True,
        "desktopDir": str(dst_dir),
        "html": str(dst_html),
        "md": str(dst_md),
    }


def main() -> int:
    st = load_state()
    cur = st.get("current") or {}
    slug = cur.get("slug")
    step = cur.get("step")

    if not slug:
        print(json.dumps({"ok": False, "error": "state.current.slug missing"}))
        return 1

    restaurants = st.setdefault("restaurants", {})
    rs = restaurants.setdefault(slug, {"status": "in_progress", "step": step or "kb_desktop"})

    # Step 1: KB to Desktop (one-time per restaurant)
    if rs.get("step") in (None, "kb_desktop"):
        res = ensure_desktop_kb(slug)
        if not res.get("ok"):
            print(json.dumps({"ok": False, "slug": slug, "step": "kb_desktop", "error": res.get("error"), "detail": res}))
            return 1

        rs["step"] = "retell_agent"
        rs["kbDesktop"] = res
        restaurants[slug] = rs
        st["current"]["step"] = "retell_agent"
        save_state(st)

        print(json.dumps({"ok": True, "slug": slug, "step": "kb_desktop", "did": "copied KB files to Desktop", "next": "retell_agent"}))
        return 0

    # Remaining steps are interactive/browser-driven.
    # We just emit a reminder so the cron can post progress.
    print(json.dumps({"ok": True, "slug": slug, "step": rs.get("step"), "did": "no-op (interactive step)", "next": rs.get("step")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
