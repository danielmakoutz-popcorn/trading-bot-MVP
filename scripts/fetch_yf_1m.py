import argparse
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

def main(symbol: str, days: int, out_path: str):

    # 1-minute bars; Yahoo keeps ~30 days of 1m data
    df = yf.download(symbol, period="30d", interval="1m", auto_adjust=False, progress=False)

    if df.empty:
        raise SystemExit(f"No data returned for {symbol}. Try fewer days or check symbol.")

    # Normalize columns to your common format
    df = df.reset_index()  # Datetime index -> column
    df = df.rename(columns={
        "Datetime": "ts",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    })
    df = df[["ts", "open", "high", "low", "close", "volume"]]

    df.to_csv(out_path, index=False)
    print(f"Saved {len(df):,} rows to {out_path}")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="AAPL")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--out", default="data/AAPL_1m_last30d.csv")
    args = p.parse_args()
    main(args.symbol, args.days, args.out)

