# bar_check.py
# Quick sanity test for Alpaca bars using the free IEX feed (paper accounts)

import os, sys, datetime as dt
from zoneinfo import ZoneInfo
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# --- Env (Alpaca uses these names) ---
KEY    = os.getenv("APCA_API_KEY_ID", "")
SECRET = os.getenv("APCA_API_SECRET_KEY", "")

# Default symbols if none are passed on the command line
symbols = sys.argv[1].split(",") if len(sys.argv) > 1 else ["SPY", "AAPL"]

if not KEY or not SECRET:
    print("!! Missing APCA_API_KEY_ID / APCA_API_SECRET_KEY in environment.")
    raise SystemExit(1)

client = StockHistoricalDataClient(KEY, SECRET)

# Time range (timezone-aware UTC to avoid deprecation)
now = dt.datetime.now(ZoneInfo("UTC"))
start = now - dt.timedelta(days=2)

# Build request: force IEX (free plan) to avoid SIP 403s
req = StockBarsRequest(
    symbol_or_symbols=symbols,
    timeframe=TimeFrame.Minute,
    start=start,
    end=now,
    feed="iex"
)

try:
    bars = client.get_stock_bars(req)
except Exception as e:
    print("Request failed:", repr(e))
    raise SystemExit(2)

def get_series_for(sym):
    """Handle both dict-like and attribute/response styles safely."""
    try:
        # dict-style (common)
        s = bars.get(sym, [])
    except AttributeError:
        # newer alpaca-py returns objects with .data dict
        data = getattr(bars, "data", {}) or {}
        s = data.get(sym, [])
    return s

ok = True
print(f"UTC now: {now.isoformat()}")
for sym in symbols:
    series = get_series_for(sym)
    n = len(series)
    print(f"\n== {sym} == count={n}")
    if not n:
        ok = False
        print("No bars returned. Check symbol/permissions or feed.")
        continue

    # print last 5 bars
    tail = series[-5:] if n >= 5 else series
    for r in tail:
        print(f"{r.timestamp.isoformat()} O={r.open:.2f} H={r.high:.2f} L={r.low:.2f} C={r.close:.2f} V={r.volume}")

    # freshness
    last_ts = series[-1].timestamp
    age_h = (now - last_ts).total_seconds() / 3600.0
    print(f"[freshness] last bar age ≈ {age_h:.1f} hours")

print("\nRESULT:", "✅ Bars OK" if ok else "⚠️ Some symbols returned no data")
