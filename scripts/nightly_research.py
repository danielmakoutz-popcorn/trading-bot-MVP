#!/usr/bin/env python3
"""
Nightly Bot Boi research report v1.

Reads:
  logs/trades/trades.csv
  logs/learning/bars_YYYY-MM-DD.csv
  logs/shadow/shadow-YYYY-MM-DD.csv
  reports/daily/daily_report_YYYY-MM-DD.md

Writes:
  reports/research/nightly_findings_YYYY-MM-DD.md
  reports/research/symbol_rankings_YYYY-MM-DD.csv
  reports/research/score_buckets_YYYY-MM-DD.csv
  reports/research/shadow_candidates_YYYY-MM-DD.csv

Safe rule:
  This script does NOT edit config.yaml.
  This script does NOT place trades.
  This script only studies and writes reports.
"""

from __future__ import annotations

import argparse
import ast
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]

TRADES_CSV = PROJECT_ROOT / "logs" / "trades" / "trades.csv"
LEARNING_DIR = PROJECT_ROOT / "logs" / "learning"
SHADOW_DIR = PROJECT_ROOT / "logs" / "shadow"
DAILY_REPORT_DIR = PROJECT_ROOT / "reports" / "daily"
RESEARCH_DIR = PROJECT_ROOT / "reports" / "research"


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

    for col in ["symbol", "signal_action", "order_action", "skip_reason"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    return df


def load_shadow(date_str: str) -> pd.DataFrame:
    path = SHADOW_DIR / f"shadow-{date_str}.csv"
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_csv(path)
    if df.empty:
        return df

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


def money(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return "$0.00"
    return f"${float(x):,.2f}"


def pct(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{float(x) * 100:.2f}%"


def make_symbol_rankings(trades: pd.DataFrame, learning: pd.DataFrame, shadow: pd.DataFrame) -> pd.DataFrame:
    symbols = sorted(
        set(trades["symbol"].unique() if not trades.empty and "symbol" in trades else [])
        | set(learning["symbol"].unique() if not learning.empty and "symbol" in learning else [])
    )

    rows = []

    for sym in symbols:
        t = trades[trades["symbol"] == sym] if not trades.empty and "symbol" in trades else pd.DataFrame()
        l = learning[learning["symbol"] == sym] if not learning.empty and "symbol" in learning else pd.DataFrame()
        s = shadow[shadow["symbol"] == sym] if not shadow.empty and "symbol" in shadow else pd.DataFrame()

        sell_rows = t[t["event"] == "SELL"] if not t.empty and "event" in t else pd.DataFrame()

        realized = float(sell_rows["realized_pnl_dollars"].sum()) if not sell_rows.empty else 0.0
        buys = int((t["event"] == "BUY").sum()) if not t.empty and "event" in t else 0
        sells = int((t["event"] == "SELL").sum()) if not t.empty and "event" in t else 0
        wins = int((sell_rows["realized_pnl_dollars"] > 0).sum()) if not sell_rows.empty else 0
        losses = int((sell_rows["realized_pnl_dollars"] < 0).sum()) if not sell_rows.empty else 0

        buy_signals = int((l["signal_action"] == "buy").sum()) if not l.empty and "signal_action" in l else 0
        already_skips = int((l["skip_reason"] == "already_in_position").sum()) if not l.empty and "skip_reason" in l else 0

        shadow_buys = int((s["action"] == "buy").sum()) if not s.empty and "action" in s else 0
        shadow_avg_score = float(s["entry_score"].dropna().mean()) if not s.empty and "entry_score" in s and s["entry_score"].notna().any() else None
        shadow_avg_next_ret = float(s["next_ret_pct"].dropna().mean()) if not s.empty and "next_ret_pct" in s and s["next_ret_pct"].notna().any() else None

        rows.append({
            "symbol": sym,
            "realized_pnl_dollars": realized,
            "buys": buys,
            "sells": sells,
            "wins": wins,
            "losses": losses,
            "buy_signals": buy_signals,
            "already_in_position_skips": already_skips,
            "shadow_rows": int(len(s)),
            "shadow_buys": shadow_buys,
            "shadow_avg_entry_score": shadow_avg_score,
            "shadow_avg_next_ret_pct": shadow_avg_next_ret,
        })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values(
        ["realized_pnl_dollars", "buy_signals", "already_in_position_skips"],
        ascending=[False, False, False],
    )

def make_live_position_snapshot(learning: pd.DataFrame) -> pd.DataFrame:
    """
    Build actual Bot Boi live/open-position snapshot from learning bars.

    This uses logs/learning, not shadow.
    It shows what Bot Boi actually held/saw today.
    """
    if learning.empty or "symbol" not in learning.columns:
        return pd.DataFrame()

    rows = []

    for sym, group in learning.groupby("symbol"):
        group = group.copy()

        last = group.iloc[-1]

        buy_signals = int((group.get("signal_action", "") == "buy").sum()) if "signal_action" in group else 0
        already_skips = int((group.get("skip_reason", "") == "already_in_position").sum()) if "skip_reason" in group else 0

        scored = group[group["entry_score"].notna()] if "entry_score" in group else pd.DataFrame()
        latest_entry_score = (
            float(scored["entry_score"].iloc[-1])
            if not scored.empty
            else None
        )

        avg_entry_score = (
            float(scored["entry_score"].mean())
            if not scored.empty
            else None
        )

        position_qty = float(last.get("position_qty", 0.0) or 0.0)
        last_price = float(last.get("price", 0.0) or 0.0)
        avg_price = float(last.get("avg_price", 0.0) or 0.0)
        pnl_pct = float(last.get("pnl_pct", 0.0) or 0.0)

        unrealized_pnl_dollars = (
            (last_price - avg_price) * position_qty
            if position_qty > 0 and avg_price > 0
            else 0.0
        )

        rows.append({
            "symbol": sym,
            "bars_logged": int(len(group)),
            "last_price": last_price,
            "position_qty": position_qty,
            "avg_price": avg_price,
            "unrealized_pnl_pct": pnl_pct,
            "unrealized_pnl_dollars": unrealized_pnl_dollars,
            "buy_signals": buy_signals,
            "already_in_position_skips": already_skips,
            "latest_entry_score": latest_entry_score,
            "avg_entry_score": avg_entry_score,
        })

    out = pd.DataFrame(rows)

    if out.empty:
        return out

    return out.sort_values(
        ["unrealized_pnl_dollars", "buy_signals"],
        ascending=[False, False],
    )


def make_score_buckets(source: pd.DataFrame, source_name: str) -> pd.DataFrame:
    if source.empty or "entry_score" not in source.columns:
        return pd.DataFrame()

    df = source.copy()
    df = df[df["entry_score"].notna()].copy()

    if df.empty:
        return pd.DataFrame()

    bins = [0.0, 0.25, 0.35, 0.50, 0.65, 0.80, 1.01]
    labels = ["0.00-0.25", "0.25-0.35", "0.35-0.50", "0.50-0.65", "0.65-0.80", "0.80-1.00"]
    df["score_bucket"] = pd.cut(df["entry_score"], bins=bins, labels=labels, include_lowest=True)

    group = df.groupby("score_bucket", observed=False)

    out = group.agg(
        rows=("entry_score", "count"),
        avg_entry_score=("entry_score", "mean"),
    ).reset_index()

    out["source"] = source_name

    if "next_ret_pct" in df.columns:
        ret = group["next_ret_pct"].mean().reset_index(name="avg_next_ret_pct")
        out = out.merge(ret, on="score_bucket", how="left")

    if "pnl_pct" in df.columns:
        pnl = group["pnl_pct"].mean().reset_index(name="avg_live_pnl_pct")
        out = out.merge(pnl, on="score_bucket", how="left")

    return out


def make_shadow_candidates(shadow: pd.DataFrame, limit: int = 25) -> pd.DataFrame:
    if shadow.empty:
        return pd.DataFrame()

    required = {"symbol", "action", "entry_score"}
    if not required.issubset(set(shadow.columns)):
        return pd.DataFrame()

    buys = shadow[(shadow["action"] == "buy") & shadow["entry_score"].notna()].copy()
    if buys.empty:
        return pd.DataFrame()

    # Prefer strong repeated candidates, not one-off sparkles.
    grouped = buys.groupby("symbol").agg(
        buy_count=("action", "count"),
        avg_entry_score=("entry_score", "mean"),
        max_entry_score=("entry_score", "max"),
        avg_next_ret_pct=("next_ret_pct", "mean") if "next_ret_pct" in buys.columns else ("entry_score", "mean"),
        last_close=("close", "last") if "close" in buys.columns else ("entry_score", "last"),
        avg_adx=("adx", "mean") if "adx" in buys.columns else ("entry_score", "mean"),
    ).reset_index()

    grouped = grouped.sort_values(
        ["buy_count", "avg_entry_score", "avg_next_ret_pct"],
        ascending=[False, False, False],
    )

    return grouped.head(limit)

def make_suggested_experiments(live_positions: pd.DataFrame, shadow_candidates: pd.DataFrame) -> list[str]:
    suggestions = []

    if not live_positions.empty:
        open_pos = live_positions[live_positions["position_qty"] > 0].copy()

        if not open_pos.empty:
            leaders = open_pos.sort_values("unrealized_pnl_dollars", ascending=False).head(3)
            laggards = open_pos.sort_values("unrealized_pnl_dollars", ascending=True).head(3)

            leader_names = ", ".join(leaders["symbol"].astype(str).tolist())
            laggard_names = ", ".join(laggards["symbol"].astype(str).tolist())

            suggestions.append(
                f"Carryover watch: strongest open positions were {leader_names}. "
                "Track whether overnight carry improves or gives back gains."
            )

            suggestions.append(
                f"Weak-position watch: weakest open positions were {laggard_names}. "
                "Review whether these need tighter exits or higher entry thresholds after more data."
            )

        skip_heavy = live_positions.sort_values("already_in_position_skips", ascending=False).head(3)
        if not skip_heavy.empty and skip_heavy["already_in_position_skips"].max() > 25:
            names = ", ".join(skip_heavy["symbol"].astype(str).tolist())
            suggestions.append(
                f"Add-on/cooldown study: {names} had many already-in-position skips. "
                "Later test whether add-on buys, stricter cooldowns, or no-rebuy windows would help."
            )

    if not shadow_candidates.empty:
        filtered = shadow_candidates.copy()

        if "avg_next_ret_pct" in filtered.columns:
            filtered = filtered[filtered["avg_next_ret_pct"].fillna(0) > 0]

        top_shadow = filtered.head(5)
        if not top_shadow.empty:
            names = ", ".join(top_shadow["symbol"].astype(str).tolist())
            suggestions.append(
                f"Shadow watchlist experiment: review {names}. "
                "They had repeated shadow buy signals with positive next-bar behavior."
            )

    if not suggestions:
        suggestions.append(
            "No strong experiment candidates found yet. Collect more clean days before changing config."
        )

    return suggestions

def write_markdown_report(
    date_str: str,
    trades: pd.DataFrame,
    learning: pd.DataFrame,
    shadow: pd.DataFrame,
    rankings: pd.DataFrame,
    live_positions: pd.DataFrame,
    score_buckets: pd.DataFrame,
    shadow_candidates: pd.DataFrame,
) -> Path:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    path = RESEARCH_DIR / f"nightly_findings_{date_str}.md"

    sell_rows = trades[trades["event"] == "SELL"] if not trades.empty and "event" in trades else pd.DataFrame()

    total_realized = float(sell_rows["realized_pnl_dollars"].sum()) if not sell_rows.empty else 0.0
    wins = int((sell_rows["realized_pnl_dollars"] > 0).sum()) if not sell_rows.empty else 0
    losses = int((sell_rows["realized_pnl_dollars"] < 0).sum()) if not sell_rows.empty else 0
    win_rate = wins / max(1, wins + losses)

    learning_rows = len(learning)
    shadow_rows = len(shadow)
    unique_shadow_symbols = shadow["symbol"].nunique() if not shadow.empty and "symbol" in shadow else 0
    shadow_buy_count = int((shadow["action"] == "buy").sum()) if not shadow.empty and "action" in shadow else 0

    open_positions = (
        live_positions[live_positions["position_qty"] > 0]
        if not live_positions.empty and "position_qty" in live_positions
        else pd.DataFrame()
    )

    total_unrealized = (
        float(open_positions["unrealized_pnl_dollars"].sum())
        if not open_positions.empty and "unrealized_pnl_dollars" in open_positions
        else 0.0
    )

    lines = []
    lines.append(f"# Bot Boi Nightly Research: {date_str}")
    lines.append("")
    lines.append("## Safety")
    lines.append("")
    lines.append("- This report is research only.")
    lines.append("- No live config was changed.")
    lines.append("- No trades were placed by this script.")
    lines.append("")
    lines.append("## Data Loaded")
    lines.append("")
    lines.append(f"- Trades rows: **{len(trades):,}**")
    lines.append(f"- Learning rows: **{learning_rows:,}**")
    lines.append(f"- Shadow rows: **{shadow_rows:,}**")
    lines.append(f"- Shadow symbols: **{unique_shadow_symbols:,}**")
    lines.append(f"- Shadow buy candidates: **{shadow_buy_count:,}**")
    lines.append("")
    lines.append("## Actual Bot Boi Closed Trade Snapshot")
    lines.append("")
    lines.append(f"- Realized P/L estimate: **{money(total_realized)}**")
    lines.append(f"- Closed winners / losers: **{wins} / {losses}**")
    lines.append(f"- Win rate: **{win_rate * 100:.1f}%**")
    lines.append("")

    lines.append("## Live Bot Boi Position Snapshot")
    lines.append("")
    lines.append(f"- Estimated unrealized P/L from learning log: **{money(total_unrealized)}**")
    lines.append(f"- Open symbols tracked: **{len(open_positions)}**")
    lines.append("")

    if live_positions.empty:
        lines.append("_No live learning position data available._")
    else:
        lines.append("| Symbol | Qty | Last Price | Avg Price | Unrealized P/L | Unrealized P/L % | Buy Signals | Already-In-Position Skips | Latest Score |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")

        for _, r in live_positions.head(15).iterrows():
            latest_score = r.get("latest_entry_score")
            latest_score_txt = "n/a" if pd.isna(latest_score) else f"{float(latest_score):.3f}"

            lines.append(
                f"| {r['symbol']} | "
                f"{float(r['position_qty']):.6f} | "
                f"${float(r['last_price']):,.2f} | "
                f"${float(r['avg_price']):,.2f} | "
                f"{money(r['unrealized_pnl_dollars'])} | "
                f"{pct(r['unrealized_pnl_pct'])} | "
                f"{int(r['buy_signals'])} | "
                f"{int(r['already_in_position_skips'])} | "
                f"{latest_score_txt} |"
            )

    lines.append("")

    lines.append("## Actual Closed-Trade Symbol Summary")
    lines.append("")
    if rankings.empty:
        lines.append("_No symbol rankings available._")
    else:
        lines.append("| Symbol | Realized P/L | Buys | Sells | Wins | Losses | Shadow Buys | Avg Shadow Score |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in rankings.head(10).iterrows():
            avg_score = r.get("shadow_avg_entry_score")
            avg_score_txt = "n/a" if pd.isna(avg_score) else f"{float(avg_score):.3f}"
            lines.append(
                f"| {r['symbol']} | {money(r['realized_pnl_dollars'])} | "
                f"{int(r['buys'])} | {int(r['sells'])} | {int(r['wins'])} | {int(r['losses'])} | "
                f"{int(r['shadow_buys'])} | {avg_score_txt} |"
            )
    lines.append("")

    lines.append("## Strong Shadow Candidates")
    lines.append("")
    if shadow_candidates.empty:
        lines.append("_No shadow candidates found._")
    else:
        lines.append("| Symbol | Buy Count | Avg Entry Score | Max Entry Score | Avg Next Ret % | Last Close | Avg ADX |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for _, r in shadow_candidates.head(15).iterrows():
            avg_ret = r.get("avg_next_ret_pct")
            lines.append(
                f"| {r['symbol']} | {int(r['buy_count'])} | "
                f"{float(r['avg_entry_score']):.3f} | {float(r['max_entry_score']):.3f} | "
                f"{'n/a' if pd.isna(avg_ret) else f'{float(avg_ret):.4f}'} | "
                f"{'n/a' if pd.isna(r.get('last_close')) else f'{float(r.get('last_close')):.2f}'} | "
                f"{'n/a' if pd.isna(r.get('avg_adx')) else f'{float(r.get('avg_adx')):.2f}'} |"
            )
    lines.append("")

    suggestions = make_suggested_experiments(live_positions, shadow_candidates)

    lines.append("## Suggested Experiments")
    lines.append("")
    for idx, suggestion in enumerate(suggestions, start=1):
        lines.append(f"{idx}. {suggestion}")
    lines.append("")

    lines.append("## Early Research Notes")
    lines.append("")
    lines.append("- Compare actual traded symbols against high-scoring shadow symbols.")
    lines.append("- Watch for symbols with many shadow buys but weak next-bar returns.")
    lines.append("- Watch for actual symbols with repeated already-in-position skips, because they may need add-on logic or stricter cooldown.")
    lines.append("- Version 1 does not yet simulate alternate TP/SL settings.")
    lines.append("- Version 1 does not yet pair every buy with its exact sell lifecycle.")
    lines.append("")
    lines.append("## Next Research Upgrades")
    lines.append("")
    lines.append("1. Pair buys and sells into trade lifecycles.")
    lines.append("2. Add max favorable excursion and max adverse excursion.")
    lines.append("3. Simulate alternate TP/SL settings from learning bars.")
    lines.append("4. Build weekly research across multiple days.")
    lines.append("5. Feed this compact packet to the local AI for a short findings memo.")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Date in YYYY-MM-DD format")
    args = parser.parse_args()

    date_str = args.date

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    trades = load_trades(date_str)
    learning = load_learning(date_str)
    shadow = load_shadow(date_str)

    rankings = make_symbol_rankings(trades, learning, shadow)
    live_positions = make_live_position_snapshot(learning)
    score_learning = make_score_buckets(learning, "learning")
    score_shadow = make_score_buckets(shadow, "shadow")
    score_buckets = pd.concat([score_learning, score_shadow], ignore_index=True) if not score_learning.empty or not score_shadow.empty else pd.DataFrame()
    shadow_candidates = make_shadow_candidates(shadow)

    rankings_path = RESEARCH_DIR / f"symbol_rankings_{date_str}.csv"
    buckets_path = RESEARCH_DIR / f"score_buckets_{date_str}.csv"
    candidates_path = RESEARCH_DIR / f"shadow_candidates_{date_str}.csv"
    live_positions_path = RESEARCH_DIR / f"live_positions_{date_str}.csv"

    rankings.to_csv(rankings_path, index=False)
    score_buckets.to_csv(buckets_path, index=False)
    shadow_candidates.to_csv(candidates_path, index=False)
    live_positions.to_csv(live_positions_path, index=False)

    report_path = write_markdown_report(
        date_str,
        trades,
        learning,
        shadow,
        rankings,
        live_positions,
        score_buckets,
        shadow_candidates,
    )

    print(f"Wrote report:      {report_path}")
    print(f"Wrote rankings:    {rankings_path}")
    print(f"Wrote buckets:     {buckets_path}")
    print(f"Wrote candidates:  {candidates_path}")
    print(f"Wrote live pos:    {live_positions_path}")
    print(f"Trades rows:       {len(trades):,}")
    print(f"Learning rows:     {len(learning):,}")
    print(f"Shadow rows:       {len(shadow):,}")


if __name__ == "__main__":
    main()
