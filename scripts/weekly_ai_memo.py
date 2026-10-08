#!/usr/bin/env python3
"""
Weekend Ai Boi memo v1.

Reads:
  reports/weekly/weekly_research_YYYY-WW.md

Writes:
  reports/weekly/weekly_ai_memo_YYYY-WW.md

This is still rule/template-based, not an LLM yet.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = PROJECT_ROOT / "reports" / "weekly"


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def most_recent_week(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


def week_id(start: date) -> str:
    iso = start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


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
    parser.add_argument("--start", help="Start date YYYY-MM-DD. Defaults to most recent Monday.")
    parser.add_argument("--end", help="End date YYYY-MM-DD. Currently only used for display compatibility.")
    args = parser.parse_args()

    if args.start:
        start = parse_date(args.start)
    else:
        start, _end = most_recent_week()

    wid = week_id(start)

    report_path = WEEKLY_DIR / f"weekly_research_{wid}.md"
    memo_path = WEEKLY_DIR / f"weekly_ai_memo_{wid}.md"

    if not report_path.exists():
        raise FileNotFoundError(f"Missing weekly report: {report_path}")

    text = report_path.read_text(encoding="utf-8")

    closed = extract_section(text, "Closed Trade Week Snapshot")
    actual = extract_section(text, "Actual Bot Boi Weekly Symbols")
    shadow = extract_section(text, "Weekly Shadow Candidates")
    experiments = extract_section(text, "Suggested Next-Week Experiments")
    notes = extract_section(text, "Weekend Scrub Notes")

    lines = []
    lines.append(f"# Weekend Ai Boi Memo: {wid}")
    lines.append("")
    lines.append("## Main Read")
    lines.append("")
    lines.append("This is the weekend strategy-council layer. It reviews the week, summarizes actual Bot Boi behavior, and surfaces next-week experiment ideas.")
    lines.append("")
    lines.append("## Actual Closed Trades")
    lines.append("")
    lines.append(closed if closed else "_No closed-trade section found._")
    lines.append("")
    lines.append("## Actual Weekly Symbols")
    lines.append("")
    lines.append(actual if actual else "_No actual weekly symbol section found._")
    lines.append("")
    lines.append("## Shadow Candidates")
    lines.append("")
    lines.append(shadow if shadow else "_No weekly shadow section found._")
    lines.append("")
    lines.append("## Next-Week Experiment Ideas")
    lines.append("")
    lines.append(experiments if experiments else "_No suggested experiments found._")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(notes if notes else "_No weekend notes found._")
    lines.append("")
    lines.append("## Safety Reminder")
    lines.append("")
    lines.append("- Do not automatically edit config.yaml from this memo.")
    lines.append("- Use this to choose paper-trading experiments.")
    lines.append("- Prefer one or two changes at a time so results stay readable.")
    lines.append("")

    memo_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote weekly Ai Boi memo: {memo_path}")


if __name__ == "__main__":
    main()

