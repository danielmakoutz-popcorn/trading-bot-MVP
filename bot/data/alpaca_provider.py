# at top of the file make sure these exist:
import pandas as pd
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from os import getenv
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from pandas import MultiIndex

@dataclass
class Candle:
    ts: datetime   # tz-aware UTC
    close: float

class AlpacaProvider:
    def __init__(self, symbol: str,
            feed: str = "iex",
            timeframe: str = "1Min"):

        api_key = getenv("APCA_API_KEY_ID")
        api_secret = getenv("APCA_API_SECRET_KEY")
        if not api_key or not api_secret:
            raise ValueError("Missing env: Key or secret")

        self.symbol = symbol
        self.feed = DataFeed.IEX if feed.lower() == "iex" else DataFeed.SIP
        print(f"(DATA) {self.symbol} using feed={self.feed.name}")
        self.timeframe = TimeFrame.Minute if timeframe.lower() in ["1min", "1m"] else TimeFrame.Day
        self.client = StockHistoricalDataClient(api_key, api_secret)
        self._last_ts: Optional[datetime] = None

    def history(self, n: int = 300, start=None, end=None):
        """
        Return a UTC-indexed pandas DataFrame (OHLCV) for self.symbol.
        Handles Alpaca's MultiIndex, normalizes tz to UTC, and sets self._last_ts.
        """

        # Default window if caller didn't specify one
        now_utc = datetime.now(timezone.utc)
        if start is None and end is None:
            span = max(n * 2, 600)                 # generous buffer => >= n rows
            start = now_utc - timedelta(minutes=span)
            end = now_utc

        # Build request using provider's own settings
        req = StockBarsRequest(
            symbol_or_symbols=[self.symbol],
            timeframe=self.timeframe,
            start=start,
            end=end,
            limit=n,
            feed=self.feed,                         # IEX on free plan
            adjustment="raw",
        )

        # Fetch bars (sync client)
        bars = self.client.get_stock_bars(req)
        df = getattr(bars, "df", None)
        if df is None or df.empty:
            return None

        # ---- Normalize index -> UTC and simplify to DatetimeIndex ----
        if isinstance(df.index, MultiIndex):
            # Get timestamp level safely (named "timestamp" on Alpaca)
            lvl_names = df.index.names or []
            ts = df.index.get_level_values("timestamp") if "timestamp" in lvl_names \
                 else df.index.get_level_values(-1)

            # Ensure UTC
            ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")

            # Drop the symbol level (we already know self.symbol)
            df = df.droplevel(0)
            df.index = ts
        else:
            # Single DatetimeIndex path
            idx = df.index
            df.index = idx.tz_localize("UTC") if getattr(idx, "tz", None) is None else idx.tz_convert("UTC")

        df = df.sort_index()

        # Keep standard columns only if present
        keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep]

        # Update last-ts for next_candle() to continue smoothly
        self._last_ts = df.index[-1]

        return df

    async def next_candle(self) -> Optional[Candle]:

        now_utc = datetime.now(timezone.utc)

        # Only look back a few minutes; not 90 or 500 bars every loop
        if self._last_ts is None:
            start = now_utc - timedelta(minutes=5)
        else:
            start = self._last_ts - timedelta(minutes=1)

        req = StockBarsRequest(
            symbol_or_symbols=[self.symbol],
            timeframe=TimeFrame.Minute,
            start=start,
            end=now_utc,
            limit=5,
            feed="iex",
            adjustment="raw",
        )

        bars = self.client.get_stock_bars(req)
        df = getattr(bars, "df", None)

        if df is None or df.empty:
            # optional: comment out this line if you want silence during off-hours
            print(f"[DATA] Empty df from provider for {self.symbol}")
            return None

        # Normalize the index
        if isinstance(df.index, pd.MultiIndex):
            df.index = df.index.get_level_values("timestamp")
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df = df.sort_index()
        print(f"[DATA DEBUG] {self.symbol} last 3 bars:\n{df.tail(3)}")

        # Filter for new bars only
        cutoff = self._last_ts or datetime.min.replace(tzinfo=timezone.utc)
        new_df = df[df.index > cutoff]

        if new_df.empty:
            return None

        # Grab the latest bar only
        ts = new_df.index[-1]
        close = float(new_df["close"].iloc[-1])
        self._last_ts = ts

        print(f"[DEBUG BAR] {self.symbol} {ts.isoformat()} close={close}")
        return Candle(ts=ts, close=close)



