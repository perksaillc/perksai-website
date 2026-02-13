#!/usr/bin/env python3
"""Akira (Riverview, FL) public info + menu scraper.

Outputs (overwrites):
- tmp/retail_agents/akira_riverview/knowledge_base_akira_full_latest.html
- tmp/retail_agents/akira_riverview/knowledge_base_akira_full_latest.md
- tmp/retail_agents/akira_riverview/restaurant_profile.json

Prints one-line JSON summary to stdout:
{ ok, hash, outputPathLatestHtml, outputPathLatestMd, name, address, phone, hours }
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = "/Users/gioalers/clawd"
OUT_DIR = os.path.join(BASE_DIR, "tmp/retail_agents/akira_riverview")

URL_HOME = "https://www.akirafl.com/"
URL_MENU = "https://www.akirafl.com/menu"


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def extract_ld_json(html: str) -> Optional[dict]:
    # Find the first application/ld+json block that looks like a Restaurant
    for m in re.finditer(r"<script[^>]+type=\"application/ld\+json\"[^>]*>(.*?)</script>", html, re.S | re.I):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") in ("Restaurant", "FoodEstablishment"):
            return data
    return None


def normalize_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_hours_from_ld(ld: dict) -> List[str]:
    # Expect openingHoursSpecification.openingHours like ["Su 12:30-21:00", "Tu,We,Th,Fr,Sa 11:30-14:30", ...]
    spec = ld.get("openingHoursSpecification")
    if isinstance(spec, dict):
        oh = spec.get("openingHours")
        if isinstance(oh, list):
            return [str(x) for x in oh if x]
    return []


def expand_days(days_part: str) -> List[str]:
    # handles "Tu,We,Th" etc
    mapping = {
        "Mo": "Monday",
        "Tu": "Tuesday",
        "We": "Wednesday",
        "Th": "Thursday",
        "Fr": "Friday",
        "Sa": "Saturday",
        "Su": "Sunday",
    }
    days = []
    for code in days_part.split(","):
        code = code.strip()
        if not code:
            continue
        if code in mapping:
            days.append(mapping[code])
    return days


def format_time_range(rng: str) -> str:
    # "12:30-21:00" => "12:30 PM – 9:00 PM" (best-effort)
    def conv(t: str) -> str:
        t = t.strip()
        hh, mm = t.split(":")
        h = int(hh)
        m = int(mm)
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12
        if h12 == 0:
            h12 = 12
        return f"{h12}:{m:02d} {ampm}"

    if "-" not in rng:
        return rng
    a, b = rng.split("-", 1)
    return f"{conv(a)} – {conv(b)}"


def build_hours_table(opening_hours: List[str]) -> Dict[str, List[str]]:
    # Returns {"Monday": ["Closed" or "11:30 AM – 2:30 PM", "4:00 PM – 9:00 PM"], ...}
    out: Dict[str, List[str]] = {d: [] for d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]}

    for entry in opening_hours:
        entry = entry.strip()
        if not entry:
            continue
        # patterns: "Su 12:30-21:00" or "Tu,We,Th 16:00-21:00"
        m = re.match(r"^([A-Za-z,]+)\s+(\d{1,2}:\d{2}-\d{1,2}:\d{2})$", entry)
        if not m:
            continue
        days_part, rng = m.group(1), m.group(2)
        days = expand_days(days_part)
        if not days:
            continue
        fr = format_time_range(rng)
        for d in days:
            out[d].append(fr)

    # Fill in closed if empty (best-effort; Akira is closed Monday per website)
    for d, ranges in out.items():
        if not ranges:
            out[d] = ["Closed"]

    return out


def extract_next_menu_value(html: str) -> dict:
    # The menu JSON is embedded in a Next.js stream chunk:
    # self.__next_f.push([1,"6:[\"$\",...,{\"value\":{\"menuCategories\":[...]}}]"])
    frags = re.findall(r"self\.__next_f\.push\(\[1,\"(.*?)\"\]\)", html)
    if not frags:
        raise RuntimeError("No __next_f push fragments found")

    candidates = [f for f in frags if "menuCategories" in f]
    if not candidates:
        raise RuntimeError("No fragment containing menuCategories found")

    # Some pages include multiple; merge by first valid
    for frag in candidates:
        try:
            decoded = json.loads('"' + frag + '"')
            colon = decoded.find(":")
            if colon == -1:
                continue
            payload = decoded[colon + 1 :]
            arr = json.loads(payload)
            if isinstance(arr, list):
                for x in arr:
                    if isinstance(x, dict) and isinstance(x.get("value"), dict) and "menuCategories" in x["value"]:
                        return x["value"]
        except Exception:
            continue

    raise RuntimeError("Failed to decode menuCategories fragment")


def menu_to_sections(menu_value: dict) -> List[Tuple[str, List[dict]]]:
    # Flatten into sections: "<MenuCatName> — <MenuGroupName>" with items
    sections: List[Tuple[str, List[dict]]] = []
    cats = menu_value.get("menuCategories") or []
    for cat in cats:
        cat_name = cat.get("menuCatName") or "Menu"
        groups = cat.get("menuGroups") or []
        for g in groups:
            group_name = g.get("menuGroupName") or "(Unlabeled)"
            items = g.get("menuItems") or []
            label = f"{cat_name} — {group_name}"
            sections.append((label, items))
    return sections


def format_kb_html(name: str, city_state: str, phone: str, address: str, website: str, menu_url: str, hours_table: Dict[str, List[str]], sections: List[Tuple[str, List[dict]]]) -> str:
    def esc(s: str) -> str:
        return (
            (s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    lines: List[str] = []
    lines.append("<!doctype html>")
    lines.append("<html lang='en'>")
    lines.append("<head>")
    lines.append("  <meta charset='utf-8' />")
    lines.append("  <meta name='viewport' content='width=device-width, initial-scale=1' />")
    lines.append(f"  <title>{esc(name)} ({esc(city_state)}) — Knowledge Base</title>")
    lines.append("  <style>")
    lines.append("    body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.35;margin:24px;color:#111}")
    lines.append("    h1{margin:0 0 8px 0;font-size:26px}")
    lines.append("    h2{margin:22px 0 8px 0;font-size:18px;border-top:1px solid #eee;padding-top:14px}")
    lines.append("    h3{margin:18px 0 6px 0;font-size:16px}")
    lines.append("    .meta{color:#444;font-size:14px;margin:0 0 16px 0}")
    lines.append("    .pill{display:inline-block;background:#f3f4f6;border:1px solid #e5e7eb;border-radius:999px;padding:2px 10px;margin-right:8px}")
    lines.append("    ul{margin:6px 0 12px 20px}")
    lines.append("    li{margin:2px 0}")
    lines.append("    .price{white-space:nowrap;color:#111}")
    lines.append("    .desc{color:#444}")
    lines.append("    .small{color:#666;font-size:12px}")
    lines.append("    .section{margin-bottom:18px}")
    lines.append("  </style>")
    lines.append("</head>")
    lines.append("<body>")
    lines.append(f"  <h1>{esc(name)} ({esc(city_state)}) — Knowledge Base</h1>")
    lines.append(
        "  <p class='meta'>"
        f"<span class='pill'>Phone: {esc(phone)}</span>"
        f"<span class='pill'>Address: {esc(address)}</span>"
        f"<span class='pill'>Website: {esc(website)}</span>"
        "</p>"
    )

    lines.append("  <div class='section'>")
    lines.append("    <h2>Links</h2>")
    lines.append("    <ul>")
    lines.append(f"      <li><strong>Website:</strong> {esc(website)}</li>")
    lines.append(f"      <li><strong>Menu:</strong> {esc(menu_url)}</li>")
    lines.append("    </ul>")
    lines.append("  </div>")

    lines.append("  <div class='section'>")
    lines.append("    <h2>Hours</h2>")
    lines.append("    <ul>")
    for day in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]:
        ranges = hours_table.get(day, ["Closed"]) or ["Closed"]
        if ranges == ["Closed"]:
            lines.append(f"      <li><strong>{esc(day)}:</strong> Closed</li>")
        else:
            # If multiple windows, join with " | "
            joined = " | ".join(esc(x) for x in ranges)
            lines.append(f"      <li><strong>{esc(day)}:</strong> {joined}</li>")
    lines.append("    </ul>")
    lines.append("  </div>")

    lines.append("  <div class='section'>")
    lines.append("    <h2>Menu</h2>")
    lines.append(f"    <p class='small'>Scraped from {esc(menu_url)} (best-effort). If anything looks off, confirm on-site or by phone.</p>")

    for (label, items) in sections:
        lines.append(f"    <h3>{esc(label)}</h3>")
        lines.append("    <ul>")
        for it in items:
            nm = normalize_whitespace(str(it.get("menuItemName") or ""))
            desc = normalize_whitespace(str(it.get("menuItemDesc") or ""))
            price = it.get("menuItemPrice")
            try:
                price_f = float(price) if price is not None else None
            except Exception:
                price_f = None
            price_str = f"${price_f:.2f}" if price_f is not None else ""
            spicy = bool(it.get("spicy"))
            popular = bool(it.get("popular"))
            flags = []
            if spicy:
                flags.append("spicy")
            if popular:
                flags.append("popular")
            flag_str = f" <span class='small'>(" + ", ".join(flags) + ")</span>" if flags else ""

            if desc and price_str:
                lines.append(
                    f"      <li><strong>{esc(nm)}</strong>{flag_str}<span class='desc'> — {esc(desc)}</span> <span class='price'>— {esc(price_str)}</span></li>"
                )
            elif price_str:
                lines.append(f"      <li><strong>{esc(nm)}</strong>{flag_str} <span class='price'>— {esc(price_str)}</span></li>")
            else:
                lines.append(f"      <li><strong>{esc(nm)}</strong>{flag_str}</li>")
        lines.append("    </ul>")

    lines.append("</body>")
    lines.append("</html>")

    return "\n".join(lines)


def html_to_md(name: str, city_state: str, phone: str, address: str, website: str, menu_url: str, hours_table: Dict[str, List[str]], sections: List[Tuple[str, List[dict]]]) -> str:
    out: List[str] = []
    out.append(f"# {name} ({city_state}) — Knowledge Base")
    out.append("")
    out.append(f"- Phone: {phone}")
    out.append(f"- Address: {address}")
    out.append(f"- Website: {website}")
    out.append(f"- Menu: {menu_url}")
    out.append("")

    out.append("## Hours")
    for day in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]:
        ranges = hours_table.get(day, ["Closed"]) or ["Closed"]
        if ranges == ["Closed"]:
            out.append(f"- {day}: Closed")
        else:
            out.append(f"- {day}: " + " | ".join(ranges))
    out.append("")

    out.append("## Menu")
    out.append(f"Source: {menu_url}")
    out.append("")

    for (label, items) in sections:
        out.append(f"### {label}")
        for it in items:
            nm = normalize_whitespace(str(it.get("menuItemName") or ""))
            desc = normalize_whitespace(str(it.get("menuItemDesc") or ""))
            price = it.get("menuItemPrice")
            try:
                price_f = float(price) if price is not None else None
            except Exception:
                price_f = None
            price_str = f"${price_f:.2f}" if price_f is not None else ""
            flags = []
            if it.get("spicy"):
                flags.append("spicy")
            if it.get("popular"):
                flags.append("popular")
            flag = f" ({', '.join(flags)})" if flags else ""

            if desc and price_str:
                out.append(f"- **{nm}**{flag} — {desc} — {price_str}")
            elif price_str:
                out.append(f"- **{nm}**{flag} — {price_str}")
            else:
                out.append(f"- **{nm}**{flag}")
        out.append("")

    return "\n".join(out).strip() + "\n"


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    # Fetch sources
    home_html = fetch(URL_HOME)
    menu_html = fetch(URL_MENU)

    ld = extract_ld_json(menu_html) or extract_ld_json(home_html) or {}

    name = normalize_whitespace(ld.get("name") or "Akira")

    addr = ld.get("address") or {}
    if isinstance(addr, dict):
        street = normalize_whitespace(addr.get("streetAddress") or "")
        locality = normalize_whitespace(addr.get("addressLocality") or "Riverview")
        region = normalize_whitespace(addr.get("addressRegion") or "FL")
        postal = normalize_whitespace(addr.get("postalCode") or "")
        address = ", ".join([x for x in [street, f"{locality}, {region} {postal}".strip()] if x])
        city_state = f"{locality}, {region}".strip(", ")
    else:
        address = "Riverview, FL"
        city_state = "Riverview, FL"

    phone = normalize_whitespace(ld.get("telephone") or "(813) 689-5544")

    opening_hours = parse_hours_from_ld(ld)
    hours_table = build_hours_table(opening_hours)

    menu_value = extract_next_menu_value(menu_html)
    sections = menu_to_sections(menu_value)

    # Build outputs
    kb_html = format_kb_html(
        name=name,
        city_state=city_state,
        phone=phone,
        address=address,
        website=URL_HOME,
        menu_url=URL_MENU,
        hours_table=hours_table,
        sections=sections,
    )

    kb_md = html_to_md(
        name=name,
        city_state=city_state,
        phone=phone,
        address=address,
        website=URL_HOME,
        menu_url=URL_MENU,
        hours_table=hours_table,
        sections=sections,
    )

    out_html = os.path.join(OUT_DIR, "knowledge_base_akira_full_latest.html")
    out_md = os.path.join(OUT_DIR, "knowledge_base_akira_full_latest.md")

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(kb_html)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(kb_md)

    profile = {
        "slug": "akira_riverview",
        "name": name,
        "city": "Riverview",
        "state": "FL",
        "address": address,
        "phone": phone,
        "website": URL_HOME,
        "menu": URL_MENU,
        "hoursSource": "schema.org JSON-LD from menu page",
        "generatedAt": datetime.now().isoformat(),
    }

    profile_path = os.path.join(OUT_DIR, "restaurant_profile.json")
    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    digest = sha256_text(kb_html + "\n" + kb_md)

    print(
        json.dumps(
            {
                "ok": True,
                "hash": digest,
                "outputPathLatestHtml": out_html,
                "outputPathLatestMd": out_md,
                "name": name,
                "address": address,
                "phone": phone,
                "hours": hours_table,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        raise
