#!/usr/bin/env python3
"""Scrape public info for Sunflower Cafe Inc (Valrico, FL).

Outputs:
- Markdown report: /Users/gioalers/clawd/tmp/sunflower_cafe_public_info.md
- State file: /Users/gioalers/clawd/memory/sunflower-cafe-scrape-state.json

No external deps.

Notes:
- This site uses a Yext Knowledge Tags embed script which contains structured business data.
- Menu pages are mostly static HTML; we parse menu item blocks from the HTML.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request

OUT_MD = "/Users/gioalers/clawd/tmp/sunflower_cafe_public_info.md"
# Convenience copies for manual upload to Retell KB
OUT_MD_LATEST = "/Users/gioalers/clawd/tmp/retail_agents/sunflower_cafe/knowledge_base_sunflower_full_latest.md"
OUT_HTML_LATEST = "/Users/gioalers/clawd/tmp/retail_agents/sunflower_cafe/knowledge_base_sunflower_full_latest.html"
STATE_PATH = "/Users/gioalers/clawd/memory/sunflower-cafe-scrape-state.json"

OFFICIAL_SITE = "https://www.sunflowercafe.net/"

# Yext Knowledge Tags embed for this site (contains address/hours/phone/email/description)
YEXT_EMBED_URL = (
    "https://knowledgetags.yextpages.net/embed"
    "?key=qMbYa18O3E71xBSZFVdNb0usc5pwDuoyXai3Nrs09lfBk3yKY6mQScSgV4gM8daI"
    "&account_id=7008624378"
    "&entity_id=7008624378"
    "&locale=en"
)

MENU_PAGES = [
    ("Lunch Menu", "https://www.sunflowercafe.net/lunch-menu"),
    ("Dinner & Appetizers", "https://www.sunflowercafe.net/dinner-appetizers"),
    ("Sushi", "https://www.sunflowercafe.net/sushi"),
    ("Soup / Salad / Misc", "https://www.sunflowercafe.net/soup-salad-misc"),
]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36"
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="ignore")


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = (
        s.replace("&amp;", "&")
        .replace("&nbsp;", " ")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
        .replace("\u00a0", " ")
    )
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_meta_description(html: str) -> str | None:
    m = re.search(r"<meta\s+name=\"description\"\s+content=\"([^\"]+)\"", html, re.I)
    return m.group(1).strip() if m else None


def parse_yext_embed(embed_js: str) -> dict:
    # The embed JS contains a serialized key-value map like:
    # "address.line1":"3452 Lithia Pinecrest Rd", ...
    def get(key: str) -> str | None:
        m = re.search(r'"%s"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"' % re.escape(key), embed_js)
        if not m:
            return None
        val = m.group(1)
        # unescape common sequences
        val = val.replace("\\/", "/").replace("\\n", "\n")
        val = val.replace("\\\"", '"')
        return val

    out = {
        "name": get("name") or get("businessName") or "Sunflower Cafe Inc",
        "description": get("description"),
        "address": {
            "line1": get("address.line1") or get("address1"),
            "line2": get("address.line2") or get("address2"),
            "city": get("address.city") or get("city"),
            "region": get("address.region") or get("region"),
            "postalCode": get("address.postalCode") or get("zip"),
            "countryCode": get("address.countryCode") or get("countryCode"),
        },
        "phone": get("mainPhone") or get("phone"),
        "email": get("email") or (get("emails[0]") if get("emails[0]") else None),
        # This site uses Yext; the human-readable hours string is often in additionalHoursText.
        "hoursText": get("hoursText") or get("additionalHoursText") or get("hours") or get("hours-text"),
    }

    # Basic cleanup
    if out["phone"]:
        out["phone"] = out["phone"].strip()
    if out["email"]:
        out["email"] = out["email"].strip()

    return out


def parse_hours_from_hours_text(hours_text: str | None) -> dict[str, str]:
    # Example (from site):
    # "Monday to Friday: Lunch 11:30 AM - 2:30 PM | Dinner 5:00 PM - 9:00 PM  Saturday: 12:00 PM - 9:00 PM Sunday: 12:00 PM - 8:30 PM"
    if not hours_text:
        return {}

    ht = re.sub(r"\s+", " ", hours_text).strip()
    # Normalize separators
    ht = ht.replace("|", " | ")

    out: dict[str, str] = {}

    m_wd = re.search(r"Monday\s+to\s+Friday\s*:\s*(.+?)(?:Saturday\s*:|Sunday\s*:|$)", ht, re.I)
    if m_wd:
        wd = m_wd.group(1).strip()
        # keep as a single string
        for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            out[d] = wd

    m_sat = re.search(r"Saturday\s*:\s*([^S]+?)(?:Sunday\s*:|$)", ht, re.I)
    if m_sat:
        out["Saturday"] = m_sat.group(1).strip()

    m_sun = re.search(r"Sunday\s*:\s*(.+)$", ht, re.I)
    if m_sun:
        out["Sunday"] = m_sun.group(1).strip()

    # Final cleanup
    for k, v in list(out.items()):
        out[k] = re.sub(r"\s+", " ", v).strip()

    return out


def parse_menu_page(html: str) -> dict:
    # Parse sections and items from menu pages.
    # Typical structure:
    # <div class="menuHeading">Appetizer</div>
    # <div class="menuItemBox"> ... <div class="menuItemName">Edamame</div> ... <div class="menuItemPrice">$5.95</div>

    sections: list[dict] = []
    current_section = {"title": "(Unlabeled)", "items": []}

    # Walk the HTML for headings + item boxes in order
    token_re = re.compile(r"(<div\s+class=\"menuHeading\"[^>]*>[\s\S]*?</div>)|(<div\s+class=\"menuItemBox[\s\S]*?</div>\s*<div\s+style=\"clear:\s*both;\"[^>]*></div>\s*</div>)",
                         re.I)

    def flush_section():
        nonlocal current_section
        if current_section["items"]:
            sections.append(current_section)
        current_section = {"title": "(Unlabeled)", "items": []}

    for m in token_re.finditer(html):
        heading_block = m.group(1)
        item_block = m.group(2)

        if heading_block:
            title = strip_tags(heading_block)
            title = re.sub(r"\s+", " ", title).strip()
            # New section
            flush_section()
            current_section["title"] = title
            continue

        if item_block:
            name_m = re.search(r"menuItemName\"[^>]*>\s*([\s\S]*?)\s*</div>", item_block, re.I)
            name = strip_tags(name_m.group(1)) if name_m else ""
            name = name.strip()
            if not name:
                continue

            # description can contain nested richText
            desc_m = re.search(r"menuItemDesc\"[^>]*>[\s\S]*?<div\s+class=\"richText\"[^>]*>([\s\S]*?)</div>", item_block, re.I)
            desc = strip_tags(desc_m.group(1)) if desc_m else ""

            price_m = re.search(r"menuItemPrice\"[^>]*>\s*([^<]+?)\s*</div>", item_block, re.I)
            price = strip_tags(price_m.group(1)) if price_m else ""

            # Some items may have multi-price text inside the block
            if not price:
                raw = strip_tags(item_block)
                price = " | ".join(re.findall(r"\$\s*\d+\.\d{2}", raw))

            current_section["items"].append({
                "name": name,
                "description": desc,
                "price": price,
            })

    # flush at end
    flush_section()

    # If nothing parsed, fallback: capture any name/price patterns
    if not sections:
        sections = [{"title": "(Parsed Fallback)", "items": []}]

    return {"sections": sections}


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
    return (s or "").replace("\n", " ").strip()


def html_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_html_page(title: str, contact: dict, hours: dict[str, str], about: str | None, menu: dict) -> str:
    addr = contact.get("address") or {}
    addr_parts = [addr.get("line1"), addr.get("line2"), ", ".join([p for p in [addr.get("city"), addr.get("region"), addr.get("postalCode")] if p])]
    addr_full = ", ".join([p for p in addr_parts if p and str(p).strip()])

    css = """
      body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.35;margin:24px;color:#111}
      h1{margin:0 0 8px 0;font-size:26px}
      h2{margin:22px 0 8px 0;font-size:18px;border-top:1px solid #eee;padding-top:14px}
      h3{margin:18px 0 6px 0;font-size:16px}
      .meta{color:#444;font-size:14px;margin:0 0 16px 0}
      .pill{display:inline-block;background:#f3f4f6;border:1px solid #e5e7eb;border-radius:999px;padding:2px 10px;margin-right:8px}
      ul{margin:6px 0 12px 20px}
      li{margin:2px 0}
      .price{white-space:nowrap;color:#111}
      .desc{color:#444}
      .small{color:#666;font-size:12px}
      .section{margin-bottom:18px}
    """

    def hours_list():
        if not hours:
            return "<p>(Hours not found)</p>"
        lines = []
        for d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]:
            if d in hours:
                lines.append(f"<li><strong>{html_escape(d)}:</strong> {html_escape(hours[d])}</li>")
        return "<ul>" + "".join(lines) + "</ul>"

    def menu_html():
        out = []
        for page_title, payload in menu.items():
            out.append(f"<h3>{html_escape(page_title)}</h3>")
            if isinstance(payload, dict) and payload.get("error"):
                out.append(f"<p class='small'>(Error scraping: {html_escape(payload.get('error'))})</p>")
                continue
            sections = payload.get("sections") if isinstance(payload, dict) else None
            if not sections:
                out.append("<p class='small'>(No items found)</p>")
                continue
            for sec in sections:
                st = sec.get("title") or "(Unlabeled)"
                out.append(f"<h4>{html_escape(st)}</h4>")
                items = sec.get("items") or []
                if not items:
                    out.append("<p class='small'>(No items found)</p>")
                    continue
                out.append("<ul>")
                for it in items:
                    name = html_escape(it.get("name", ""))
                    desc = html_escape(it.get("description", ""))
                    price = html_escape(it.get("price", ""))
                    parts = [f"<strong>{name}</strong>"]
                    if desc:
                        parts.append(f"<span class='desc'> — {desc}</span>")
                    if price:
                        parts.append(f" <span class='price'>— {price}</span>")
                    out.append("<li>" + "".join(parts) + "</li>")
                out.append("</ul>")
        return "\n".join(out)

    phone = contact.get("phone")
    email = contact.get("email")

    meta_pills = []
    if phone:
        meta_pills.append(f"<span class='pill'>Phone: {html_escape(phone)}</span>")
    if email:
        meta_pills.append(f"<span class='pill'>Email: {html_escape(email)}</span>")
    if addr_full:
        meta_pills.append(f"<span class='pill'>Address: {html_escape(addr_full)}</span>")

    return f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>{html_escape(title)}</title>
  <style>{css}</style>
</head>
<body>
  <h1>{html_escape(title)}</h1>
  <p class='meta'>{''.join(meta_pills)}</p>

  <div class='section'>
    <h2>Hours</h2>
    {hours_list()}
    <p class='small'>Note: Monday–Friday has a break between lunch and dinner.</p>
  </div>

  <div class='section'>
    <h2>About</h2>
    <p>{html_escape(about or '')}</p>
  </div>

  <div class='section'>
    <h2>Menu</h2>
    <p class='small'>Best-effort scrape from https://www.sunflowercafe.net/. If anything looks off, confirm on-site or by phone.</p>
    {menu_html()}
  </div>

  <p class='small'>Generated: {int(time.time())}</p>
</body>
</html>
"""


def main() -> int:
    state = load_state()

    official_html = fetch(OFFICIAL_SITE)
    embed_js = fetch(YEXT_EMBED_URL)

    meta_desc = parse_meta_description(official_html)
    yext = parse_yext_embed(embed_js)
    hours = parse_hours_from_hours_text(yext.get("hoursText"))

    menu: dict[str, object] = {}
    for title, url in MENU_PAGES:
        try:
            html = fetch(url)
            menu[title] = {
                "url": url,
                **parse_menu_page(html),
            }
        except Exception as e:
            menu[title] = {"url": url, "error": str(e)}

    report = {
        "business_name": yext.get("name") or "Sunflower Cafe Inc",
        "official_site": OFFICIAL_SITE,
        "contact": {
            "address": yext.get("address"),
            "phone": yext.get("phone"),
            "email": yext.get("email"),
        },
        "hours": hours,
        "hoursText": yext.get("hoursText"),
        "meta_description": meta_desc,
        "yext_description": yext.get("description"),
        "scraped_at_epoch": int(time.time()),
        "menu": menu,
        "sources": {
            "yext_embed": YEXT_EMBED_URL,
            "menu_pages": [u for _, u in MENU_PAGES],
        },
    }

    # Build markdown
    md_lines: list[str] = []
    md_lines.append("# Sunflower Cafe Inc (Valrico, FL) — Public Info")
    md_lines.append("")
    md_lines.append(f"**Official site:** {OFFICIAL_SITE}")

    addr = (yext.get("address") or {})
    addr_full = ""
    if addr.get("line1"):
        addr_full = addr.get("line1")
        if addr.get("city") or addr.get("region") or addr.get("postalCode"):
            addr_full += f"\n{addr.get('city','').strip()} {',' if addr.get('city') else ''} {addr.get('region','').strip()} {addr.get('postalCode','').strip()}".strip()
    if addr_full.strip():
        md_lines.append("**Address:** " + addr_full.replace("\n", ", "))

    if yext.get("phone"):
        md_lines.append(f"**Phone:** {yext['phone']}")
    if yext.get("email"):
        md_lines.append(f"**Email:** {yext['email']}")
    md_lines.append("")

    md_lines.append("## Hours")
    if hours:
        for d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]:
            if d in hours:
                md_lines.append(f"- **{d}:** {hours[d]}")
    elif yext.get("hoursText"):
        md_lines.append(yext["hoursText"])
    else:
        md_lines.append("(Hours not found)")
    md_lines.append("")

    md_lines.append("## About")
    if yext.get("description"):
        md_lines.append(yext["description"])
        md_lines.append("")
    if meta_desc and meta_desc != yext.get("description"):
        md_lines.append("(From website meta description)")
        md_lines.append(meta_desc)
        md_lines.append("")

    md_lines.append("## Menu")
    md_lines.append("Note: This scrape is best-effort; if something looks wrong, confirm on-site or by phone.")
    md_lines.append("")

    for page_title, payload in menu.items():
        md_lines.append(f"### {page_title}")
        if isinstance(payload, dict) and payload.get("error"):
            md_lines.append(f"(Error scraping: {payload['error']})")
            md_lines.append("")
            continue
        sections = (payload or {}).get("sections") if isinstance(payload, dict) else None
        if not sections:
            md_lines.append("(No items found)")
            md_lines.append("")
            continue
        for sec in sections:
            st = sec.get("title") or "(Unlabeled)"
            md_lines.append(f"#### {md_escape(st)}")
            items = sec.get("items") or []
            if not items:
                md_lines.append("(No items found)")
                continue
            for it in items:
                line = f"- **{md_escape(it.get('name',''))}**"
                if it.get("description"):
                    line += f" — {md_escape(it['description'])}"
                if it.get("price"):
                    line += f" — {md_escape(it['price'])}"
                md_lines.append(line)
            md_lines.append("")

    md = "\n".join(md_lines).strip() + "\n"

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    # Write convenient copies for manual KB upload
    os.makedirs(os.path.dirname(OUT_MD_LATEST), exist_ok=True)
    with open(OUT_MD_LATEST, "w", encoding="utf-8") as f:
        f.write(md)

    html_doc = build_html_page(
        title="Sunflower Cafe Inc (Valrico, FL) — Knowledge Base",
        contact=report["contact"],
        hours=hours,
        about=report.get("yext_description") or meta_desc,
        menu=menu,
    )

    os.makedirs(os.path.dirname(OUT_HTML_LATEST), exist_ok=True)
    with open(OUT_HTML_LATEST, "w", encoding="utf-8") as f:
        f.write(html_doc)

    h = hashlib.sha256(md.encode("utf-8")).hexdigest()

    save_state({
        **state,
        "lastRunAt": int(time.time()),
        "lastHash": h,
        "outputPath": OUT_MD,
        "outputPathLatestMd": OUT_MD_LATEST,
        "outputPathLatestHtml": OUT_HTML_LATEST,
        "report": {
            "name": report["business_name"],
            "address": addr,
            "phone": yext.get("phone"),
            "email": yext.get("email"),
        },
    })

    print(json.dumps({
        "ok": True,
        "hash": h,
        "outputPath": OUT_MD,
        "outputPathLatestMd": OUT_MD_LATEST,
        "outputPathLatestHtml": OUT_HTML_LATEST,
        "address": addr,
        "phone": yext.get("phone"),
        "email": yext.get("email"),
        "hours": hours,
        "sources": report["sources"],
    }, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        raise
