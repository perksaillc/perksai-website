#!/usr/bin/env python3
"""Generate per-restaurant pitch deck tab content (markdown) for manual copy/paste.

Why:
- Google Docs (document tabs) automation can be flaky due to iframe targeting.
- This generator produces a clean, restaurant-specific content pack the user (or future automation)
  can paste into each Doc tab.

Usage:
  python3 /Users/gioalers/clawd/scripts/restaurant_pitch_deck_generator.py --slug <slug>

Inputs:
- /Users/gioalers/clawd/tmp/retail_agents/<slug>/restaurant_profile.json
- /Users/gioalers/Desktop/KB Uploads/<slug>/knowledge_base_<slug>_full_latest.md (optional but preferred)

Output:
- /Users/gioalers/clawd/tmp/retail_agents/<slug>/pitch_deck_content_<slug>.md

Prints one-line JSON: {ok, slug, outPath}
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text())


def _first_kb_fields(kb_text: str) -> dict:
    # very light parse from our KB template
    out = {}
    # phone
    m = re.search(r"- Phone: (\(\d{3}\) \d{3}-\d{4})", kb_text)
    if m:
        out["phone"] = m.group(1)
    # address
    m = re.search(r"- Address: (.+)", kb_text)
    if m:
        out["address"] = m.group(1).strip()
    # hours block
    m = re.search(r"## Hours\n([\s\S]*?)\n\n## ", kb_text)
    if m:
        out["hours"] = m.group(1).strip()
    return out


def _money_range(lo: int, hi: int) -> str:
    return f"{lo} to {hi} dollars/month + usage fee"


def _sanitize_ascii(s: str) -> str:
    """Make text safe for Google Docs copy/paste.

    We frequently see mojibake like "Â" / "Äì" in Google Docs tabs when
    pasting rich text with smart quotes/dashes/bullets.

    Policy:
    - Prefer plain ASCII for all generated copy/paste packs.
    - Replace common typography with ASCII equivalents.
    """

    repl = {
        "\u2014": "-",  # em dash
        "\u2013": "-",  # en dash
        "\u2212": "-",  # minus
        "\u2019": "'",  # right single quote
        "\u2018": "'",  # left single quote
        "\u201c": '"',  # left double quote
        "\u201d": '"',  # right double quote
        "\u2026": "...",  # ellipsis
        "\u2022": "-",  # bullet
        "\u2192": "->",  # right arrow
        "\u00a0": " ",  # nbsp
    }
    for a, b in repl.items():
        s = s.replace(a, b)

    # Hard guarantee: ASCII-only output.
    return s.encode("ascii", "ignore").decode("ascii")


def build_content(cfg: dict, kb_text: str | None) -> str:
    name = cfg.get("name", cfg.get("slug", "Restaurant"))
    slug = cfg.get("slug", "")
    city = cfg.get("city", "")
    state = cfg.get("state", "FL")

    address = cfg.get("address")
    phone = cfg.get("phone")
    hours = cfg.get("hours")
    website = cfg.get("website")
    menu_url = cfg.get("menu_url")
    order_url = cfg.get("order_url")

    if kb_text:
        kb = _first_kb_fields(kb_text)
        address = address or kb.get("address")
        phone = phone or kb.get("phone")
        hours = hours or kb.get("hours")

    # normalize hours bullets for deck
    hours_lines = []
    if hours:
        for ln in hours.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            hours_lines.append(f"- {ln}")

    ordering_note = (
        f"- Online ordering: {order_url}" if order_url else "- Online ordering: not confirmed"
    )

    md = []
    md.append(f"# {name} - Pitch Deck Content (Copy/Paste)\n")
    md.append(f"Slug: `{slug}`\n")
    md.append("Paste tip: In Google Docs tabs, use Cmd+A then Delete, then paste. If formatting acts weird, use Paste without formatting (Cmd+Shift+V).\n")

    md.append("---\n")
    md.append("## TAB: Pitch Deck\n")
    md.append(f"{name} ({city}, {state})\nAI Phone Assistant — Pitch Deck\n")
    md.append("1) Introduction & Value Proposition")
    md.append(
        f"{name} gets high-intent calls that typically fall into a few buckets: (1) confirm hours/location, (2) quick menu questions, and (3) ordering/reservation guidance. The AI assistant answers instantly and consistently, reducing missed calls and front-of-house interruption.\n"
    )
    md.append("What the AI assistant delivers:")
    md.append("- Fewer missed calls during peak hours")
    md.append("- Faster answers to FAQs (hours, location, menu)")
    md.append("- Better guest experience (consistent, accurate info)")
    md.append("- Less staff interruption so the team can focus on in-person guests\n")

    md.append("Common call intents handled instantly")
    if hours:
        md.append("- \"Are you open right now?\" -> uses published hours + current time")
    if address:
        md.append(f"- \"Where are you located?\" -> {address}")
    if phone:
        md.append(f"- \"What's the phone number?\" -> {phone}")
    if menu_url:
        md.append(f"- \"Where can I see the menu?\" -> {menu_url}")
    if order_url:
        md.append(f"- \"How do I order online?\" -> {order_url}")
    else:
        md.append("- \"How do I place an order?\" -> advise calling the restaurant and/or using the official menu link")
    md.append("")

    md.append("2) How the AI Assistant Works")
    md.append("- Greeting -> identify intent: hours, directions, menu, ordering, reservations")
    md.append("- Knowledge Base retrieval: hours, contact info, official links")
    md.append("- Ordering: guide to the official ordering link if available; otherwise advise calling")
    md.append(
        "- Reservations: if reservations are requested and no system is confirmed, collect details and advise calling to confirm"
    )
    md.append("- Confirmation: read back any captured details clearly\n")

    md.append("Safety and compliance guardrails")
    md.append("- Never request/store card numbers")
    md.append("- Never guess prices/ingredients/hours/policies; use KB")
    md.append(
        "- Allergy language: if allergies are mentioned, advise confirming ingredients and cross-contact with staff\n"
    )

    md.append(f"3) Starter Plan ({_money_range(200,250)})\nSee \"Pricing\" tab.\n")
    md.append(f"4) Medium Plan ({_money_range(300,350)})\nSee \"Pricing\" tab.\n")
    md.append(f"5) High Plan ({_money_range(400,450)})\nSee \"Pricing\" tab.\n")
    md.append(f"6) Website + AI Assistant Plan ({_money_range(450,500)})\nSee \"Pricing\" tab.\n")

    md.append(f"{name} baseline facts")
    if website:
        md.append(f"- Website: {website}")
    if menu_url:
        md.append(f"- Menu: {menu_url}")
    if order_url:
        md.append(f"- Online ordering: {order_url}")
    if phone:
        md.append(f"- Phone: {phone}")
    if address:
        md.append(f"- Address: {address}")
    if hours_lines:
        md.append("- Hours:")
        md.extend([f"  {h}" for h in hours_lines])
    md.append("\nImplementation timeline\n- 1-3 business days for initial setup + go-live (assuming manual KB upload + quick QA)\n")
    md.append("STATUS: COMPLETE\n")

    md.append("---\n")
    md.append("## TAB: Pricing\n")
    md.append(f"{name} — Pricing & Packages\n")
    md.append("How billing works")
    md.append("- Monthly plan fee (covers setup, prompt/KB configuration, and ongoing tuning)")
    md.append("- Usage fee: per-minute AI call time billed at provider cost (voice + LLM)")
    md.append("- Payment handling: the AI never collects card numbers\n")

    md.append("Starter Plan — 200 to 250 dollars/month + usage fee")
    md.append("Includes:")
    md.append("- Answer calls and handle FAQs: hours, directions, phone, menu link")
    md.append("- KB-first accuracy (no guessing)")
    md.append("- Basic reservation request capture (if requested): day/time, party size, name, callback")
    md.append("- Monthly refresh: 1 KB update + light tuning\n")

    md.append("Medium Plan — 300 to 350 dollars/month + usage fee")
    md.append("Everything in Starter, plus:")
    md.append("- After-hours coverage: capture intent and provide next-step messaging")
    md.append("- Bi-weekly optimization based on transcripts")
    md.append("- Simple reporting: call volume + top intents\n")

    md.append("High Plan — 400 to 450 dollars/month + usage fee")
    md.append("Everything in Medium, plus:")
    md.append("- Weekly optimization + QA scoring")
    md.append("- Custom escalation rules\n")

    md.append("Website + AI Assistant Plan — 450 to 500 dollars/month + usage fee")
    md.append("Includes:")
    md.append("- Simple mobile-friendly site refresh (1–3 pages) and basic SEO foundation")
    md.append("- AI phone assistant (Medium plan feature set)\n")

    md.append("Optional add-ons")
    md.append("- Additional phone line/location")
    md.append("- Bilingual (English + Spanish)")
    md.append("- Live call transfer (if desired)")
    md.append("\nSTATUS: COMPLETE\n")

    md.append("---\n")
    md.append("## TAB: Strategy\n")
    md.append(f"{name} — Strategy & Standardized Operations\n")
    md.append("Objectives")
    md.append("- Reduce missed calls and improve guest experience")
    md.append("- Answer FAQs instantly with accurate hours/location/menu link")
    md.append("- Reduce staff time spent on repetitive questions\n")

    md.append("Rollout plan (phased)")
    md.append("Phase 0 — Confirm operations")
    md.append("- Confirm reservation policy (accepted or not)")
    md.append("- Confirm any key policies guests ask about")
    md.append("- Confirm escalation rules\n")

    md.append("Phase 1 — Build")
    md.append("- Verify knowledge base (hours, address, phone, official links)")
    md.append("- Configure guardrails\n")

    md.append("Phase 2 — Test")
    md.append("- Test calls: open-now, hours, location, menu link, reservation request, allergy scenario\n")

    md.append("Phase 3 — Go-live")
    md.append("- Monitor transcripts; patch missing info into KB\n")

    md.append("Knowledge base governance")
    md.append("- Source of truth: official website/menu/ordering")
    md.append("- Update SLAs: hours same-day; menu/prices within 48 hours\n")
    md.append("STATUS: COMPLETE\n")

    md.append("---\n")
    md.append("## TAB: SHARE AND TEST LINK\n")
    md.append(f"{name} — Share & Test\n")
    md.append("Public links")
    if website:
        md.append(f"- Website: {website}")
    if menu_url:
        md.append(f"- Menu: {menu_url}")
    if order_url:
        md.append(f"- Online ordering: {order_url}")
    if phone:
        md.append(f"- Phone: {phone}")
    if address:
        md.append(f"- Address: {address}")
    md.append("\nTest script")
    md.append("1) Hours\n- \"Are you open right now?\"\n- \"What time do you close tonight?\"\n")
    md.append("2) Location\n- \"Where are you located?\"\n- \"What's your phone number?\"\n")
    md.append("3) Menu\n- \"Where can I see your menu?\"\n")
    md.append("4) Reservation request\n- \"Can you book a table for 4 tomorrow at 7?\"\n")
    md.append("5) Allergy guardrail\n- \"I have a peanut allergy.\"\n")
    md.append("\nSTATUS: COMPLETE\n")

    md.append("---\n")
    md.append(f"## TAB: {name} Facts (KB)\n")
    md.append(f"{name} — Facts (KB)\n")
    md.append("Contact")
    if address:
        md.append(f"- Address: {address}")
    if phone:
        md.append(f"- Phone: {phone}")
    md.append("\nHours")
    if hours_lines:
        md.extend(hours_lines)
    else:
        md.append("- Use the KB for confirmed hours.")
    md.append("\nLinks")
    if website:
        md.append(f"- Website: {website}")
    if menu_url:
        md.append(f"- Menu: {menu_url}")
    if order_url:
        md.append(f"- Online ordering: {order_url}")
    md.append("\nGuardrails")
    md.append("- Do not collect card numbers")
    md.append("- Do not guess menu/prices/hours; use KB")
    md.append("\nSTATUS: COMPLETE\n")

    return _sanitize_ascii("\n".join(md).strip() + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    args = ap.parse_args()

    slug = args.slug
    base = Path("/Users/gioalers/clawd/tmp/retail_agents") / slug
    cfg_path = base / "restaurant_profile.json"
    if not cfg_path.exists():
        print(json.dumps({"ok": False, "slug": slug, "error": f"missing config: {cfg_path}"}))
        return 1

    cfg = _read_json(cfg_path)

    kb_path = Path.home() / "Desktop" / "KB Uploads" / slug / f"knowledge_base_{slug}_full_latest.md"
    kb_text = kb_path.read_text() if kb_path.exists() else None

    out_path = base / f"pitch_deck_content_{slug}.md"
    out_path.write_text(build_content(cfg, kb_text))

    print(json.dumps({"ok": True, "slug": slug, "outPath": str(out_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
