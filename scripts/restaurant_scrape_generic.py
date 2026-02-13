#!/usr/bin/env python3
"""Generic restaurant scraper -> KB markdown + HTML.

Used by restaurant pipeline cron jobs.

Inputs:
  --slug <slug>
  --config <path-to-restaurant_profile.json>

Outputs (in tmp/retail_agents/<slug>/):
  knowledge_base_<slug>_full_latest.md
  knowledge_base_<slug>_full_latest.html

Also copies latest HTML+MD to:
  ~/Desktop/KB Uploads/<slug>/

Prints one-line JSON:
  {ok, slug, hash, outputPathLatestMd, outputPathLatestHtml, address, phone, hours}

Error handling:
- Never throws a raw stack trace to stdout.
- Exits non-zero on failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import urllib.request
import urllib.error


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())


class _Stripper:
    """Very small HTML-to-text stripper (no external deps)."""

    def __init__(self) -> None:
        self._chunks: list[str] = []
        self._in_skip = False

    def feed(self, html: str) -> None:
        # remove script/style/noscript blocks first
        html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
        html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
        html = re.sub(r"<noscript[\s\S]*?</noscript>", " ", html, flags=re.I)
        # replace breaks/blocks with newlines
        html = re.sub(r"<(br|br/|/p|/div|/li|/tr|/h\d)>", "\n", html, flags=re.I)
        # drop tags
        text = re.sub(r"<[^>]+>", " ", html)
        # unescape minimal entities
        text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        self._chunks.append(text)

    def text(self) -> str:
        out = "\n".join(self._chunks)
        out = re.sub(r"[\t\r]+", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        out = re.sub(r" {2,}", " ", out)
        return out.strip()


def _safe_text(html: str) -> str:
    s = _Stripper()
    s.feed(html)
    return s.text()


def fetch(url: str, timeout: int = 25) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # best-effort decode
            try:
                return raw.decode("utf-8")
            except Exception:
                return raw.decode("latin-1", errors="ignore")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {url}")


def extract_phone(text: str) -> str | None:
    # US phone patterns
    m = re.search(r"\(?\b(\d{3})\)?[\s\-\.]?(\d{3})[\s\-\.]?(\d{4})\b", text)
    if not m:
        return None
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}"


def extract_menu_price_lines(text: str, limit: int = 120) -> list[str]:
    # lines like "Edamame$6.00" or "Pork Gyoza$7.00(Fried...)"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    hits = []
    for ln in lines:
        if re.search(r"\$\s*\d+(?:\.\d{2})?", ln) or re.search(r"\b\d+\.\d{2}\b", ln):
            # filter obvious totals/tips
            if re.search(r"\bTotal\b|Promo|Tip|Payment|GIFT CARD", ln, re.I):
                continue
            hits.append(ln)
        if len(hits) >= limit:
            break
    # de-dupe while preserving order
    out = []
    seen = set()
    for h in hits:
        if h in seen:
            continue
        seen.add(h)
        out.append(h)
    return out


def md_to_html(md: str) -> str:
    # minimal HTML wrapper (no external deps)
    esc = md
    esc = esc.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    esc = esc.replace("\n", "<br>\n")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Knowledge Base</title>"
        "<style>body{font-family:Arial,system-ui,-apple-system;line-height:1.35;margin:24px;max-width:900px}"
        "code,pre{background:#f6f8fa;padding:2px 4px;border-radius:4px}"
        "h1,h2,h3{margin-top:20px}</style>"
        "</head><body>"
        f"<div>{esc}</div>"
        "</body></html>"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    slug = args.slug
    config_path = Path(args.config)
    base_dir = Path(f"/Users/gioalers/clawd/tmp/retail_agents/{slug}")
    base_dir.mkdir(parents=True, exist_ok=True)

    try:
        cfg = json.loads(config_path.read_text())
        name = cfg.get("name", slug)
        website = cfg.get("website")
        menu_url = cfg.get("menu_url")
        order_url = cfg.get("order_url")
        address = cfg.get("address")
        hours = cfg.get("hours")
        phone = cfg.get("phone")

        sources = []
        text_blobs = []

        if website:
            html = fetch(website)
            sources.append(website)
            text_blobs.append(_safe_text(html))

        if menu_url and menu_url != website:
            html = fetch(menu_url)
            sources.append(menu_url)
            text_blobs.append(_safe_text(html))

        menu_price_lines = []
        if order_url:
            html = fetch(order_url)
            sources.append(order_url)
            text_blobs.append(_safe_text(html))
            menu_price_lines = extract_menu_price_lines(text_blobs[-1], limit=140)

        joined = "\n\n".join(text_blobs)

        # backfill phone/address/hours if missing
        phone = phone or extract_phone(joined)
        # address/hours are often best kept from config (less error-prone)

        md = []
        md.append(f"{name} — Knowledge Base")
        md.append("")
        md.append(f"Last updated: {_now_iso()}")
        md.append("")
        md.append("## Official links")
        if website:
            md.append(f"- Website: {website}")
        if menu_url:
            md.append(f"- Menu: {menu_url}")
        if order_url:
            md.append(f"- Online ordering: {order_url}")
        md.append("")

        md.append("## Contact")
        if address:
            md.append(f"- Address: {address}")
        if phone:
            md.append(f"- Phone: {phone}")
        md.append("")

        if hours:
            md.append("## Hours")
            md.append(hours.strip())
            md.append("")

        md.append("## Ordering / reservations")
        md.append("- The assistant should guide callers to the official online ordering link when placing a pickup order.")
        md.append("- If reservations are requested and no reservation system is confirmed, collect details and advise calling the restaurant.")
        md.append("")

        md.append("## Guardrails")
        md.append("- Never request/store card numbers.")
        md.append("- Never guess prices, ingredients, or hours. Use this Knowledge Base as the source of truth.")
        md.append("- Allergy note: if a caller mentions allergies, advise them to confirm ingredients and cross-contact with the restaurant.")
        md.append("")

        if menu_price_lines:
            md.append("## Menu (selected items/prices from online ordering)")
            md.append("(Use the online ordering menu as the most current source. Do not guess if an item is missing.)")
            md.append("")
            for ln in menu_price_lines[:120]:
                md.append(f"- {ln}")
            md.append("")

        md.append("## Sources")
        for s in sources:
            md.append(f"- {s}")
        md.append("")

        md_text = "\n".join(md).strip() + "\n"
        h = hashlib.sha256(md_text.encode("utf-8")).hexdigest()[:12]

        out_md = base_dir / f"knowledge_base_{slug}_full_latest.md"
        out_html = base_dir / f"knowledge_base_{slug}_full_latest.html"
        out_md.write_text(md_text)
        out_html.write_text(md_to_html(md_text))

        # copy to Desktop
        desktop_dir = Path.home() / "Desktop" / "KB Uploads" / slug
        desktop_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out_md, desktop_dir / out_md.name)
        shutil.copy2(out_html, desktop_dir / out_html.name)

        print(
            json.dumps(
                {
                    "ok": True,
                    "slug": slug,
                    "hash": h,
                    "outputPathLatestMd": str(out_md),
                    "outputPathLatestHtml": str(out_html),
                    "desktopDir": str(desktop_dir),
                    "address": address,
                    "phone": phone,
                    "hours": hours,
                }
            )
        )
        return 0

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(json.dumps({"ok": False, "slug": slug, "error": err}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
