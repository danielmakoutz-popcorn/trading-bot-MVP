#!/usr/bin/env python3
"""Check local stores, sizing helper, and log rotation without broker access."""
import ast
from contextlib import redirect_stderr
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(condition, label):
    if not condition:
        raise RuntimeError(label)
    print(f"PASS {label}")


def main():
    files = sorted([*ROOT.glob("*.py"), *(ROOT / "bot").rglob("*.py"),
                    *(ROOT / "scripts").glob("*.py")])
    for path in files:
        ast.parse(path.read_text(), filename=str(path.relative_to(ROOT)))
    print(f"PASS source syntax: {len(files)} Python files (including this validator)")

    from bot.core.db import DB
    from bot.core.logs import setup_logging
    from bot.core.risk import position_size
    from equity_store import EquityDB

    with TemporaryDirectory(prefix="bot-validation-") as temporary:
        root = Path(temporary)
        equity_path = root / "equity.db"
        equity = EquityDB(equity_path)
        check(equity.latest() is None, "new equity store has no snapshot")
        equity.insert_snapshot(100000.0, 90000.0, 10000.0, 0.0)
        equity.insert_snapshot(100100.0, 90000.0, 10100.0, 100.0)
        equity.conn.close()
        equity = EquityDB(equity_path)
        check(equity.latest()[1:] == (100100.0, 90000.0, 10100.0, 100.0)
              and len(equity.recent()) == 2,
              "two synthetic equity snapshots survive closing and reopening SQLite")
        equity.conn.close()

        database_path = root / "marketbot.db"
        database = DB(str(database_path))
        timestamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
        database.insert_equity(timestamp, 100000.0)
        database.insert_trade(timestamp, "SYNTHETIC", "buy", 1.0, 100.0, 0.0)
        database.upsert_position("SYNTHETIC", 1.0, 100.0)
        database.upsert_position("SYNTHETIC", 2.0, 101.0)
        database.close()
        database = DB(str(database_path))
        check(database.get_positions() == {"SYNTHETIC": {"qty": 2.0, "avg_price": 101.0}}
              and len(database.fetch_trades()) == 1 and len(database.fetch_equity()) == 1,
              "trade, equity, and updated position survive reopening without a duplicate position")
        database.close()

        check(position_size(10000.0, 0.05, 100.0) == 5.0
              and position_size(10000.0, 0.05, 0.0) == 0.0
              and position_size(10000.0, -0.05, 100.0) == 0.0,
              "sizing helper handles positive sizing, zero price, and negative allocation")

        with redirect_stderr(StringIO()):
            logger = setup_logging({"logging": {"dir": str(root / "logs"),
                                    "file": "validation.log", "max_bytes": 180, "backup_count": 2}})
            try:
                for number in range(10):
                    logger.info("Synthetic rotation record %s %s", number, "x" * 50)
                for handler in logger.handlers:
                    handler.flush()
                paths = sorted((root / "logs").glob("validation.log*"))
                check(len(paths) == 3 and (root / "logs" / "validation.log.2").is_file(),
                      "configured rotation retains the active log and two backup files")
            finally:
                for handler in list(logger.handlers):
                    handler.close()
                    logger.removeHandler(handler)

    print("COMPLETE offline component checks; engine, API, and broker were not started")


if __name__ == "__main__":
    main()
