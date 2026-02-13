#!/usr/bin/env python3
"""Pipeline runner for Akira (Riverview, FL).

Purpose: keep the local restaurant pipeline outputs fresh and ready for manual KB upload.

Steps:
1) Run scraper (akira_riverview_scrape.py) to regenerate KB HTML/MD.
2) Validate outputs (exist, size, key strings).
3) Copy labeled KB files to Desktop for manual upload.
4) Write/refresh pipeline state JSON.

NOTE: This script does NOT create/publish Retell agents or Moveo pitch decks.
Those are tracked as pending steps in the state file.

Outputs:
- memory/akira-riverview-pipeline-state.json

Prints one-line JSON summary to stdout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

BASE = Path("/Users/gioalers/clawd")
SLUG = "akira_riverview"
OUT_DIR = BASE / "tmp" / "retail_agents" / SLUG
DESKTOP_DIR = Path.home() / "Desktop" / "KB Uploads" / SLUG
STATE_PATH = BASE / "memory" / "akira-riverview-pipeline-state.json"

SCRAPER = BASE / "scripts" / "akira_riverview_scrape.py"

REQ_HTML = OUT_DIR / "knowledge_base_akira_full_latest.html"
REQ_MD = OUT_DIR / "knowledge_base_akira_full_latest.md"
PROMPT_MD = OUT_DIR / "global_prompt_retell_akira.md"
PROFILE_JSON = OUT_DIR / "restaurant_profile.json"
OUTLINE_MD = OUT_DIR / "product_agent_outline_akira.md"


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text("utf-8"))
        except Exception:
            pass
    return {
        "slug": SLUG,
        "createdAt": int(time.time()),
        "steps": {},
        "retell": {"status": "pending", "agentUrl": None},
        "moveo": {"status": "pending", "docUrl": None},
    }


def save_state(st: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    st["updatedAt"] = int(time.time())
    STATE_PATH.write_text(json.dumps(st, indent=2), "utf-8")


def validate_kb(html_path: Path, md_path: Path) -> Dict[str, Any]:
    errs = []
    for p in [html_path, md_path]:
        if not p.exists():
            errs.append(f"missing:{p.name}")
        elif p.stat().st_size < 5_000:
            errs.append(f"too_small:{p.name}:{p.stat().st_size}")

    if html_path.exists():
        html = html_path.read_text("utf-8", errors="ignore")
        for s in ["Akira", "Riverview", "(813)", "Hours", "Menu"]:
            if s not in html:
                errs.append(f"missing_text:{s}")

    return {"ok": len(errs) == 0, "errors": errs}


def copy_to_desktop() -> Dict[str, Any]:
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    copied = []
    for p in [REQ_HTML, REQ_MD]:
        if p.exists():
            dst = DESKTOP_DIR / p.name
            shutil.copy2(p, dst)
            copied.append(str(dst))
    return {"ok": True, "copied": copied, "desktopDir": str(DESKTOP_DIR)}


def main() -> None:
    st = load_state()

    # Step 1: Scrape
    try:
        proc = subprocess.run(
            ["python3", str(SCRAPER)],
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        )
        line = (proc.stdout or "").strip().splitlines()[-1]
        scrape_result = json.loads(line)
        st.setdefault("steps", {})["scrape"] = {
            "ok": bool(scrape_result.get("ok")),
            "hash": scrape_result.get("hash"),
            "ranAt": int(time.time()),
        }
    except Exception as e:
        st.setdefault("steps", {})["scrape"] = {"ok": False, "error": str(e), "ranAt": int(time.time())}
        save_state(st)
        print(json.dumps({"ok": False, "stage": "scrape", "error": str(e)}))
        return

    # Step 2: Validate
    v = validate_kb(REQ_HTML, REQ_MD)
    st["steps"]["validate"] = {**v, "ranAt": int(time.time())}
    if not v["ok"]:
        save_state(st)
        print(json.dumps({"ok": False, "stage": "validate", "errors": v["errors"]}))
        return

    # Step 3: Desktop copy
    c = copy_to_desktop()
    st["steps"]["desktop"] = {**c, "ranAt": int(time.time())}

    # Required files presence
    st["outputs"] = {
        "kbHtml": str(REQ_HTML) if REQ_HTML.exists() else None,
        "kbMd": str(REQ_MD) if REQ_MD.exists() else None,
        "prompt": str(PROMPT_MD) if PROMPT_MD.exists() else None,
        "outline": str(OUTLINE_MD) if OUTLINE_MD.exists() else None,
        "profile": str(PROFILE_JSON) if PROFILE_JSON.exists() else None,
    }

    save_state(st)

    print(
        json.dumps(
            {
                "ok": True,
                "slug": SLUG,
                "hash": st["steps"]["scrape"].get("hash"),
                "kbHtml": str(REQ_HTML),
                "kbMd": str(REQ_MD),
                "desktopDir": str(DESKTOP_DIR),
                "retell": st.get("retell", {}),
                "moveo": st.get("moveo", {}),
            }
        )
    )


if __name__ == "__main__":
    main()
