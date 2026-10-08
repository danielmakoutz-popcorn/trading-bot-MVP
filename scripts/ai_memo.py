#!/usr/bin/env python3
"""
Ai Boi memo v0.1

Reads the nightly research markdown and writes a short human memo.
This version is template/rule-based, not an LLM yet.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = PROJECT_ROOT / "reports" / "research"
AI_DIR = PROJECT_ROOT / "reports" / "ai"


def strip_leading_heading(section: str) -> str:
    """Remove the copied markdown heading so memos do not duplicate headings."""
    if not section:
        return ""

    lines = section.strip().splitlines()
    if lines and lines[0].startswith("## "):
        lines = lines[1:]

    return "\n".join(lines).strip()


def extract_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    if start == -1:
        return ""

    next_heading = text.find("\n## ", start + len(marker))
    if next_heading == -1:
        section = text[start:].strip()
    else:
        section = text[start:next_heading].strip()

    return strip_leading_heading(section)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()

    date_str = args.date
    AI_DIR.mkdir(parents=True, exist_ok=True)

    research_path = RESEARCH_DIR / f"nightly_findings_{date_str}.md"
    memo_path = AI_DIR / f"nightly_ai_memo_{date_str}.md"

    if not research_path.exists():
        raise FileNotFoundError(f"Missing research report: {research_path}")

    text = research_path.read_text(encoding="utf-8")

    live = extract_section(text, "Live Bot Boi Position Snapshot")
    experiments = extract_section(text, "Suggested Experiments")
    shadow = extract_section(text, "Strong Shadow Candidates")
    closed = extract_section(text, "Actual Bot Boi Closed Trade Snapshot")

    lines = []
    lines.append(f"# Ai Boi Memo: {date_str}")
    lines.append("")
    lines.append("## Main Read")
    lines.append("")
    lines.append("Bot Boi generated a completed nightly research packet. This memo is a compact review layer, not an automatic config change.")
    lines.append("")
    lines.append("## Actual Bot Boi")
    lines.append("")
    lines.append(closed if closed else "_No closed-trade section found._")
    lines.append("")
    lines.append("## Open Position Read")
    lines.append("")
    lines.append(live if live else "_No live-position section found._")
    lines.append("")
    lines.append("## Suggested Experiments")
    lines.append("")
    lines.append(experiments if experiments else "_No suggested experiments section found._")
    lines.append("")
    lines.append("## Shadow Watchlist")
    lines.append("")
    lines.append(shadow if shadow else "_No shadow-candidate section found._")
    lines.append("")
    lines.append("## Safety Reminder")
    lines.append("")
    lines.append("- Do not automatically edit config.yaml from this memo.")
    lines.append("- Treat suggestions as experiments to review, not commands.")
    lines.append("- Prefer several clean trading days before changing live behavior.")
    lines.append("")

    memo_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote Ai Boi memo: {memo_path}")


if __name__ == "__main__":
    main()
