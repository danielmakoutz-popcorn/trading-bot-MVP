#!/usr/bin/env python3
"""
Daily Bot Boi summary report.

Reads:
  logs/trades/trades.csv
  logs/learning/bars_YYYY-MM-DD.csv

Writes:
  reports/daily/daily_report_YYYY-MM-DD.md
  reports/daily/daily_stats_YYYY-MM-DD.csv
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRADES_CSV = PROJECT_ROOT / "logs" / "trades" / "trades.csv"
LEARNING_DIR = PROJECT_ROOT / "logs" / "learning"
REPORT_DIR = PROJECT_ROOT / "reports" / "daily"


def parse_extra(raw: Any) -> dict:
    """Parse the extra column from trades.csv safely."""
    if raw is None:
        return {}

    text = str(raw).strip()
    if not text:
        return {}

    # Preferred: valid JSON.
    try:
        return json.loads(text)
    except Exception:
        pass

    # Fallback for older rows that may look like Python dict strings.
    try:
        value = ast.literal_eval(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def load_trades(date_str: str) -> pd.DataFrame:
    if not TRADES_CSV.exists():
        return pd.DataFrame()

    df = pd.read_csv(TRADES_CSV)
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame()

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"])
    df = df[df["timestamp"].dt.strftime("%Y-%m-%d") == date_str].copy()

    if df.empty:
        return df

    df["symbol"] = df["symbol"].astype(str)
    df["event"] = df["event"].astype(str).str.upper()
    df["qty"] = pd.to_numeric(df.get("qty"), errors="coerce").fillna(0.0)
    df["price"] = pd.to_numeric(df.get("price"), errors="coerce").fillna(0.0)
    df["reason"] = df.get("reason", "").fillna("").astype(str)

    extras = df.get("extra", pd.Series(["{}"] * len(df))).apply(parse_extra)
    df["extra_dict"] = extras

    df["pnl_pct"] = extras.apply(lambda x: x.get("pnl_pct") if isinstance(x, dict) else None)
    df["avg_price"] = extras.apply(lambda x: x.get("avg_price") if isinstance(x, dict) else None)
    df["entry_score"] = extras.apply(lambda x: x.get("entry_score") if isinstance(x, dict) else None)
    df["order_id"] = extras.apply(lambda x: x.get("order_id") if isinstance(x, dict) else "")

    df["pnl_pct"] = pd.to_numeric(df["pnl_pct"], errors="coerce")
    df["avg_price"] = pd.to_numeric(df["avg_price"], errors="coerce")
    df["entry_score"] = pd.to_numeric(df["entry_score"], errors="coerce")

    # Realized P/L estimate for SELL rows when avg_price is available.
    df["realized_pnl_dollars"] = 0.0
    sell_mask = (df["event"] == "SELL") & df["avg_price"].notna()
    df.loc[sell_mask, "realized_pnl_dollars"] = (
        (df.loc[sell_mask, "price"] - df.loc[sell_mask, "avg_price"])
        * df.loc[sell_mask, "qty"]
    )

    return df


def load_learning(date_str: str) -> pd.DataFrame:
    path = LEARNING_DIR / f"bars_{date_str}.csv"
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_csv(path)
    if df.empty:
        return df

    for col in ["price", "entry_score", "position_qty", "avg_price", "pnl_pct"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["tp_hit", "sl_hit"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().isin(["true", "1", "yes"])

    for col in ["symbol", "signal_action", "order_action", "skip_reason"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    return df


def safe_money(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return "$0.00"
    return f"${float(x):,.2f}"


def safe_pct(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{float(x) * 100:.2f}%"


def make_symbol_stats(trades: pd.DataFrame, learning: pd.DataFrame) -> pd.DataFrame:
    symbols = sorted(
        set(trades["symbol"].unique() if not trades.empty else [])
        | set(learning["symbol"].unique() if not learning.empty and "symbol" in learning.columns else [])
    )

    rows = []
    for sym in symbols:
        t = trades[trades["symbol"] == sym] if not trades.empty else pd.DataFrame()
        l = learning[learning["symbol"] == sym] if not learning.empty else pd.DataFrame()

        buys = int((t["event"] == "BUY").sum()) if not t.empty else 0
        sells = int((t["event"] == "SELL").sum()) if not t.empty else 0

        sell_rows = t[t["event"] == "SELL"] if not t.empty else pd.DataFrame()
        realized = float(sell_rows["realized_pnl_dollars"].sum()) if not sell_rows.empty else 0.0

        wins = int((sell_rows["realized_pnl_dollars"] > 0).sum()) if not sell_rows.empty else 0
        losses = int((sell_rows["realized_pnl_dollars"] < 0).sum()) if not sell_rows.empty else 0

        avg_sell_pnl_pct = (
            float(sell_rows["pnl_pct"].dropna().mean())
            if not sell_rows.empty and sell_rows["pnl_pct"].notna().any()
            else None
        )

        bars = int(len(l)) if not l.empty else 0
        buy_signals = int((l["signal_action"] == "buy").sum()) if not l.empty and "signal_action" in l else 0
        already_in_position = int((l["skip_reason"] == "already_in_position").sum()) if not l.empty and "skip_reason" in l else 0

        last_unrealized = (
            float(l["pnl_pct"].dropna().iloc[-1])
            if not l.empty and "pnl_pct" in l and l["pnl_pct"].notna().any()
            else None
        )

        rows.append({
            "symbol": sym,
            "bars_logged": bars,
            "buys": buys,
            "sells": sells,
            "wins": wins,
            "losses": losses,
            "realized_pnl_dollars": realized,
            "avg_sell_pnl_pct": avg_sell_pnl_pct,
            "last_unrealized_pnl_pct": last_unrealized,
            "buy_signals": buy_signals,
            "already_in_position_skips": already_in_position,
        })

    return pd.DataFrame(rows).sort_values("realized_pnl_dollars", ascending=False)


def write_report(date_str: str, trades: pd.DataFrame, learning: pd.DataFrame, stats: pd.DataFrame) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"daily_report_{date_str}.md"

    total_buys = int((trades["event"] == "BUY").sum()) if not trades.empty else 0
    total_sells = int((trades["event"] == "SELL").sum()) if not trades.empty else 0
    sell_rows = trades[trades["event"] == "SELL"] if not trades.empty else pd.DataFrame()

    realized_total = float(sell_rows["realized_pnl_dollars"].sum()) if not sell_rows.empty else 0.0
    winners = int((sell_rows["realized_pnl_dollars"] > 0).sum()) if not sell_rows.empty else 0
    losers = int((sell_rows["realized_pnl_dollars"] < 0).sum()) if not sell_rows.empty else 0
    win_rate = winners / max(1, winners + losers)

    tp_sells = int((sell_rows["reason"] == "tp_hit").sum()) if not sell_rows.empty else 0
    sl_sells = int((sell_rows["reason"] == "sl_hit").sum()) if not sell_rows.empty else 0

    learning_rows = int(len(learning)) if not learning.empty else 0
    buy_signals = int((learning["signal_action"] == "buy").sum()) if not learning.empty and "signal_action" in learning else 0
    already_skips = int((learning["skip_reason"] == "already_in_position").sum()) if not learning.empty and "skip_reason" in learning else 0

    best_symbols = stats.head(5)
    worst_symbols = stats.tail(5).sort_values("realized_pnl_dollars", ascending=True)

    lines = []
    lines.append(f"# Bot Boi Daily Summary: {date_str}")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Realized P/L estimate: **{safe_money(realized_total)}**")
    lines.append(f"- Buys: **{total_buys}**")
    lines.append(f"- Sells: **{total_sells}**")
    lines.append(f"- Winners / losers: **{winners} / {losers}**")
    lines.append(f"- Win rate on closed sells: **{win_rate * 100:.1f}%**")
    lines.append(f"- TP sells: **{tp_sells}**")
    lines.append(f"- SL sells: **{sl_sells}**")
    lines.append(f"- Learning rows logged: **{learning_rows:,}**")
    lines.append(f"- Buy signals in learning log: **{buy_signals:,}**")
    lines.append(f"- Already-in-position skips: **{already_skips:,}**")
    lines.append("")

    lines.append("## Best Symbols by Realized P/L")
    lines.append("")
    if best_symbols.empty:
        lines.append("_No symbol stats available._")
    else:
        lines.append("| Symbol | Realized P/L | Buys | Sells | Wins | Losses | Avg Sell P/L % |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for _, r in best_symbols.iterrows():
            lines.append(
                f"| {r['symbol']} | {safe_money(r['realized_pnl_dollars'])} | "
                f"{int(r['buys'])} | {int(r['sells'])} | {int(r['wins'])} | {int(r['losses'])} | "
                f"{safe_pct(r['avg_sell_pnl_pct'])} |"
            )
    lines.append("")

    lines.append("## Worst Symbols by Realized P/L")
    lines.append("")
    if worst_symbols.empty:
        lines.append("_No symbol stats available._")
    else:
        lines.append("| Symbol | Realized P/L | Buys | Sells | Wins | Losses | Avg Sell P/L % |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for _, r in worst_symbols.iterrows():
            lines.append(
                f"| {r['symbol']} | {safe_money(r['realized_pnl_dollars'])} | "
                f"{int(r['buys'])} | {int(r['sells'])} | {int(r['wins'])} | {int(r['losses'])} | "
                f"{safe_pct(r['avg_sell_pnl_pct'])} |"
            )
    lines.append("")

    lines.append("## Notes for Future AI")
    lines.append("")
    lines.append("- Realized P/L is estimated from sell price minus broker average price stored in trade extras.")
    lines.append("- This report does not yet match buys to sells into full trade pairs.")
    lines.append("- Open overnight positions are not fully summarized yet.")
    lines.append("- Next upgrade: pair entries/exits, track max favorable/adverse excursion, and compare alternate TP/SL settings.")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Date in YYYY-MM-DD format")
    args = parser.parse_args()

    date_str = args.date

    trades = load_trades(date_str)
    learning = load_learning(date_str)
    stats = make_symbol_stats(trades, learning)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    stats_path = REPORT_DIR / f"daily_stats_{date_str}.csv"
    stats.to_csv(stats_path, index=False)

    report_path = write_report(date_str, trades, learning, stats)

    print(f"Wrote report: {report_path}")
    print(f"Wrote stats:  {stats_path}")
    print(f"Trades loaded: {len(trades)}")
    print(f"Learning rows loaded: {len(learning)}")


if __name__ == "__main__":
    main()
