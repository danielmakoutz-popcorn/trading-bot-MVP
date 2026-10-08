#!/usr/bin/env python3
"""
Strategy Sandbox v1.

Heavy weekend research engine for Bot Boi / Ai Boi.

Reads:
  logs/learning/bars_YYYY-MM-DD.csv
  logs/shadow/shadow-YYYY-MM-DD.csv

Writes:
  reports/sandbox/sandbox_report_YYYY-WW.md
  reports/sandbox/sandbox_symbol_summary_YYYY-WW.csv
  reports/sandbox/sandbox_top_configs_YYYY-WW.csv
  reports/sandbox/candidate_config_YYYY-WW.yaml

Safety:
  - Does NOT edit config.yaml.
  - Does NOT place trades.
  - Does NOT touch Alpaca.
  - Only simulates alternate variables from logged historical rows.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
from pandas.errors import EmptyDataError


PROJECT_ROOT = Path(__file__).resolve().parents[1]

LEARNING_DIR = PROJECT_ROOT / "logs" / "learning"
SHADOW_DIR = PROJECT_ROOT / "logs" / "shadow"
SANDBOX_DIR = PROJECT_ROOT / "reports" / "sandbox"


@dataclass(frozen=True)
class SimConfig:
    threshold: float
    tp_pct: float
    sl_pct: float
    cooldown_bars: int
    max_hold_bars: int


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def most_recent_week(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
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


def money(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return "$0.00"
    return f"${float(x):,.2f}"


def pct(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{float(x) * 100:.2f}%"


def confidence_label(trades: int) -> str:
    """Simple sample-size confidence label for sandbox results."""
    if trades >= 15:
        return "HIGH"
    if trades >= 5:
        return "MEDIUM"
    if trades >= 1:
        return "LOW"
    return "NONE"


def confidence_note(trades: int) -> str:
    if trades >= 15:
        return "15+ trades, stronger sample for paper testing review"
    if trades >= 5:
        return "5-14 trades, useful but still needs confirmation"
    if trades >= 1:
        return "fewer than 5 trades, interesting clue only"
    return "no simulated trades"


def load_learning_day(day: date) -> pd.DataFrame:
    path = LEARNING_DIR / f"bars_{day.isoformat()}.csv"
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    try:
        df = pd.read_csv(path, low_memory=False)
    except EmptyDataError:
        return pd.DataFrame()

    if df.empty:
        return df

    df["source"] = "learning"
    df["date"] = day.isoformat()

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    else:
        df["timestamp"] = pd.NaT

    for col in ["price", "entry_score", "position_qty", "avg_price", "pnl_pct"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].fillna("").astype(str)

    keep = ["timestamp", "date", "source", "symbol", "price", "entry_score"]
    return df[[c for c in keep if c in df.columns]].copy()


def load_shadow_day(day: date) -> pd.DataFrame:
    path = SHADOW_DIR / f"shadow-{day.isoformat()}.csv"
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    try:
        df = pd.read_csv(path, low_memory=False)
    except EmptyDataError:
        return pd.DataFrame()

    if df.empty:
        return df

    df["source"] = "shadow"
    df["date"] = day.isoformat()

    if "time" in df.columns:
        df["timestamp"] = pd.to_datetime(df["time"], errors="coerce", utc=True)
    else:
        df["timestamp"] = pd.NaT

    if "close" in df.columns:
        df["price"] = pd.to_numeric(df["close"], errors="coerce")
    else:
        df["price"] = pd.NA

    if "entry_score" in df.columns:
        df["entry_score"] = pd.to_numeric(df["entry_score"], errors="coerce")
    else:
        df["entry_score"] = pd.NA

    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].fillna("").astype(str)

    keep = ["timestamp", "date", "source", "symbol", "price", "entry_score"]
    return df[[c for c in keep if c in df.columns]].copy()


def load_source(start: date, end: date, source: str) -> pd.DataFrame:
    if source == "learning":
        frames = [load_learning_day(d) for d in date_range(start, end)]
    elif source == "shadow":
        frames = [load_shadow_day(d) for d in date_range(start, end)]
    else:
        raise ValueError(f"Unknown source: {source}")

    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["timestamp", "symbol", "price"])
    df = df[df["symbol"] != ""].copy()
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    return df


def build_configs(heavy: bool) -> list[SimConfig]:
    thresholds = [x / 100 for x in range(30, 81)]  # 0.30 through 0.80 by .01

    if heavy:
        tp_pcts = [x / 10000 for x in [75, 100, 125, 150, 175, 200, 225, 250, 275, 300, 350]]
        sl_pcts = [x / 10000 for x in [40, 50, 60, 75, 90, 100, 125, 150, 175, 200]]
        cooldowns = [0, 1, 2, 3, 5, 10, 15, 20, 30]
        max_holds = [0, 30, 60, 120, 240, 390]
    else:
        tp_pcts = [x / 10000 for x in [100, 125, 150, 175, 200, 225, 250, 300]]
        sl_pcts = [x / 10000 for x in [50, 75, 100, 125, 150, 200]]
        cooldowns = [0, 2, 5, 10, 15, 30]
        max_holds = [0, 60, 120, 240]

    configs = []
    for threshold in thresholds:
        for tp_pct in tp_pcts:
            for sl_pct in sl_pcts:
                for cooldown_bars in cooldowns:
                    for max_hold_bars in max_holds:
                        configs.append(
                            SimConfig(
                                threshold=threshold,
                                tp_pct=tp_pct,
                                sl_pct=sl_pct,
                                cooldown_bars=cooldown_bars,
                                max_hold_bars=max_hold_bars,
                            )
                        )
    return configs


def simulate_symbol(symbol_df: pd.DataFrame, cfg: SimConfig, trade_dollars: float = 1000.0) -> dict:
    """
    Simple replay simulation.

    Entry:
      flat and entry_score >= threshold

    Exit:
      TP hit, SL hit, max_hold hit, or final row closes the open position.

    This is not a perfect market replay. It is a research approximation using logged rows.
    """
    df = symbol_df.sort_values("timestamp").reset_index(drop=True)

    in_pos = False
    entry_price = 0.0
    entry_i = -1
    last_exit_i = -10**9

    trades = []
    equity = 0.0
    peak_equity = 0.0
    max_drawdown = 0.0

    current_mfe = 0.0
    current_mae = 0.0

    for i, row in df.iterrows():
        price = float(row["price"])
        score = row.get("entry_score")

        if pd.isna(price) or price <= 0:
            continue

        if in_pos:
            ret = (price - entry_price) / entry_price

            current_mfe = max(current_mfe, ret)
            current_mae = min(current_mae, ret)

            held_bars = i - entry_i
            exit_reason = None

            if ret >= cfg.tp_pct:
                exit_reason = "tp"
            elif ret <= -cfg.sl_pct:
                exit_reason = "sl"
            elif cfg.max_hold_bars > 0 and held_bars >= cfg.max_hold_bars:
                exit_reason = "max_hold"

            if exit_reason:
                pnl_dollars = trade_dollars * ret
                equity += pnl_dollars
                peak_equity = max(peak_equity, equity)
                drawdown = peak_equity - equity
                max_drawdown = max(max_drawdown, drawdown)

                trades.append({
                    "ret_pct": ret,
                    "pnl_dollars": pnl_dollars,
                    "exit_reason": exit_reason,
                    "held_bars": held_bars,
                    "mfe_pct": current_mfe,
                    "mae_pct": current_mae,
                })

                in_pos = False
                entry_price = 0.0
                entry_i = -1
                last_exit_i = i
                current_mfe = 0.0
                current_mae = 0.0

                # Do not also enter on the same row after an exit.
                continue

        if not in_pos:
            if score is None or pd.isna(score):
                continue

            if float(score) >= cfg.threshold and (i - last_exit_i) >= cfg.cooldown_bars:
                in_pos = True
                entry_price = price
                entry_i = i
                current_mfe = 0.0
                current_mae = 0.0

    # Close any still-open sandbox position at final available price.
    if in_pos and entry_price > 0 and not df.empty:
        final_price = float(df["price"].dropna().iloc[-1])
        ret = (final_price - entry_price) / entry_price
        pnl_dollars = trade_dollars * ret
        equity += pnl_dollars
        peak_equity = max(peak_equity, equity)
        drawdown = peak_equity - equity
        max_drawdown = max(max_drawdown, drawdown)

        trades.append({
            "ret_pct": ret,
            "pnl_dollars": pnl_dollars,
            "exit_reason": "final_close",
            "held_bars": len(df) - 1 - entry_i,
            "mfe_pct": max(current_mfe, ret),
            "mae_pct": min(current_mae, ret),
        })

    n = len(trades)
    if n == 0:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_return_pct": 0.0,
            "avg_return_pct": 0.0,
            "median_return_pct": 0.0,
            "total_pnl_dollars_per_1000": 0.0,
            "avg_mfe_pct": 0.0,
            "avg_mae_pct": 0.0,
            "max_drawdown_dollars_per_1000": 0.0,
            "tp_exits": 0,
            "sl_exits": 0,
            "max_hold_exits": 0,
            "final_close_exits": 0,
            "quality_score": -9999.0,
            "confidence": confidence_label(0),
            "confidence_note": confidence_note(0),
        }

    rets = pd.Series([t["ret_pct"] for t in trades])
    pnls = pd.Series([t["pnl_dollars"] for t in trades])
    mfes = pd.Series([t["mfe_pct"] for t in trades])
    maes = pd.Series([t["mae_pct"] for t in trades])
    reasons = pd.Series([t["exit_reason"] for t in trades])

    wins = int((rets > 0).sum())
    losses = int((rets < 0).sum())
    win_rate = wins / max(1, wins + losses)

    total_return = float(rets.sum())
    avg_return = float(rets.mean())
    median_return = float(rets.median())

    # Quality score favors return, consistency, enough trades, and lower drawdown.
    # This is a ranking heuristic, not gospel.
    quality_score = (
        total_return
        + (avg_return * 5.0)
        + (win_rate * 0.10)
        + min(n, 50) * 0.001
        - (max_drawdown / trade_dollars) * 0.50
    )

    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_return_pct": total_return,
        "avg_return_pct": avg_return,
        "median_return_pct": median_return,
        "total_pnl_dollars_per_1000": float(pnls.sum()),
        "avg_mfe_pct": float(mfes.mean()),
        "avg_mae_pct": float(maes.mean()),
        "max_drawdown_dollars_per_1000": float(max_drawdown),
        "tp_exits": int((reasons == "tp").sum()),
        "sl_exits": int((reasons == "sl").sum()),
        "max_hold_exits": int((reasons == "max_hold").sum()),
        "final_close_exits": int((reasons == "final_close").sum()),
        "quality_score": quality_score,
        "confidence": confidence_label(n),
        "confidence_note": confidence_note(n),
    }


def run_grid(
    df: pd.DataFrame,
    source: str,
    configs: list[SimConfig],
    min_rows: int,
    min_trades: int,
    top_per_symbol: int,
    max_symbols: int | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    symbol_groups = []
    for sym, g in df.groupby("symbol"):
        scored_count = int(g["entry_score"].notna().sum()) if "entry_score" in g else 0
        if len(g) >= min_rows and scored_count > 0:
            symbol_groups.append((sym, g.copy(), scored_count))

    # Prefer symbols with more scored rows.
    symbol_groups.sort(key=lambda x: x[2], reverse=True)

    if max_symbols is not None and max_symbols > 0:
        symbol_groups = symbol_groups[:max_symbols]

    top_rows = []
    summary_rows = []

    total_symbols = len(symbol_groups)
    print(f"[{source}] Simulating {total_symbols} symbols with {len(configs):,} configs each...")

    for idx, (sym, g, scored_count) in enumerate(symbol_groups, start=1):
        print(f"[{source}] {idx}/{total_symbols} {sym}: rows={len(g):,} scored={scored_count:,}")

        symbol_results = []

        for cfg in configs:
            metrics = simulate_symbol(g, cfg)

            if metrics["trades"] < min_trades:
                continue

            row = {
                "source": source,
                "symbol": sym,
                "threshold": cfg.threshold,
                "tp_pct": cfg.tp_pct,
                "sl_pct": cfg.sl_pct,
                "cooldown_bars": cfg.cooldown_bars,
                "max_hold_bars": cfg.max_hold_bars,
                "rows": int(len(g)),
                "scored_rows": int(scored_count),
                **metrics,
            }
            symbol_results.append(row)

        if not symbol_results:
            summary_rows.append({
                "source": source,
                "symbol": sym,
                "rows": int(len(g)),
                "scored_rows": int(scored_count),
                "tested_configs": len(configs),
                "valid_configs": 0,
            })
            continue

        res = pd.DataFrame(symbol_results).sort_values("quality_score", ascending=False)

        best = res.iloc[0].to_dict()
        summary_rows.append({
            "source": source,
            "symbol": sym,
            "rows": int(len(g)),
            "scored_rows": int(scored_count),
            "tested_configs": len(configs),
            "valid_configs": int(len(res)),
            "best_quality_score": best["quality_score"],
            "best_confidence": best.get("confidence", confidence_label(int(best.get("trades", 0)))),
            "best_confidence_note": best.get("confidence_note", confidence_note(int(best.get("trades", 0)))),
            "best_threshold": best["threshold"],
            "best_tp_pct": best["tp_pct"],
            "best_sl_pct": best["sl_pct"],
            "best_cooldown_bars": best["cooldown_bars"],
            "best_max_hold_bars": best["max_hold_bars"],
            "best_trades": best["trades"],
            "best_win_rate": best["win_rate"],
            "best_total_return_pct": best["total_return_pct"],
            "best_avg_return_pct": best["avg_return_pct"],
            "best_pnl_per_1000": best["total_pnl_dollars_per_1000"],
            "best_max_drawdown_per_1000": best["max_drawdown_dollars_per_1000"],
        })

        top_rows.extend(res.head(top_per_symbol).to_dict("records"))

    top_df = pd.DataFrame(top_rows)
    summary_df = pd.DataFrame(summary_rows)

    if not top_df.empty:
        top_df = top_df.sort_values(["quality_score", "total_return_pct", "win_rate"], ascending=[False, False, False])

    if not summary_df.empty and "best_quality_score" in summary_df.columns:
        summary_df = summary_df.sort_values(["best_quality_score", "best_total_return_pct"], ascending=[False, False])

    return top_df, summary_df


def write_candidate_config(
    wid: str,
    actual_summary: pd.DataFrame,
    shadow_summary: pd.DataFrame,
    top_configs: pd.DataFrame,
    out_path: Path,
) -> None:
    """
    Writes a candidate YAML-like file manually.
    This is intentionally NOT config.yaml.
    """
    lines = []
    lines.append("# Candidate config generated by Strategy Sandbox")
    lines.append("# Research only. Do NOT blindly copy over config.yaml.")
    lines.append(f"# Week: {wid}")
    lines.append("")

    combined = []
    if not actual_summary.empty:
        a = actual_summary.copy()
        a["candidate_source"] = "actual_learning"
        combined.append(a)
    if not shadow_summary.empty:
        s = shadow_summary.copy()
        s["candidate_source"] = "shadow_universe"
        combined.append(s)

    if combined:
        combo = pd.concat(combined, ignore_index=True)
        combo = combo[combo.get("valid_configs", 0) > 0].copy()
        if not combo.empty:
            combo = combo.sort_values(["best_quality_score", "best_total_return_pct"], ascending=[False, False])
            top_symbols = combo.head(12)
        else:
            top_symbols = pd.DataFrame()
    else:
        top_symbols = pd.DataFrame()

    lines.append("candidate_symbols:")
    if top_symbols.empty:
        lines.append("  []")
    else:
        for _, r in top_symbols.iterrows():
            lines.append(f"  - symbol: {r['symbol']}")
            lines.append(f"    source: {r.get('candidate_source', 'unknown')}")
            lines.append(f"    threshold: {float(r['best_threshold']):.2f}")
            lines.append(f"    tp_pct: {float(r['best_tp_pct']):.4f}")
            lines.append(f"    sl_pct: {float(r['best_sl_pct']):.4f}")
            lines.append(f"    cooldown_bars: {int(r['best_cooldown_bars'])}")
            lines.append(f"    max_hold_bars: {int(r['best_max_hold_bars'])}")
            lines.append(f"    simulated_trades: {int(r['best_trades'])}")
            lines.append(f"    confidence: {r.get('best_confidence', confidence_label(int(r['best_trades'])))}")
            lines.append(f"    confidence_note: \"{r.get('best_confidence_note', confidence_note(int(r['best_trades'])))}\"")
            lines.append(f"    simulated_win_rate: {float(r['best_win_rate']):.4f}")
            lines.append(f"    simulated_total_return_pct: {float(r['best_total_return_pct']):.6f}")
            lines.append(f"    simulated_pnl_per_1000: {float(r['best_pnl_per_1000']):.2f}")

    lines.append("")
    lines.append("notes:")
    lines.append("  - This file is a research artifact.")
    lines.append("  - Copying these values directly into live config is not recommended.")
    lines.append("  - Use paper/shadow testing first.")
    lines.append("  - Prefer one or two changes at a time.")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_report(
    wid: str,
    start: date,
    end: date,
    configs_tested: int,
    learning_df: pd.DataFrame,
    shadow_df: pd.DataFrame,
    actual_summary: pd.DataFrame,
    shadow_summary: pd.DataFrame,
    top_configs: pd.DataFrame,
    candidate_path: Path,
    out_path: Path,
) -> None:
    lines = []
    lines.append(f"# Strategy Sandbox Report: {wid}")
    lines.append("")
    lines.append(f"Week range: **{start.isoformat()} → {end.isoformat()}**")
    lines.append("")
    lines.append("## Safety")
    lines.append("")
    lines.append("- This is sandbox research only.")
    lines.append("- No live config was changed.")
    lines.append("- No trades were placed.")
    lines.append("- Candidate config is written separately and must be manually reviewed.")
    lines.append("")
    lines.append("## Data Loaded")
    lines.append("")
    lines.append(f"- Learning rows: **{len(learning_df):,}**")
    lines.append(f"- Shadow rows: **{len(shadow_df):,}**")
    lines.append(f"- Config combinations tested per symbol: **{configs_tested:,}**")
    lines.append("")
    lines.append("## Top Actual-Learning Sandbox Results")
    lines.append("")
    if actual_summary.empty:
        lines.append("_No actual-learning sandbox results._")
    else:
        lines.append("| Symbol | Confidence | Best Score | Threshold | TP | SL | Cooldown | Max Hold | Trades | Win Rate | Total Return | P/L per $1000 | Max DD per $1000 |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in actual_summary.head(15).iterrows():
            if pd.isna(r.get("best_quality_score")):
                continue
            lines.append(
                f"| {r['symbol']} | {r.get('best_confidence', confidence_label(int(r['best_trades'])))} | {float(r['best_quality_score']):.4f} | "
                f"{float(r['best_threshold']):.2f} | {pct(r['best_tp_pct'])} | {pct(r['best_sl_pct'])} | "
                f"{int(r['best_cooldown_bars'])} | {int(r['best_max_hold_bars'])} | "
                f"{int(r['best_trades'])} | {float(r['best_win_rate']) * 100:.1f}% | "
                f"{pct(r['best_total_return_pct'])} | {money(r['best_pnl_per_1000'])} | "
                f"{money(r['best_max_drawdown_per_1000'])} |"
            )
    lines.append("")
    lines.append("## Top Shadow-Universe Sandbox Results")
    lines.append("")
    if shadow_summary.empty:
        lines.append("_No shadow sandbox results._")
    else:
        lines.append("| Symbol | Confidence | Best Score | Threshold | TP | SL | Cooldown | Max Hold | Trades | Win Rate | Total Return | P/L per $1000 | Max DD per $1000 |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in shadow_summary.head(20).iterrows():
            if pd.isna(r.get("best_quality_score")):
                continue
            lines.append(
                f"| {r['symbol']} | {r.get('best_confidence', confidence_label(int(r['best_trades'])))} | {float(r['best_quality_score']):.4f} | "
                f"{float(r['best_threshold']):.2f} | {pct(r['best_tp_pct'])} | {pct(r['best_sl_pct'])} | "
                f"{int(r['best_cooldown_bars'])} | {int(r['best_max_hold_bars'])} | "
                f"{int(r['best_trades'])} | {float(r['best_win_rate']) * 100:.1f}% | "
                f"{pct(r['best_total_return_pct'])} | {money(r['best_pnl_per_1000'])} | "
                f"{money(r['best_max_drawdown_per_1000'])} |"
            )
    lines.append("")
    lines.append("## Suggested Research Interpretation")
    lines.append("")
    lines.append("1. Prefer symbols that perform well in actual-learning results and also appear strong in shadow results.")
    lines.append("2. Be skeptical of symbols with great sandbox results but very few trades. LOW confidence means fewer than 5 trades.")
    lines.append("3. Treat threshold/TP/SL suggestions as experiments, not commands.")
    lines.append("4. The next upgrade should compare sandbox picks against the following week’s actual performance.")
    lines.append("5. The step after that is full trade lifecycle pairing and richer exit simulation.")
    lines.append("")
    lines.append("## Files Written")
    lines.append("")
    lines.append(f"- Candidate config: `{candidate_path}`")
    lines.append("- Top configs CSV: see `reports/sandbox/`")
    lines.append("- Symbol summary CSV: see `reports/sandbox/`")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Start date YYYY-MM-DD. Defaults to most recent Monday.")
    parser.add_argument("--end", help="End date YYYY-MM-DD. Defaults to same week Friday.")
    parser.add_argument("--source", choices=["learning", "shadow", "both"], default="both")
    parser.add_argument("--heavy", action="store_true", help="Use a larger parameter grid.")
    parser.add_argument("--max-symbols", type=int, default=0, help="Limit symbols per source. 0 means no limit.")
    parser.add_argument("--min-rows", type=int, default=100, help="Minimum rows required for a symbol.")
    parser.add_argument("--min-trades", type=int, default=3, help="Minimum simulated trades required for a config.")
    parser.add_argument("--top-per-symbol", type=int, default=25, help="Keep top N configs per symbol.")
    args = parser.parse_args()

    if args.start:
        start = parse_date(args.start)
        end = parse_date(args.end) if args.end else start + timedelta(days=4)
    else:
        start, end = most_recent_week()

    wid = week_id(start)
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)

    configs = build_configs(heavy=args.heavy)
    max_symbols = None if args.max_symbols == 0 else args.max_symbols

    learning_df = load_source(start, end, "learning") if args.source in ("learning", "both") else pd.DataFrame()
    shadow_df = load_source(start, end, "shadow") if args.source in ("shadow", "both") else pd.DataFrame()

    actual_top = pd.DataFrame()
    actual_summary = pd.DataFrame()
    shadow_top = pd.DataFrame()
    shadow_summary = pd.DataFrame()

    if args.source in ("learning", "both") and not learning_df.empty:
        actual_top, actual_summary = run_grid(
            learning_df,
            source="learning",
            configs=configs,
            min_rows=args.min_rows,
            min_trades=args.min_trades,
            top_per_symbol=args.top_per_symbol,
            max_symbols=max_symbols,
        )

    if args.source in ("shadow", "both") and not shadow_df.empty:
        shadow_top, shadow_summary = run_grid(
            shadow_df,
            source="shadow",
            configs=configs,
            min_rows=args.min_rows,
            min_trades=args.min_trades,
            top_per_symbol=args.top_per_symbol,
            max_symbols=max_symbols,
        )

    top_configs = pd.concat(
        [df for df in [actual_top, shadow_top] if not df.empty],
        ignore_index=True,
    ) if (not actual_top.empty or not shadow_top.empty) else pd.DataFrame()

    if not top_configs.empty:
        top_configs = top_configs.sort_values(["quality_score", "total_return_pct"], ascending=[False, False])

    symbol_summary = pd.concat(
        [df for df in [actual_summary, shadow_summary] if not df.empty],
        ignore_index=True,
    ) if (not actual_summary.empty or not shadow_summary.empty) else pd.DataFrame()

    top_configs_path = SANDBOX_DIR / f"sandbox_top_configs_{wid}.csv"
    summary_path = SANDBOX_DIR / f"sandbox_symbol_summary_{wid}.csv"
    candidate_path = SANDBOX_DIR / f"candidate_config_{wid}.yaml"
    report_path = SANDBOX_DIR / f"sandbox_report_{wid}.md"

    top_configs.to_csv(top_configs_path, index=False)
    symbol_summary.to_csv(summary_path, index=False)

    write_candidate_config(
        wid=wid,
        actual_summary=actual_summary,
        shadow_summary=shadow_summary,
        top_configs=top_configs,
        out_path=candidate_path,
    )

    write_report(
        wid=wid,
        start=start,
        end=end,
        configs_tested=len(configs),
        learning_df=learning_df,
        shadow_df=shadow_df,
        actual_summary=actual_summary,
        shadow_summary=shadow_summary,
        top_configs=top_configs,
        candidate_path=candidate_path,
        out_path=report_path,
    )

    print(f"Wrote sandbox report:   {report_path}")
    print(f"Wrote symbol summary:   {summary_path}")
    print(f"Wrote top configs:      {top_configs_path}")
    print(f"Wrote candidate config: {candidate_path}")
    print(f"Week:                   {start.isoformat()} to {end.isoformat()}")
    print(f"Configs per symbol:     {len(configs):,}")
    print(f"Learning rows:          {len(learning_df):,}")
    print(f"Shadow rows:            {len(shadow_df):,}")


if __name__ == "__main__":
    main()
