# equity_store.py
import sqlite3
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime, timezone

DB_PATH = Path("equity.db")

class EquityDB:
    def __init__(self, path: Path = DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,             -- ISO8601 UTC
                equity REAL NOT NULL,
                cash REAL,
                positions_value REAL,
                pnl REAL
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(ts)")
        self.conn.commit()

    def insert_snapshot(self, equity: float, cash: Optional[float]=None,
                        positions_value: Optional[float]=None, pnl: Optional[float]=None):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.conn.execute(
            "INSERT INTO equity(ts,equity,cash,positions_value,pnl) VALUES(?,?,?,?,?)",
            (ts, equity, cash, positions_value, pnl)
        )
        self.conn.commit()

    def latest(self) -> Optional[Tuple[str,float,Optional[float],Optional[float],Optional[float]]]:
        cur = self.conn.execute(
            "SELECT ts,equity,cash,positions_value,pnl FROM equity ORDER BY id DESC LIMIT 1"
        )
        return cur.fetchone()

    def recent(self, limit:int=200) -> List[Tuple[str,float,Optional[float],Optional[float],Optional[float]]]:
        cur = self.conn.execute(
            "SELECT ts,equity,cash,positions_value,pnl FROM equity ORDER BY id DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()
