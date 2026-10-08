#!/usr/bin/env python3
"""
Weekend Ai Boi weekly research scrubber v1.

Reads:
  logs/trades/trades.csv
  logs/learning/bars_YYYY-MM-DD.csv
  logs/shadow/shadow-YYYY-MM-DD.csv
  reports/research/nightly_findings_YYYY-MM-DD.md

Writes:
  reports/weekly/weekly_research_YYYY-WW.md
  reports/weekly/weekly_actual_symbols_YYYY-WW.csv
  reports/weekly/weekly_shadow_candidates_YYYY-WW.csv
  reports/weekly/weekly_score_buckets_YYYY-WW.csv

Safe rule:
  This script does NOT edit config.yaml.
  This script does NOT place trades.
  This script only studies the week and writes reports.
"""

from __future__ import annotations

import argparse
import ast
import json
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

PROJECT_ROOT = Path(__file__).resolve().parents[1]

TRADES_CSV = PROJECT_ROOT / "logs" / "trades" / "trades.csv"
LEARNING_DIR = PROJECT_ROOT / "logs" / "learning"
SHADOW_DIR = PROJECT_ROOT / "logs" / "shadow"
RESEARCH_DIR = PROJECT_ROOT / "reports" / "research"
WEEKLY_DIR = PROJECT_ROOT / "reports" / "weekly"


def parse_extra(raw: Any) -> dict:
    if raw is None:
        return {}

    text = str(raw).strip()
    if not text:
        return {}

    try:
        return json.loads(text)
    except Exception:
        pass

    try:
        value = ast.literal_eval(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def money(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return "$0.00"
    return f"${float(x):,.2f}"


def pct(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{float(x) * 100:.2f}%"


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def most_recent_week(today: date | None = None) -> tuple[date, date]:
    """
    Returns Monday-Friday for the most recent trading-style week.

    If run on Saturday/Sunday, returns the week that just ended.
    If run during the week, returns the current Monday-Friday window.
    """
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)

    if today.weekday() >= 5:
        return monday, friday

    return monday, friday


def date_range(start: date, end: date) -> list[date]:
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def week_id(start: date) -> str:
    iso = start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def load_trades(start: date, end: date) -> pd.DataFrame:
    if not TRADES_CSV.exists():
        return pd.DataFrame()

    df = pd.read_csv(TRADES_CSV)
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame()

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"])

    start_ts = pd.Timestamp(start.isoformat(), tz="UTC")
    end_ts = pd.Timestamp((end + timedelta(days=1)).isoformat(), tz="UTC")

    df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] < end_ts)].copy()

    if df.empty:
        return df

    df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    df["symbol"] = df["symbol"].astype(str)
    df["event"] = df["event"].astype(str).str.upper()
    df["reason"] = df.get("reason", "").fillna("").astype(str)
    df["qty"] = pd.to_numeric(df.get("qty"), errors="coerce").fillna(0.0)
    df["price"] = pd.to_numeric(df.get("price"), errors="coerce").fillna(0.0)

    extras = df.get("extra", pd.Series(["{}"] * len(df))).apply(parse_extra)
    df["extra_dict"] = extras

    df["pnl_pct"] = extras.apply(lambda x: x.get("pnl_pct") if isinstance(x, dict) else None)
    df["avg_price"] = extras.apply(lambda x: x.get("avg_price") if isinstance(x, dict) else None)
    df["entry_score"] = extras.apply(lambda x: x.get("entry_score") if isinstance(x, dict) else None)

    df["pnl_pct"] = pd.to_numeric(df["pnl_pct"], errors="coerce")
    df["avg_price"] = pd.to_numeric(df["avg_price"], errors="coerce")
    df["entry_score"] = pd.to_numeric(df["entry_score"], errors="coerce")

    df["realized_pnl_dollars"] = 0.0
    sell_mask = (df["event"] == "SELL") & df["avg_price"].notna()
    df.loc[sell_mask, "realized_pnl_dollars"] = (
        (df.loc[sell_mask, "price"] - df.loc[sell_mask, "avg_price"])
        * df.loc[sell_mask, "qty"]
    )

    return df


def load_learning_for_day(day: date) -> pd.DataFrame:
    path = LEARNING_DIR / f"bars_{day.isoformat()}.csv"
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_csv(path)
    if df.empty:
        return df

    df["date"] = day.isoformat()

    for col in ["price", "entry_score", "position_qty", "avg_price", "pnl_pct"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["symbol", "signal_action", "order_action", "skip_reason"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    return df


def load_learning(start: date, end: date) -> pd.DataFrame:
    frames = [load_learning_for_day(d) for d in date_range(start, end)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_shadow_for_day(day: date) -> pd.DataFrame:
    path = SHADOW_DIR / f"shadow-{day.isoformat()}.csv"
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    try:
        df = pd.read_csv(path, low_memory=False)
    except EmptyDataError:
        return pd.DataFrame()

    if df.empty:
        return df

    df["date"] = day.isoformat()

    for col in [
        "open", "high", "low", "close", "volume",
        "next_ret_pct", "ema20", "ema50", "ema200", "vwap", "adx",
        "entry_score", "comp_cross_up", "comp_adx_ok", "comp_rsi_ok",
        "comp_close_gt_vwap", "comp_trend_ok",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["symbol", "signal_name", "action", "reason"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    return df


def load_shadow(start: date, end: date) -> pd.DataFrame:
    frames = [load_shadow_for_day(d) for d in date_range(start, end)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def make_actual_symbol_week(trades: pd.DataFrame, learning: pd.DataFrame) -> pd.DataFrame:
    symbols = sorted(
        set(trades["symbol"].unique() if not trades.empty and "symbol" in trades else [])
        | set(learning["symbol"].unique() if not learning.empty and "symbol" in learning else [])
    )

    rows = []

    for sym in symbols:
        t = trades[trades["symbol"] == sym] if not trades.empty and "symbol" in trades else pd.DataFrame()
        l = learning[learning["symbol"] == sym] if not learning.empty and "symbol" in learning else pd.DataFrame()

        sell_rows = t[t["event"] == "SELL"] if not t.empty and "event" in t else pd.DataFrame()

        buys = int((t["event"] == "BUY").sum()) if not t.empty and "event" in t else 0
        sells = int((t["event"] == "SELL").sum()) if not t.empty and "event" in t else 0
        realized = float(sell_rows["realized_pnl_dollars"].sum()) if not sell_rows.empty else 0.0

        wins = int((sell_rows["realized_pnl_dollars"] > 0).sum()) if not sell_rows.empty else 0
        losses = int((sell_rows["realized_pnl_dollars"] < 0).sum()) if not sell_rows.empty else 0

        buy_signals = int((l["signal_action"] == "buy").sum()) if not l.empty and "signal_action" in l else 0
        already_skips = int((l["skip_reason"] == "already_in_position").sum()) if not l.empty and "skip_reason" in l else 0

        avg_live_pnl_pct = (
            float(l["pnl_pct"].dropna().mean())
            if not l.empty and "pnl_pct" in l and l["pnl_pct"].notna().any()
            else None
        )

        last_live_pnl_pct = (
            float(l["pnl_pct"].dropna().iloc[-1])
            if not l.empty and "pnl_pct" in l and l["pnl_pct"].notna().any()
            else None
        )

        avg_entry_score = (
            float(l["entry_score"].dropna().mean())
            if not l.empty and "entry_score" in l and l["entry_score"].notna().any()
            else None
        )

        rows.append({
            "symbol": sym,
            "days_seen": int(l["date"].nunique()) if not l.empty and "date" in l else 0,
            "bars_logged": int(len(l)),
            "buys": buys,
            "sells": sells,
            "wins": wins,
            "losses": losses,
            "realized_pnl_dollars": realized,
            "avg_live_pnl_pct": avg_live_pnl_pct,
            "last_live_pnl_pct": last_live_pnl_pct,
            "avg_entry_score": avg_entry_score,
            "buy_signals": buy_signals,
            "already_in_position_skips": already_skips,
        })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values(
        ["realized_pnl_dollars", "last_live_pnl_pct", "buy_signals"],
        ascending=[False, False, False],
    )


def make_weekly_shadow_candidates(shadow: pd.DataFrame, limit: int = 30) -> pd.DataFrame:
    if shadow.empty:
        return pd.DataFrame()

    required = {"symbol", "action", "entry_score"}
    if not required.issubset(set(shadow.columns)):
        return pd.DataFrame()

    buys = shadow[(shadow["action"] == "buy") & shadow["entry_score"].notna()].copy()
    if buys.empty:
        return pd.DataFrame()

    grouped = buys.groupby("symbol").agg(
        days_seen=("date", "nunique"),
        buy_count=("action", "count"),
        avg_entry_score=("entry_score", "mean"),
        max_entry_score=("entry_score", "max"),
        avg_next_ret_pct=("next_ret_pct", "mean") if "next_ret_pct" in buys.columns else ("entry_score", "mean"),
        median_next_ret_pct=("next_ret_pct", "median") if "next_ret_pct" in buys.columns else ("entry_score", "median"),
        last_close=("close", "last") if "close" in buys.columns else ("entry_score", "last"),
        avg_adx=("adx", "mean") if "adx" in buys.columns else ("entry_score", "mean"),
    ).reset_index()

    grouped["shadow_strength"] = (
        grouped["buy_count"].fillna(0)
        * grouped["avg_entry_score"].fillna(0)
        * (1 + grouped["avg_next_ret_pct"].fillna(0).clip(lower=-0.05, upper=0.05))
    )

    grouped = grouped.sort_values(
        ["days_seen", "shadow_strength", "avg_entry_score"],
        ascending=[False, False, False],
    )

    return grouped.head(limit)


def make_score_buckets_weekly(learning: pd.DataFrame, shadow: pd.DataFrame) -> pd.DataFrame:
    outs = []

    for source_name, df in [("learning", learning), ("shadow", shadow)]:
        if df.empty or "entry_score" not in df.columns:
            continue

        clean = df[df["entry_score"].notna()].copy()
        if clean.empty:
            continue

        bins = [0.0, 0.25, 0.35, 0.50, 0.65, 0.80, 1.01]
        labels = ["0.00-0.25", "0.25-0.35", "0.35-0.50", "0.50-0.65", "0.65-0.80", "0.80-1.00"]
        clean["score_bucket"] = pd.cut(clean["entry_score"], bins=bins, labels=labels, include_lowest=True)

        group = clean.groupby("score_bucket", observed=False)

        out = group.agg(
            rows=("entry_score", "count"),
            avg_entry_score=("entry_score", "mean"),
        ).reset_index()

        out["source"] = source_name

        if "pnl_pct" in clean.columns:
            live_pnl = group["pnl_pct"].mean().reset_index(name="avg_live_pnl_pct")
            out = out.merge(live_pnl, on="score_bucket", how="left")

        if "next_ret_pct" in clean.columns:
            next_ret = group["next_ret_pct"].mean().reset_index(name="avg_next_ret_pct")
            out = out.merge(next_ret, on="score_bucket", how="left")

        outs.append(out)

    if not outs:
        return pd.DataFrame()

    return pd.concat(outs, ignore_index=True)


def make_weekly_suggestions(actual: pd.DataFrame, shadow_candidates: pd.DataFrame) -> list[str]:
    suggestions = []

    if not actual.empty:
        active = actual[actual["bars_logged"] > 0].copy()

        if not active.empty:
            leaders = active.sort_values(["realized_pnl_dollars", "last_live_pnl_pct"], ascending=[False, False]).head(3)
            laggards = active.sort_values(["realized_pnl_dollars", "last_live_pnl_pct"], ascending=[True, True]).head(3)

            leader_names = ", ".join(leaders["symbol"].astype(str).tolist())
            laggard_names = ", ".join(laggards["symbol"].astype(str).tolist())

            suggestions.append(
                f"Keep watch on weekly leaders: {leader_names}. They were the strongest actual Bot Boi symbols this week."
            )

            suggestions.append(
                f"Review weak actual symbols: {laggard_names}. Consider testing higher thresholds, tighter exits, or replacement only after confirming across more data."
            )

            skip_heavy = active.sort_values("already_in_position_skips", ascending=False).head(3)
            if not skip_heavy.empty and skip_heavy["already_in_position_skips"].max() > 50:
                names = ", ".join(skip_heavy["symbol"].astype(str).tolist())
                suggestions.append(
                    f"Study add-on/cooldown behavior for {names}. They had high already-in-position skips across the week."
                )

    if not shadow_candidates.empty:
        positive_shadow = shadow_candidates.copy()
        if "avg_next_ret_pct" in positive_shadow.columns:
            positive_shadow = positive_shadow[positive_shadow["avg_next_ret_pct"].fillna(0) > 0]

        top = positive_shadow.head(5)
        if not top.empty:
            names = ", ".join(top["symbol"].astype(str).tolist())
            suggestions.append(
                f"Next-week shadow watchlist: {names}. These appeared repeatedly as strong shadow candidates across the week."
            )

    if not suggestions:
        suggestions.append("No strong weekly recommendations yet. Collect another clean week before changing config.")

    return suggestions


def write_weekly_report(
    start: date,
    end: date,
    trades: pd.DataFrame,
    learning: pd.DataFrame,
    shadow: pd.DataFrame,
    actual: pd.DataFrame,
    shadow_candidates: pd.DataFrame,
    score_buckets: pd.DataFrame,
) -> Path:
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    wid = week_id(start)
    path = WEEKLY_DIR / f"weekly_research_{wid}.md"

    sell_rows = trades[trades["event"] == "SELL"] if not trades.empty and "event" in trades else pd.DataFrame()

    realized_total = float(sell_rows["realized_pnl_dollars"].sum()) if not sell_rows.empty else 0.0
    wins = int((sell_rows["realized_pnl_dollars"] > 0).sum()) if not sell_rows.empty else 0
    losses = int((sell_rows["realized_pnl_dollars"] < 0).sum()) if not sell_rows.empty else 0
    win_rate = wins / max(1, wins + losses)

    suggestions = make_weekly_suggestions(actual, shadow_candidates)

    lines = []
    lines.append(f"# Weekend Ai Boi Weekly Research: {wid}")
    lines.append("")
    lines.append(f"Week range: **{start.isoformat()} → {end.isoformat()}**")
    lines.append("")
    lines.append("## Safety")
    lines.append("")
    lines.append("- This report is research only.")
    lines.append("- No live config was changed.")
    lines.append("- No trades were placed by this script.")
    lines.append("")
    lines.append("## Data Loaded")
    lines.append("")
    lines.append(f"- Trade rows: **{len(trades):,}**")
    lines.append(f"- Learning rows: **{len(learning):,}**")
    lines.append(f"- Shadow rows: **{len(shadow):,}**")
    lines.append(f"- Actual symbols seen: **{actual['symbol'].nunique() if not actual.empty else 0:,}**")
    lines.append(f"- Shadow symbols seen: **{shadow['symbol'].nunique() if not shadow.empty and 'symbol' in shadow else 0:,}**")
    lines.append("")
    lines.append("## Closed Trade Week Snapshot")
    lines.append("")
    lines.append(f"- Realized P/L estimate: **{money(realized_total)}**")
    lines.append(f"- Closed winners / losers: **{wins} / {losses}**")
    lines.append(f"- Closed-trade win rate: **{win_rate * 100:.1f}%**")
    lines.append("")
    lines.append("## Actual Bot Boi Weekly Symbols")
    lines.append("")
    if actual.empty:
        lines.append("_No actual weekly symbol data available._")
    else:
        lines.append("| Symbol | Days | Bars | Realized P/L | Buys | Sells | Wins | Losses | Last Live P/L % | Avg Score | Buy Signals | In-Position Skips |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in actual.head(15).iterrows():
            avg_score = r.get("avg_entry_score")
            lines.append(
                f"| {r['symbol']} | {int(r['days_seen'])} | {int(r['bars_logged'])} | "
                f"{money(r['realized_pnl_dollars'])} | {int(r['buys'])} | {int(r['sells'])} | "
                f"{int(r['wins'])} | {int(r['losses'])} | {pct(r['last_live_pnl_pct'])} | "
                f"{'n/a' if pd.isna(avg_score) else f'{float(avg_score):.3f}'} | "
                f"{int(r['buy_signals'])} | {int(r['already_in_position_skips'])} |"
            )
    lines.append("")
    lines.append("## Weekly Shadow Candidates")
    lines.append("")
    if shadow_candidates.empty:
        lines.append("_No weekly shadow candidates found._")
    else:
        lines.append("| Symbol | Days | Buy Count | Avg Score | Max Score | Avg Next Ret % | Last Close | Avg ADX |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in shadow_candidates.head(20).iterrows():
            lines.append(
                f"| {r['symbol']} | {int(r['days_seen'])} | {int(r['buy_count'])} | "
                f"{float(r['avg_entry_score']):.3f} | {float(r['max_entry_score']):.3f} | "
                f"{'n/a' if pd.isna(r.get('avg_next_ret_pct')) else f'{float(r.get('avg_next_ret_pct')):.4f}'} | "
                f"{'n/a' if pd.isna(r.get('last_close')) else f'{float(r.get('last_close')):.2f}'} | "
                f"{'n/a' if pd.isna(r.get('avg_adx')) else f'{float(r.get('avg_adx')):.2f}'} |"
            )
    lines.append("")
    lines.append("## Suggested Next-Week Experiments")
    lines.append("")
    for i, s in enumerate(suggestions, start=1):
        lines.append(f"{i}. {s}")
    lines.append("")
    lines.append("## Weekend Scrub Notes")
    lines.append("")
    lines.append("- Weekly results are stronger than one-day results, but still not proof.")
    lines.append("- Treat suggested changes as paper-trading experiments, not commands.")
    lines.append("- Best next upgrade: pair buys and sells into full trade lifecycles across the week.")
    lines.append("- Future heavy mode: simulate alternate TP/SL, thresholds, cooldowns, and symbol swaps.")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Start date YYYY-MM-DD. Defaults to most recent Monday.")
    parser.add_argument("--end", help="End date YYYY-MM-DD. Defaults to same week Friday.")
    args = parser.parse_args()

    if args.start:
        start = parse_date(args.start)
        end = parse_date(args.end) if args.end else start + timedelta(days=4)
    else:
        start, end = most_recent_week()

    wid = week_id(start)

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)

    trades = load_trades(start, end)
    learning = load_learning(start, end)
    shadow = load_shadow(start, end)

    actual = make_actual_symbol_week(trades, learning)
    shadow_candidates = make_weekly_shadow_candidates(shadow)
    score_buckets = make_score_buckets_weekly(learning, shadow)

    actual_path = WEEKLY_DIR / f"weekly_actual_symbols_{wid}.csv"
    shadow_path = WEEKLY_DIR / f"weekly_shadow_candidates_{wid}.csv"
    buckets_path = WEEKLY_DIR / f"weekly_score_buckets_{wid}.csv"

    actual.to_csv(actual_path, index=False)
    shadow_candidates.to_csv(shadow_path, index=False)
    score_buckets.to_csv(buckets_path, index=False)

    report_path = write_weekly_report(
        start,
        end,
        trades,
        learning,
        shadow,
        actual,
        shadow_candidates,
        score_buckets,
    )

    print(f"Wrote weekly report:     {report_path}")
    print(f"Wrote actual symbols:    {actual_path}")
    print(f"Wrote shadow candidates: {shadow_path}")
    print(f"Wrote score buckets:     {buckets_path}")
    print(f"Week:                    {start.isoformat()} to {end.isoformat()}")
    print(f"Trades rows:             {len(trades):,}")
    print(f"Learning rows:           {len(learning):,}")
    print(f"Shadow rows:             {len(shadow):,}")


if __name__ == "__main__":
    main()
