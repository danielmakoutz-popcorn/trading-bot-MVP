#!/usr/bin/env python3
"""
AI Boi experiment logger v1.

Creates and updates a simple experiment log:
  experiments/experiment_log.csv

Safety:
  - Does NOT edit config.yaml.
  - Does NOT place trades.
  - Only records what experiment you decided to run.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = PROJECT_ROOT / "experiments"
EXPERIMENT_LOG = EXPERIMENT_DIR / "experiment_log.csv"

FIELDS = [
    "created_at",
    "experiment_name",
    "status",
    "symbols_removed",
    "symbols_added",
    "reason",
    "source_report",
    "start_date",
    "end_date",
    "result_notes",
]


def ensure_log() -> None:
    EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    if not EXPERIMENT_LOG.exists():
        with EXPERIMENT_LOG.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()


def add_experiment(args: argparse.Namespace) -> None:
    ensure_log()

    row = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "experiment_name": args.name,
        "status": args.status,
        "symbols_removed": args.remove or "",
        "symbols_added": args.add or "",
        "reason": args.reason or "",
        "source_report": args.source_report or "",
        "start_date": args.start_date or "",
        "end_date": args.end_date or "",
        "result_notes": args.notes or "",
    }

    with EXPERIMENT_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writerow(row)

    print(f"Logged experiment: {args.name}")
    print(f"File: {EXPERIMENT_LOG}")


def list_experiments(_: argparse.Namespace) -> None:
    ensure_log()
    print(EXPERIMENT_LOG.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Log AI Boi/Bot Boi experiments safely.")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add a new experiment row")
    add.add_argument("--name", required=True, help="Experiment name")
    add.add_argument("--status", default="planned", choices=["planned", "active", "complete", "paused", "cancelled"])
    add.add_argument("--remove", default="", help="Symbols removed, comma-separated")
    add.add_argument("--add", default="", help="Symbols added, comma-separated")
    add.add_argument("--reason", default="", help="Why you are running the experiment")
    add.add_argument("--source-report", default="", help="Report path or memo that inspired it")
    add.add_argument("--start-date", default="", help="YYYY-MM-DD")
    add.add_argument("--end-date", default="", help="YYYY-MM-DD")
    add.add_argument("--notes", default="", help="Result notes or extra context")
    add.set_defaults(func=add_experiment)

    show = sub.add_parser("list", help="Print the experiment log")
    show.set_defaults(func=list_experiments)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
