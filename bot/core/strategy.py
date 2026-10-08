from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Deque, Dict, List, Any
from collections import deque
from bot.strategy.signals import (
    trend_ok, crossed_up, entry_signal, size_by_atr_pct,
    update_trailing_stop, exit_signal
)
from bot.core.trade_logs import feature_snapshot
from bot.core.indicators import add_indicators
from time import monotonic
import numpy as np
import pandas as pd
import logging
import math

@dataclass
class Signal:
    action: str   # 'buy', 'sell', or 'hold'
    reason: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

class SMAState:
    def __init__(self, fast_window: int, slow_window: int):
        if fast_window >= slow_window:
            raise ValueError("Fast SMA window must be < slow SMA window")
        self.fast_n = int(fast_window)
        self.slow_n = int(slow_window)
        self.prices = deque(maxlen=self.slow_n)
        self.cooldown = int(cooldown)
        self.min_adx = int(min_adx)
        self.buf = []
        self.bar_index = 0

    def update(self, close_price: float):
        self.prices.append(float(close_price))
        n = len(self.prices)
        if n < self.slow_n:
            return None, None, n  # not enough history yet

        fast_ma = sum(list(self.prices)[-self.fast_n:]) / self.fast_n
        slow_ma = sum(self.prices) / self.slow_n
        return fast_ma, slow_ma, n

class Strategy:
    def on_price(self, price: float) -> Signal:
        raise NotImplementedError

class SMACrossover(Strategy):
    """
    Upgraded to indicator-driven logic:
      - Maintains a rolling OHLCV buffer (1m bars)
      - Computes EMA/RSI/ADX/ATR/VWAP each bar via add_indicators()
      - Uses entry_signal/exit_signal from signals.py
      - Tracks entry/trail state locally; engine still owns orders
    """
    def __init__(self, *, fast_window=20, slow_window=50,
             cooldown=10, min_adx=18,
             rsi_len=14, adx_len=14, atr_len=14,
             min_adx_pad=0, cross_eps_pct=0.001, use_close_over_ema20=True,
             tp_pct=0.04, sl_pct=0.03, buf_len=1000, entry_threshold=0.6, weights=None, scoring=None, has_pos_fn=None):
        if fast_window >= slow_window:
            raise ValueError("fast SMA window must be < slow window")

        #logging
        self.log = logging.getLogger(__name__)

        # keep old SMA state around if you still call snapshot() elsewhere
        self.fast_n = int(fast_window)
        self.slow_n = int(slow_window)

        # rolling 1m OHLCV buffer
        self.buf_len = int(buf_len)
        self.buf: Deque[dict] = deque(maxlen=int(buf_len))
        self.bar_index: int = 0

        # trade state (used by entry/exit helpers)
        self.cooldown = int(cooldown)
        self.min_adx = int(min_adx)
        self.last_entry_i: Optional[int] = None
        self.entry_i: Optional[int] = None
        self.entry_price: Optional[float] = None
        self.entry_qty: float = 0.0
        self.last_entry_i: Optional[int] = None
        self.trail: Optional[float] = None

        # indicator lengths from args
        self.rsi_len = int(rsi_len)
        self.adx_len = int(adx_len)
        self.atr_len = int(atr_len)
        self.MIN_ADX_PAD = int(min_adx_pad)
        self.CROSS_EPS_PCT = float(cross_eps_pct)
        self.USE_CLOSE_OVER_EMA20 = bool(use_close_over_ema20)
        self.tp_pct = float(tp_pct)
        self.sl_pct = float(sl_pct)

        #weight value scoring
        self.entry_threshold = float(entry_threshold)
        self.weights = dict(weights or {})
        self.scoring = dict(scoring or {})

        # position checker
        self.has_pos_fn = has_pos_fn
        self.position_of = has_pos_fn or (lambda _sym: False)

        # last values for snapshot()
        self.last_fast = None
        self.last_slow = None

        self.log.info(f"[INIT] fast={self.fast_n} slow={self.slow_n} "
                      f"MIN_ADX={self.min_adx} PAD={self.MIN_ADX_PAD} "
                      f"ADX_LEN={self.adx_len} RSI_LEN={self.rsi_len} "
                      f"CROSS_EPS_PCT={self.CROSS_EPS_PCT} USE_CLOSE20={self.USE_CLOSE_OVER_EMA20} "
                      f"TP={self.tp_pct} SL={self.sl_pct}")

    def _softstep(self, x, mid, span):
        """Logistic-ish soft step. x>mid -> ~1, x<mid -> ~0."""
        import math
        k = 4.0 / max(1e-9, span)   # slope
        return 1.0 / (1.0 + math.exp(-k * (x - mid)))

    def _score_components(self, meta: dict) -> dict:
        """
        Return per-feature scores in [0,1].
        Discrete checks map to {True:1, False:0}; some get soft scores.
        """
        def _clamp01(x: float) -> float:
            if x != x:  # NaN
                return 0.0
            if x < 0.0: return 0.0
            if x > 1.0: return 1.0
            return x

        px    = float(meta.get('close', float('nan')))
        vwap  = float(meta.get('vwap',  float('nan')))
        fast  = float(meta.get('ema_fast', float('nan')))
        slow  = float(meta.get('ema_slow', float('nan')))
        slope = float(meta.get('ema_fast_slope', 0.0))  # per-bar change in fast EMA

        # 1) Cross-up: 1.0 if crossed; otherwise give slope credit (up to 0.10%/bar)
        if meta.get('cross_up', False):
            s_cross = 1.0
        else:
            # slope/price of 0.10% per bar → full credit
            s_cross = _clamp01((slope / max(1e-9, px)) / 0.001)

        # 2) VWAP: percent premium above vwap (0.15% → full credit)
        if px >= vwap and all(map(math.isfinite, [px, vwap])):
            s_vwap = _clamp01(((px - vwap) / px) / 0.0015)
        else:
            s_vwap = 0.0

        # 3) Trend: EMA distance as a percent of price (0.20% → full credit)
        if all(map(math.isfinite, [px, fast, slow])):
            s_trend = _clamp01(((fast - slow) / px) / 0.002)
        else:
            # fallback to your binary flags if EMAs/VWAP missing
            s_trend = 1.0 if meta.get('ema50>200', False) or meta.get('trend_ok', False) else 0.0

        # soft RSI & ADX (fall back to binary if missing)
        rsi = float(meta.get('rsi', float('nan')))
        adx = float(meta.get('adx', float('nan')))

        if rsi == rsi:  # not NaN
            rsi_mid  = float(self.scoring.get('rsi_mid', 50))
            rsi_span = float(self.scoring.get('rsi_span', 20))
            s_rsi = self._softstep(rsi, rsi_mid, rsi_span)
        else:
            s_rsi = 1.0 if meta.get('rsi_ok', False) else 0.0

        if adx == adx:
            adx_min  = float(self.scoring.get('adx_min', self.min_adx))
            adx_span = float(self.scoring.get('adx_span', 10))
            s_adx = self._softstep(adx, adx_min, adx_span)
        else:
            s_adx = 1.0 if meta.get('adx_ok', False) else 0.0

        return {
            'cross_up': s_cross,
            'adx_ok': s_adx,
            'rsi_ok': s_rsi,
            'close_gt_vwap': s_vwap,
            'trend_ok': s_trend,
        }

    def _entry_confidence(self, meta: dict) -> tuple[float, dict]:
        """
        Weighted score in [0,1], plus breakdown. Weights normalized automatically.
        """
        comps = self._score_components(meta)
        w = {
            'cross_up':       float(self.weights.get('cross_up', 0.30)),
            'adx_ok':         float(self.weights.get('adx_ok', 0.20)),
            'rsi_ok':         float(self.weights.get('rsi_ok', 0.20)),
            'close_gt_vwap':  float(self.weights.get('close_gt_vwap', 0.15)),
            'trend_ok':       float(self.weights.get('trend_ok', 0.15)),
        }
        w_sum = sum(w.values()) or 1.0
        score = sum(w[k] * comps[k] for k in comps) / w_sum
        return score, {'weights': w, 'components': comps}

    entry_confidence = _entry_confidence

    def in_position(self) -> bool:
        local = (self.entry_qty > 0) or (self.entry_price is not None)
        if self.has_pos_fn is not None:
            try:
                return bool(self.has_pos_fn()) or local
            except Exception:
                return local
        return local

    def _enter_position(self, price: float, qty: float = 1.0):
        self.entry_price = float(price)
        self.entry_qty   = float(qty)
        self.entry_i     = self.bar_index
        self.last_entry_i = self.bar_index
        # optional: reset trailing stop
        self.trail = None

    def _exit_position(self, price: float, reason: str = ""):
        if self.entry_price:
            pnl = (price - self.entry_price) / self.entry_price * 100
            self.log.info(f"[TRADE_EXIT] reason={reason} pnl={pnl:.2f}% entry={self.entry_price} exit={price}")
        self.entry_price = None
        self.entry_qty   = 0.0
        self.entry_i     = None
        self.trail       = None

    def _checks_entry(self, meta) -> dict:
        # meta: dict with keys from your indicator pass: rsi, adx, vwap, ema50, ema200, fast_ma, slow_ma, close, bar_index

        score, detail = self.entry_confidence(meta)
        sym = meta.get("symbol", "?")
        self.log.info(f"[CONF] {sym} entry_score={score:.3f} comps={detail['components']}")

        meta['entry_score'] = score
        meta['score_components'] = detail['components']

        session_ok     = meta.get('session_ok', True)
        cooldown_ok    = (self.last_entry_i is None) or ((meta['bar_index'] - self.last_entry_i) >= self.cooldown)
        adx_ok         = meta['adx'] >= self.min_adx
        cross_up       = meta['fast_ma_prev'] <= meta['slow_ma_prev'] and meta['fast_ma'] > meta['slow_ma']
        rsi_ok         = meta['rsi'] >= getattr(self, 'rsi_buy', 35)
        close_gt_vwap  = meta['close'] >= meta.get('vwap', meta['close'])
        trend_ok       = meta.get('ema50', 0) > meta.get('ema200', 0)

        checks = {
            'session_ok': session_ok,
            'cooldown_ok': cooldown_ok,
            'adx_ok': adx_ok,
            'cross_up': cross_up,
            'rsi_ok': rsi_ok,
            'close>vwap': close_gt_vwap,
            'ema50>200': trend_ok,
        }
        return checks

    def _checks_exit(self, meta) -> dict:
        tp_pct = getattr(self, 'tp_pct', 0.04)
        sl_pct = getattr(self, 'sl_pct', 0.03)
        pnl_pct = (meta['close'] - self.entry_price) / self.entry_price if self.entry_price else 0.0

        tp_hit = pnl_pct >= tp_pct
        sl_hit = pnl_pct <= -sl_pct
        cross_dn = meta['fast_ma_prev'] >= meta['slow_ma_prev'] and meta['fast_ma'] < meta['slow_ma']
        rsi_cool = meta['rsi'] <= getattr(self, 'rsi_sell', 65)

        return {'tp_hit': tp_hit, 'sl_hit': sl_hit, 'cross_dn': cross_dn, 'rsi<=sell': rsi_cool}

    # NEW preferred path: pass full candle
    def on_candle(self, candle, symbol: str) -> Signal:
        can_enter = False
        sym = symbol or "UNK"
        """
        candle must expose .ts, .open, .high, .low, .close, .volume
        (your engine’s provider already returns this shape)
        """
        print(f"[CHK] {sym} close={getattr(candle,'close',None)} "
              f"fast={self.last_fast} slow={self.last_slow} "
              f"cool={self.cooldown}")
        self.bar_index += 1
        # push bar into buffer
        self.buf.append({
            "ts": candle.ts, "open": float(getattr(candle, "open", np.nan)),
            "high": float(getattr(candle, "high", np.nan)),
            "low": float(getattr(candle, "low", np.nan)),
            "close": float(getattr(candle, "close", np.nan)),
            "volume": float(getattr(candle, "volume", 0.0)),
        })

        # need at least slow_n bars to stabilize indicators
        if len(self.buf) < self.slow_n:
            return Signal("hold", "warmup")

        # build DF for indicators
        df = pd.DataFrame(self.buf)
        # make a tz-aware DatetimeIndex if your ts is datetime; if string, pd.to_datetime handles it
        df.index = pd.to_datetime(df["ts"])
        df = df.drop(columns=["ts"])

        df = add_indicators(df)  # <- Block 1
        df = df.bfill().ffill()

        if df.empty:
            self.last_fast = self.last_slow = None
            return Signal("hold", "no_data")

        i = len(df) - 1
        if i < 0:
            self.last_fast = self.last_slow = None
            return Signal("hold", "no_valid_rows")

        self.last_fast = df["ema20"].iloc[i] if "ema20" in df.columns else None
        self.last_slow = df["ema50"].iloc[i] if "ema50" in df.columns else None

        print(f"[DEBUG READY] len={len(self.buf)} slow_n={self.slow_n} "
              f"fast={self.last_fast} slow={self.last_slow} is_ready={self.is_ready()}")

        if not self.is_ready():
            print(f"[DEBUG READY] len={len(self.buf)} slow_n={self.slow_n} is_ready={self.is_ready()}")
            return Signal("hold", "warmup")

        # in SMACrossover.on_candle, after df/add_indicators and i = len(df)-1:
        chk = {
            "htf_up": bool(df["htf_up"].iloc[i]) if "htf_up" in df else None,
            "ema50>200": float(df["ema50"].iloc[i]) > float(df["ema200"].iloc[i]),
            "adx>=min": float(df["adx"].iloc[i]) >= float(self.min_adx),
            "close>vwap": float(df["close"].iloc[i]) > float(df["vwap"].iloc[i]),
            "cross_up_ema20": (
                i > 0
                and (df["close"].iloc[i-1] <= df["ema20"].iloc[i-1])
                and (df["close"].iloc[i] > df["ema20"].iloc[i])
            ),
            "rsi>50": (
                ("rsi" in df.columns)
                and (float(df["rsi"].iloc[i]) > 50)
            ),
        }
        self.log.info(
            f"[CHK] {sym} {chk} min_adx={self.min_adx} cool={self.cooldown}"
        )
        price = float(df["close"].iloc[i])

        # after: price = float(df["close"].iloc[i])
        atr_pct  = float(df["atr_pct"].iloc[i]) if "atr_pct" in df.columns else float("nan")
        rsi_val  = float(df["rsi"].iloc[i])     if "rsi"     in df.columns else float("nan")
        adx_val  = float(df["adx"].iloc[i])     if "adx"     in df.columns else float("nan")
        vwap_val = float(df["vwap"].iloc[i])    if "vwap"    in df.columns else price

        fast_now  = float(df["ema20"].iloc[i])
        slow_now  = float(df["ema50"].iloc[i])
        fast_prev = float(df["ema20"].iloc[i-1] if i > 0 else df["ema20"].iloc[i])
        slow_prev = float(df["ema50"].iloc[i-1] if i > 0 else df["ema50"].iloc[i])

        ema20_now  = float(df["ema20"].iloc[i])
        ema50_now  = float(df["ema50"].iloc[i])
        ema200_now = float(df["ema200"].iloc[i]) if "ema200" in df.columns else float("nan")

        ema20_prev  = float(df["ema20"].iloc[i-1]  if i > 0 else df["ema20"].iloc[i])
        ema50_prev  = float(df["ema50"].iloc[i-1]  if i > 0 else df["ema50"].iloc[i])

        session_ok = True

        # build `meta` from your latest indicators
        meta = {
            'bar_index': self.bar_index,
            'close': price,
            'rsi': rsi_val,
            'adx': adx_val,
            'vwap': vwap_val,

            # MAs
            'ema50': ema50_now,
            'ema200': ema200_now,
            'ema_fast': ema20_now,
            'ema_slow': ema50_now,  # 🔧 added

            # MA states
            'fast_ma_prev': ema20_prev,
            'slow_ma_prev': ema50_prev,
            'fast_ma': ema20_now,

            # slope + cross
            'ema_fast_slope': ema20_now - ema20_prev,
            'cross_up': ema20_prev <= ema50_prev and ema20_now > ema50_now,
            'cross_dn': ema20_prev >= ema50_prev and ema20_now < ema50_now,

            # filters
            'session_ok': session_ok,
            'trend_ok': ema50_now > ema200_now,
            'ema50>200': ema50_now > ema200_now,
            'close>vwap': price > vwap_val if vwap_val is not None else False,  # 🔧 added

            # misc
            'symbol': symbol,
        }

        score, detail = self._entry_confidence(meta)
        self.log.info(f"[CONF] {symbol} entry_score={score:.3f} comps={detail['components']}")

        meta['entry_score'] = score
        meta['score_components'] = detail['components']

#        if (not self.in_position()) and score >= self.entry_threshold:
 #           chk = self._checks_entry(meta)
  #          self.log.info(f"[CHK] {symbol} {chk} min_adx={self.min_adx} cool={self.cooldown}")
   #         if all([chk['session_ok'], chk['cooldown_ok'], chk['adx_ok'], chk['cross_up'], chk['rsi_ok']]):
    #            # optional: also require trend_ok / close>vwap
     #           self._enter_position(close)  # your existing helper
      #          self.last_entry_i = self.bar_index
       #         self.log.info(f"[SIG] BUY {symbol} reason='entry_checks_passed' price={close}")
#        else:
 #           # ✅ Only run exit logic if we actually have a position
  #          if self.entry_price is None or self.entry_qty <= 0:
   #             self.log.debug(f"[SELL_CHECK] {symbol} skipped (flat, no position)")
    #            return Signal("hold")

#            sc = self._checks_exit(meta)
 #           self.log.info(f"[SELL_CHECK] {symbol} {sc} pos_price={self.entry_price}")

            # Only trigger sell if position exists AND exit conditions hit
  #          if any([sc['tp_hit'], sc['sl_hit'], sc['cross_dn'], sc['rsi<=sell']]):
   #             self._exit_position(price, reason='exit_checks_hit')
    #            self.log.info(f"[SIG] SELL {symbol} reason='exit_checks_hit' price={price}")
     #           return Signal("sell", reason="exit_checks_hit")

        # keep a couple values for your snapshot() compatibility (SMA via EMA20/EMA50 proxy)
        self.last_fast = float(df["ema20"].iloc[i])
        self.last_slow = float(df["ema50"].iloc[i])

        # ---- POSITION GATING (broker = source of truth while debugging) ----
#        assume_flat = bool(self.scoring.get("assume_flat_if_broker_flat", False)) \
 #                  or bool(getattr(self, "assume_flat_if_broker_flat", False))

        # Always consult broker (no local memory)
  #      broker_in_pos = self.position_of(symbol) if hasattr(self, "position_of") else False
   #     in_pos = broker_in_pos if assume_flat else self.in_position()

        # --- reconcile local vs broker ---
        # If broker is flat, we trust it and wipe any stale local state.
    #    if (getattr(self, "assume_flat_if_broker_flat", False) or self.scoring.get("assume_flat_if_broker_flat", False)):
     #       if not broker_in_pos and self.entry_price is None:
      #          self.log.info(f"[RECONCILE] {symbol} broker flat + local flat confirmed (no active entry)")
       #         in_pos = False
        #    elif not broker_in_pos:
         #       self.log.info(f"[RECONCILE] {symbol} broker flat but local entry exists -> keeping local state")
                # Do NOT clear local variables here
          #      pass

        # Extreme override (use sparingly): pretend flat to allow entries
       # if bool(self.scoring.get("ignore_positions_entirely", False)):
        #    in_pos = False

        # ---- COOLDOWN / ENTRY GATING ----
        can_enter = True
        cool_ok = (self.last_entry_i is None) or ((self.bar_index - self.last_entry_i) >= self.cooldown)
        score_ok = score >= self.entry_threshold  # keep your real score check here if you gate by score

        # Helpful logging so we can see why it did/didn't enter
        self.log.info(
            f"[ENTRY_GATE] {symbol} "
            f"cool_ok={cool_ok} score_ok={score_ok} "
            f"entry_i={self.entry_i} last_entry_i={self.last_entry_i} "
            f"-> can_enter={can_enter}"
        )

        # ---- ENTRY (scored trigger) ----

        # build meta for this bar (you already did this above)
        meta["symbol"] = symbol  # keep this for logs

        # score with graded components
        score, detail = self.entry_confidence(meta)
        meta["entry_score"] = score
        meta["score_components"] = detail["components"]

        # soft gates only (keep hard gates optional)
        session_ok  = True  # or your market-hours check
        cooldown_ok = can_enter

        # OPTIONAL hard gates (off by default; use YAML flags if you want them later)
        require_adx_gate   = bool(self.scoring.get("require_adx_gate", False))
        require_cross_gate = bool(self.scoring.get("require_cross_gate", False))
        adx_gate_ok   = float(meta.get("adx", 0.0)) >= float(self.scoring.get("adx_min", self.min_adx))
        cross_gate_ok = bool(meta.get("cross_up", False))

        ready = session_ok and cooldown_ok
        if require_adx_gate and not adx_gate_ok:     ready = False
        if require_cross_gate and not cross_gate_ok: ready = False

        # helpful log
        self.log.info(
            f"[ENTRY_DECIDE] {symbol} score={score:.3f} thr={self.entry_threshold:.3f} "
            f"ready={ready} can_enter={can_enter} req_adx={require_adx_gate} adx_ok={adx_gate_ok} "
            f"req_cross={require_cross_gate} cross_ok={cross_gate_ok} comps={detail['components']}"
        )

        # trigger by score — DO NOT place orders here; return a Signal for the engine
        if ready and score >= self.entry_threshold:
            # keep your snapshot for shadow/snail-ML
            feats, h = feature_snapshot(i, df)

            # stamp locals (state), but do not submit orders here
            self.last_fast   = float(df["ema20"].iloc[i])
            self.last_slow   = float(df["ema50"].iloc[i])
            #self.last_entry_i = self.bar_index
            #self.entry_i      = self.bar_index
            #self.entry_price  = float(df["close"].iloc[i])
            #self.trail        = update_trailing_stop(self.entry_price, float(df["atr"].iloc[i]), cur_trail=None)

            return Signal(
                "buy",
                reason="score>=threshold",
                meta={
                    "features": feats, "hash": h, "atr_pct": atr_pct,
                    "entry_score": score, "components": detail["components"]
                },
            )

        # otherwise hold
        #return Signal("hold")

        # strategy currently only generates buy/sell
        return Signal("hold")

    # BACKWARD COMPAT: if engine still calls on_price()
    def on_price(self, price: float) -> Signal:
        # synthesize a minimal candle using last known OHLC (close = price)
        # if there’s no prior bar, create a flat one
        ts = pd.Timestamp.utcnow()
        if self.buf:
            last = self.buf[-1]
            o = last["close"]
            candle = type("Mini", (), dict(ts=ts, open=o, high=max(o, price), low=min(o, price),
                                           close=price, volume=last.get("volume", 0.0)))
        else:
            candle = type("Mini", (), dict(ts=ts, open=price, high=price, low=price,
                                           close=price, volume=0.0))
        return self.on_candle(candle)

    def is_ready(self) -> bool:
        return (len(self.buf) >= self.slow_n) and \
               (self.last_fast is not None) and (self.last_slow is not None)

    def snapshot(self) -> dict:
        return {
            "fast_ma": self.last_fast,
            "slow_ma": self.last_slow,
            "n": len(self.buf),
            "ready": self.is_ready(),
        }

# === Helper: build_meta_from_df ===
def build_meta_from_df(df, i, min_adx=12, cooldown=5):
    """Extracts latest indicator snapshot from dataframe."""
    price     = float(df["close"].iloc[i])
    rsi_val   = float(df["rsi"].iloc[i])
    adx_val   = float(df["adx"].iloc[i])
    vwap_val  = float(df["vwap"].iloc[i])
    ema20_now = float(df["ema20"].iloc[i])
    ema50_now = float(df["ema50"].iloc[i])
    ema200_now = float(df["ema200"].iloc[i])

    ema20_prev = float(df["ema20"].iloc[i-1]) if i > 0 else ema20_now
    ema50_prev = float(df["ema50"].iloc[i-1]) if i > 0 else ema50_now

    # --- Derived / logic signals ---
    cross_up  = ema20_prev <= ema50_prev and ema20_now > ema50_now
    cross_dn  = ema20_prev >= ema50_prev and ema20_now < ema50_now
    ema_fast_slope = ema20_now - ema20_prev
    trend_ok  = ema50_now > ema200_now
    session_ok = True  # Placeholder for market-hours guard

    return {
        "bar_index": i,
        "close": price,
        "rsi": rsi_val,
        "adx": adx_val,
        "vwap": vwap_val,
        "ema20": ema20_now,
        "ema50": ema50_now,
        "ema200": ema200_now,
        "fast_ma_prev": ema20_prev,
        "slow_ma_prev": ema50_prev,
        "fast_ma": ema20_now,
        "slow_ma": ema50_now,
        # --- Added fields used by score_components() ---
        "ema_fast": ema20_now,
        "ema_slow": ema50_now,
        "ema_fast_slope": ema_fast_slope,
        "cross_up": cross_up,
        "cross_dn": cross_dn,
        "trend_ok": trend_ok,
        "ema50>200": trend_ok,
        "min_adx": min_adx,
        "cooldown": cooldown,
        "session_ok": session_ok,
    }

	

