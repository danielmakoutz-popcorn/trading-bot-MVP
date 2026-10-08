import csv, os, json, hashlib
import os
import math
from uuid import UUID
from datetime import datetime

try:
    import numpy as np
except Exception:
    np = None

ML_FEATURE_HASH = True
TRADES_CSV = os.path.join("logs", "trades", "trades.csv")
SHADOW_LOG = os.path.join("logs", "shadow", "shadow.log")

os.makedirs(os.path.dirname(TRADES_CSV), exist_ok=True)
os.makedirs(os.path.dirname(SHADOW_LOG), exist_ok=True)

def sanitize_json_value(value):
    """Convert values into safe JSON-friendly data."""
    if value is None:
        return None

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, dict):
        return {str(k): sanitize_json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [sanitize_json_value(v) for v in value]

    if np is not None:
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            value = float(value)
        if isinstance(value, np.ndarray):
            return sanitize_json_value(value.tolist())

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    return value

def ensure_trades_csv(path=TRADES_CSV):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp","symbol","event","qty","price","reason","pnl","extra"])

def append_trade(timestamp, symbol, event, qty, price, reason="", pnl="", extra=None, path=TRADES_CSV):
    ensure_trades_csv(path)

    try:
        ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
    except Exception:
        ts_str = str(timestamp)

    try:
        price_str = f"{float(price):.4f}"
    except Exception:
        price_str = str(price)

    try:
        pnl_str = "" if pnl in ("", None) else f"{float(pnl):.4f}"
    except Exception:
        pnl_str = str(pnl)

    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            ts_str,
            symbol,
            event,
            qty,
            price_str,
            reason,
            pnl_str,
            json.dumps(sanitize_json_value(extra or {}), sort_keys=True),
        ])

def shadow_log(line, path=SHADOW_LOG):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(line.rstrip() + "\n")

def feature_snapshot(i, df):
    feats = {
        "rel20":  float(df['close'].iloc[i] / df['ema20'].iloc[i]  - 1),
        "rel50":  float(df['close'].iloc[i] / df['ema50'].iloc[i]  - 1),
        "rel200": float(df['close'].iloc[i] / df['ema200'].iloc[i] - 1),
        "adx":    float(df['adx'].iloc[i]),
        "di_p":   float(df['di_p'].iloc[i]),
        "di_m":   float(df['di_m'].iloc[i]),
        "atr%":   float(df['atr_pct'].iloc[i]),
        "vw_delta": float(df['close'].iloc[i] - df['vwap'].iloc[i]),
        "htf_slope200": float(df['htf_slope200'].iloc[i]),
        "htf_up": int(bool(df['htf_up'].iloc[i])),
        "mfo":    int(df.index[i].minute + 60*df.index[i].hour), # minutes from open if you’re on RTH index, tweak as needed
    }
    if ML_FEATURE_HASH:
        digest = hashlib.md5(json.dumps(feats, sort_keys=True).encode()).hexdigest()[:10]
        return feats, digest
    return feats, None

LEARNING_DIR = os.path.join("logs", "learning")
os.makedirs(LEARNING_DIR, exist_ok=True)

def learning_csv_path(ts=None):
    if ts is None:
        day = datetime.now().strftime("%Y-%m-%d")
    else:
        try:
            day = ts.astimezone().strftime("%Y-%m-%d")
        except Exception:
            day = str(ts)[:10]
    return os.path.join(LEARNING_DIR, f"bars_{day}.csv")

def ensure_learning_csv(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "timestamp", "symbol", "price",
                "signal_action", "entry_score",
                "score_cross_up", "score_adx", "score_rsi", "score_vwap", "score_trend",
                "position_qty", "avg_price", "pnl_pct",
                "tp_hit", "sl_hit",
                "order_action", "order_id", "skip_reason",
            ])

def append_learning_bar(
    timestamp, symbol, price,
    signal_action="hold",
    entry_score=None,
    components=None,
    position_qty=0.0,
    avg_price=0.0,
    pnl_pct=0.0,
    tp_hit=False,
    sl_hit=False,
    order_action="",
    order_id="",
    skip_reason="",
):
    path = learning_csv_path(timestamp)
    ensure_learning_csv(path)

    components = components or {}

    ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)

    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            ts_str,
            symbol,
            float(price),
            signal_action,
            "" if entry_score is None else float(entry_score),
            components.get("cross_up", ""),
            components.get("adx_ok", ""),
            components.get("rsi_ok", ""),
            components.get("close_gt_vwap", ""),
            components.get("trend_ok", ""),
            float(position_qty or 0.0),
            float(avg_price or 0.0),
            float(pnl_pct or 0.0),
            bool(tp_hit),
            bool(sl_hit),
            order_action,
            order_id or "",
            skip_reason or "",
        ])
