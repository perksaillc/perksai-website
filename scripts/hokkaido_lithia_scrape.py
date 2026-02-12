#!/usr/bin/env python3
"""Scrape public info for Hokkaido Hibachi & Sushi - Lithia (menu + hours + contact).

- Writes a markdown report to: /Users/gioalers/clawd/tmp/hokkaido_lithia_public_info.md
- Writes state to: /Users/gioalers/clawd/memory/hokkaido-lithia-scrape-state.json

No external deps.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.request

OUT_MD = "/Users/gioalers/clawd/tmp/hokkaido_lithia_public_info.md"
STATE_PATH = "/Users/gioalers/clawd/memory/hokkaido-lithia-scrape-state.json"

OFFICIAL_SITE = "https://www.hokkaidolithiafl.com/"
ORDER_BASE = "https://order.hokkaidolithiafl.com"
LOCATIONINFO = f"{ORDER_BASE}/locationinfo?lid=17587"
CONTACT = f"{ORDER_BASE}/contact"

CATEGORY_PATHS = [
    ("Appetizers from Sushi Bar", "/order/main/appetizers-from-sushi-bar"),
    ("Appetizers from the Kitchen", "/order/main/appetizers-from-the-kitchen"),
    ("Soup", "/order/main/soup"),
    ("Salad", "/order/main/salad"),
    ("Sushi / Sashimi A La Carte", "/order/main/sushi-sashimi-a-la-carte"),
    ("Roll / Hand Roll", "/order/main/roll-hand-roll"),
    ("Chef's Special Rolls", "/order/main/chefs-special-rolls"),
    ("Sushi Bar Entrees", "/order/main/sushi-bar-entrees"),
    ("Fried Rice / Noodles", "/order/main/fried-rice-noodles"),
    ("Yaki Udon / Soba", "/order/main/yaki-udon-soba"),
    ("Teriyaki", "/order/main/teriyaki"),
    ("Tempura", "/order/main/tempura"),
    ("Katsu", "/order/main/katsu"),
    ("Hibachi Entrees", "/order/main/hibachi-entrees"),
    ("Combination Dinners", "/order/main/combination-dinners"),
    ("Kids Menu", "/order/main/kids-menu"),
    ("Teriyaki for Kids", "/order/main/teriyaki-for-kids"),
    ("Lunch Sushi Special", "/order/main/lunch-sushi-special"),
    ("Maki Lunch", "/order/main/maki-lunch"),
    ("Hibachi Lunch Specials", "/order/main/hibachi-lunch-specials"),
    ("Lunch Bento Box", "/order/main/lunch-bento-box"),
    ("Side Order", "/order/main/side-order"),
    ("Bubble Tea", "/order/main/bubble-tea"),
]

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="ignore")


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = (
        s.replace("&amp;", "&")
        .replace("&nbsp;", " ")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_meta_description(html: str) -> str | None:
    m = re.search(r"<meta\s+name=\"description\"\s+content=\"([^\"]+)\"", html, re.I)
    return m.group(1).strip() if m else None


def parse_contact(html: str) -> dict:
    # Works on /contact page.
    text = strip_tags(html)
    out = {}
    m_phone = re.search(r"Phone\s*:\s*\(?\d{3}\)?\s*\d{3}-\d{4}", text)
    if m_phone:
        out["phone"] = m_phone.group(0).split(":", 1)[-1].strip()
    # Address appears as: 16769 Fishhawk Blvd Lithia, FL 33547
    m_addr = re.search(r"16769\s+Fishhawk\s+Blvd\s+Lithia,\s*FL\s*33547", text)
    if m_addr:
        out["address"] = m_addr.group(0)
    return out


def parse_hours(location_html: str) -> dict[str, str]:
    # Table rows look like:
    # <td class="label-day">Tuesday</td> ... <strong>11:30 AM - 9:30 PM</strong>
    days = "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split()
    pattern = re.compile(
        r"<td\s+class=\"label-day\">\s*(%s)\s*</td>\s*<td[^>]*>\s*<strong>\s*([^<]+)\s*</strong>"
        % "|".join(days),
        re.I,
    )
    found = {}
    for day, hours in pattern.findall(location_html):
        d = day.capitalize()
        if d not in found:
            found[d] = strip_tags(hours)
    return found


def parse_menu_items(category_html: str) -> list[dict]:
    items = []
    # Each item has a <div class="content"> ... </div> with <h3>, optional <p>, and price spans.
    for block in re.findall(r"<div\s+class=\"content\">([\s\S]*?)</div>", category_html, re.I):
        h3 = re.search(r"<h3>([\s\S]*?)</h3>", block, re.I)
        if not h3:
            continue
        name = strip_tags(h3.group(1))
        if not name or name.lower() in {"menu", "hours"}:
            continue
        desc_m = re.search(r"<p>([\s\S]*?)</p>", block, re.I)
        desc = strip_tags(desc_m.group(1)) if desc_m else ""

        prices = [strip_tags(p) for p in re.findall(r"menuitempreview_pricevalue\">\s*([^<]+)", block, re.I)]
        # Some items show multiple price lines in plain text like "Sushi: $X" "Sashimi: $Y".
        if not prices:
            # pull out any $-amounts nearby
            raw = strip_tags(block)
            p2 = re.findall(r"\$\d+\.\d{2}", raw)
            prices = p2

        items.append({
            "name": name,
            "description": desc,
            "prices": prices,
        })

    # Deduplicate by name+desc+prices
    seen = set()
    out = []
    for it in items:
        key = (it["name"], it["description"], tuple(it["prices"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def md_escape(s: str) -> str:
    return s.replace("\n", " ").strip()


def main() -> int:
    state = load_state()

    official_html = fetch(OFFICIAL_SITE)
    contact_html = fetch(CONTACT)
    location_html = fetch(LOCATIONINFO)

    meta_desc = parse_meta_description(official_html)
    contact = parse_contact(contact_html)
    hours = parse_hours(location_html)

    menu = {}
    for title, path in CATEGORY_PATHS:
        url = ORDER_BASE + path
        try:
            html = fetch(url)
            menu[title] = parse_menu_items(html)
        except Exception as e:
            menu[title] = {"error": str(e), "url": url}

    report = {
        "business_name": "Hokkaido Hibachi & Sushi - Lithia",
        "official_site": OFFICIAL_SITE,
        "order_site": ORDER_BASE,
        "contact": contact,
        "hours": hours,
        "meta_description": meta_desc,
        "scraped_at_epoch": int(time.time()),
        "menu": menu,
    }

    md_lines = []
    md_lines.append("# Hokkaido Hibachi & Sushi - Lithia (Public Info)")
    md_lines.append("")
    md_lines.append(f"**Official site:** {OFFICIAL_SITE}")
    md_lines.append(f"**Online ordering:** {ORDER_BASE}")
    if contact.get("address"):
        md_lines.append(f"**Address:** {contact['address']}")
    if contact.get("phone"):
        md_lines.append(f"**Phone:** {contact['phone']}")
    md_lines.append("")

    md_lines.append("## Hours")
    if hours:
        for d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]:
            if d in hours:
                md_lines.append(f"- **{d}:** {hours[d]}")
    else:
        md_lines.append("(Hours not found on scrape)")
    md_lines.append("")

    md_lines.append("## About (from website)")
    if meta_desc:
        md_lines.append(meta_desc)
        md_lines.append("")

    md_lines.append("## Menu (from online ordering)")
    md_lines.append("Note: Lunch items may only appear during lunch ordering hours.")
    md_lines.append("")

    for section, items in menu.items():
        md_lines.append(f"### {section}")
        if isinstance(items, dict) and items.get("error"):
            md_lines.append(f"(Error scraping: {items['error']})")
            md_lines.append("")
            continue
        if not items:
            md_lines.append("(No items found)")
            md_lines.append("")
            continue
        for it in items:
            price = " | ".join(it.get("prices") or [])
            desc = it.get("description") or ""
            line = f"- **{md_escape(it['name'])}**"
            if desc:
                line += f" — {md_escape(desc)}"
            if price:
                line += f" — {price}"
            md_lines.append(line)
        md_lines.append("")

    md = "\n".join(md_lines).strip() + "\n"

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    h = hashlib.sha256(md.encode("utf-8")).hexdigest()

    state_out = {
        **state,
        "lastRunAt": int(time.time()),
        "lastHash": h,
        "outputPath": OUT_MD,
    }
    save_state(state_out)

    # Print a short single-line status for cron usage.
    print(json.dumps({
        "ok": True,
        "hash": h,
        "outputPath": OUT_MD,
        "address": contact.get("address"),
        "phone": contact.get("phone"),
        "hours": hours,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        raise
