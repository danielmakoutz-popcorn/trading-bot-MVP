import sqlite3
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime

class DB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init()

    def _init(self):
        cur = self.conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                equity REAL NOT NULL
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,      -- 'buy' / 'sell'
                qty REAL NOT NULL,
                price REAL NOT NULL,
                pnl REAL NOT NULL DEFAULT 0.0
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                qty REAL NOT NULL,
                avg_price REAL NOT NULL
            )"""
        )
        self.conn.commit()

    def insert_equity(self, ts: datetime, equity: float):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO equity_snapshots (ts, equity) VALUES (?, ?)", (ts.isoformat(), float(equity)))
        self.conn.commit()

    def insert_trade(self, ts: datetime, symbol: str, side: str, qty: float, price: float, pnl: float):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO trades (ts, symbol, side, qty, price, pnl) VALUES (?, ?, ?, ?, ?, ?)",
            (ts.isoformat(), symbol, side, float(qty), float(price), float(pnl)),
        )
        self.conn.commit()

    def upsert_position(self, symbol: str, qty: float, avg_price: float):
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO positions (symbol, qty, avg_price) VALUES (?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg_price=excluded.avg_price""",
            (symbol, float(qty), float(avg_price)),
        )
        self.conn.commit()

    def get_positions(self) -> Dict[str, Dict[str, float]]:
        cur = self.conn.cursor()
        cur.execute("SELECT symbol, qty, avg_price FROM positions")
        res = {}
        for sym, qty, avg in cur.fetchall():
            res[sym] = {"qty": qty, "avg_price": avg}
        return res

    def fetch_equity(self, limit: int = 500) -> List[Tuple[str, float]]:
        cur = self.conn.cursor()
        cur.execute("SELECT ts, equity FROM equity_snapshots ORDER BY id DESC LIMIT ?", (int(limit),))
        rows = cur.fetchall()
        rows.reverse()
        return rows

    def fetch_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT ts, symbol, side, qty, price, pnl FROM trades ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        rows = cur.fetchall()
        res = []
        for r in rows:
            res.append({
                "ts": r[0], "symbol": r[1], "side": r[2],
                "qty": float(r[3]), "price": float(r[4]), "pnl": float(r[5])
            })
        res.reverse()
        return res

    def close(self):
        self.conn.close()
