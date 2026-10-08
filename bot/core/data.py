from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Deque
from collections import deque
from datetime import datetime, timedelta, timezone
import pandas as pd

@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

class DataProvider:
    async def next_candle(self) -> Optional[Candle]:
        raise NotImplementedError

class YahooProvider(DataProvider):
    def __init__(self, symbol: str, lookback: int = 200):
        import yfinance as yf
        self.yf = yf
        self.symbol = symbol

    def _fetch_latest_minute(self) -> Optional[Candle]:
        import pandas as pd
        try:
            df = self.yf.download(self.symbol, period="1d", interval="1m", progress=False, prepost=False, auto_adjust=False, threads=False,)
        except Exception:
            df = pd.DataFrame()

        if df is None  or df.empty:
            try:
                df = self.yf.download(self.symbol, period="2d", interval="1m", progress=False, prepost=False, auto_adjust=False, threads=False,)
            except Exception:
                df = pd.DataFrame()

        if df is None or df.empty:
            return None

        last = df.iloc[-1]
        ts = pd.Timestamp(last.name).to_pydatetime()
        print(f"DEBUG: Latest candle timestamp: {ts}")
        return Candle(ts=ts, open=float(last["open"]), high=float(last["high"]), low=float(last["low"]), close=float(last["close"]), volume=float(last["volume"]),)

    async def next_candle(self) -> Optional[Candle]:
        c = self._fetch_latest_minute()

class CSVProvider(DataProvider):
    def __init__(self, path: str):
        self.df = pd.read_csv(path)
        # Expect either ISO strings or epoch; attempt parse
        if "timestamp" in self.df.columns:
            self.df["ts"] = pd.to_datetime(self.df["timestamp"], utc=True)
        elif "ts" in self.df.columns:
            self.df["ts"] = pd.to_datetime(self.df["ts"], utc=True)
        else:
            raise ValueError("CSV must have a 'timestamp' or 'ts' column")

        self.df = self.df.sort_values("ts")
        self.idx = 0

    async def next_candle(self) -> Optional[Candle]:
        if self.idx >= len(self.df):
            return None
        row = self.df.iloc[self.idx]
        self.idx += 1
        ts = row["ts"].to_pydatetime()
        return Candle(
            ts=ts,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
