import sys
import os, csv, datetime as dt
from typing import Dict, Any, Optional
from pathlib import Path
from more_itertools import chunked

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def _ts_iso(ts) -> str:
    if isinstance(ts, (dt.datetime, )):
        return ts.isoformat()
    return str(ts)

class ShadowLearner:
    _CSV_FIELDS = [
        "time","symbol",
        "open","high","low","close","volume",
        "rsi_prev","rsi_cur","fast_prev","fast_cur","slow_prev","slow_cur",
        "cross_up","cross_dn",
        "can_buy","can_sell","position_qty","avg_price",
        "signal_name","action","reason",
        "equity",
        "next_ret_pct", "ema20", "ema50", "ema200", "vwap", "adx",
        "rsi_ok", "adx_ok", "close_gt_vwap", "ema50_gt_ema200", "trend_ok",
        "entry_score", "comp_cross_up", "comp_adx_ok", "comp_rsi_ok", "comp_close_gt_vwap", "comp_trend_ok",
    ]

    def __init__(self,out_dir,file_prefix="shadow",flush_every=1,include_equity=True):
        self.out_dir = out_dir
        self.file_prefix = file_prefix
        self.flush_every = max(1,int(flush_every))
        self.include_equity = include_equity
        _ensure_dir(self.out_dir)
        self._pending: Dict[str,Dict[str,Any]] = {}
        self._writer: Optional[csv.DictWriter] = None
        self._fh = None
        self._rows_since_flush = 0
        self._open_writer_for_today()
        self.debug = print
        self.error = print

    def observe(self,*,ts,symbol:str,ohlcv:Dict[str,Any],indicators:Dict[str,Any],
                policy:Dict[str,Any],decision:Dict[str,Any],equity:Optional[float]=None):
        self._rotate_if_new_day()
        close_now = float(ohlcv.get("close",0.0))

        # finalize previous row for this symbol
        pend = self._pending.get(symbol)
        if pend is not None:
            prev_close = pend.get("last_close")
            target = None
            if prev_close and prev_close>0:
                target = ((close_now/prev_close)-1.0)*100.0
            pend["row"]["next_ret_pct"]=target
            self._write_row(pend["row"])
            self._pending.pop(symbol,None)

        # new pending row
        row = {
            "time":_ts_iso(ts),"symbol":symbol,
            "open":ohlcv.get("open"),"high":ohlcv.get("high"),
            "low":ohlcv.get("low"),"close":close_now,"volume":ohlcv.get("volume"),
            "rsi_prev":indicators.get("rsi_prev"),"rsi_cur":indicators.get("rsi_cur"),
            "fast_prev":indicators.get("fast_prev"),"fast_cur":indicators.get("fast_cur"),
            "slow_prev":indicators.get("slow_prev"),"slow_cur":indicators.get("slow_cur"),
            "cross_up":indicators.get("cross_up"),"cross_dn":indicators.get("cross_dn"),
            "can_buy":policy.get("can_buy"),"can_sell":policy.get("can_sell"),
            "position_qty":policy.get("position_qty"),"avg_price":policy.get("avg_price"),
            "signal_name":decision.get("signal_name"),"action":decision.get("action"),
            "reason":decision.get("reason"),
            "equity":float(equity) if (self.include_equity and equity is not None) else None,
            "next_ret_pct":None,
        }
        
        # --- add this block right after ---
        comps = indicators.get("score_components") or {}

        row.update({
            "ema20": indicators.get("ema20"),
            "ema50": indicators.get("ema50"),
            "ema200": indicators.get("ema200"),
            "vwap": indicators.get("vwap"),
            "adx": indicators.get("adx"),

            "rsi_ok": indicators.get("rsi_ok"),
            "adx_ok": indicators.get("adx_ok"),
            "close_gt_vwap": indicators.get("close>vwap")
                              if "close>vwap" in indicators
                              else ((indicators.get("close",0) > indicators.get("vwap",0))
                                    if "vwap" in indicators else None),
            "ema50_gt_200": indicators.get("ema50>200")
                              if "ema50>200" in indicators
                              else ((indicators.get("ema50",0) > indicators.get("ema200",0))
                                    if "ema200" in indicators else None),
            "trend_ok": indicators.get("trend_ok"),

            "entry_score": indicators.get("entry_score"),
            "comp_cross_up": comps.get("cross_up"),
            "comp_adx_ok": comps.get("adx_ok"),
            "comp_rsi_ok": comps.get("rsi_ok"),
            "comp_close_gt_vwap": comps.get("close_gt_vwap"),
            "comp_trend_ok": comps.get("trend_ok"),
        })
        self._pending[symbol]={"row":row,"last_close":close_now}

    def flush_all_pending_without_target(self):
        for sym,bundle in list(self._pending.items()):
            self._write_row(bundle["row"])
            self._pending.pop(sym,None)
        self._flush()

    def _open_writer_for_today(self):
        if self._fh: return
        fname=f"{self.file_prefix}-{dt.date.today().isoformat()}.csv"
        fpath=os.path.join(self.out_dir,fname)
        new_file=not os.path.exists(fpath)
        self._fh=open(fpath,"a",newline="")
        self._writer=csv.DictWriter(self._fh,fieldnames=self._CSV_FIELDS)
        if new_file: self._writer.writeheader()

    def _rotate_if_new_day(self):
        current_name=f"{self.file_prefix}-{dt.date.today().isoformat()}.csv"
        if not self._fh: self._open_writer_for_today(); return
        current_path=os.path.join(self.out_dir,current_name)
        if self._fh.name!=current_path:
            self._flush(); self._fh.close(); self._fh=None; self._writer=None
            self._open_writer_for_today()

    def _write_row(self,row:Dict[str,Any]):
        if not self._writer: self._open_writer_for_today()
        self._writer.writerow({k:row.get(k) for k in self._CSV_FIELDS})
        self._rows_since_flush+=1
        if self._rows_since_flush>=self.flush_every: self._flush()

    def _flush(self):
        if self._fh:
            self._fh.flush(); os.fsync(self._fh.fileno())
            self._rows_since_flush=0

    def close(self):
        self.flush_all_pending_without_target()
        if self._fh:
            try: self._fh.close()
            except Exception: pass

class ShadowPoller:
    def __init__(self, *, symbols, limit_bars, poll_seconds, shadow_logger, indicator_fn, fetch_fn, last_seen=None):
        self.symbols = list(symbols)
        self.limit = int(limit_bars)
        self.sleep = int(poll_seconds)
        self.logger = shadow_logger
        self.log = shadow_logger
        self.indicator = indicator_fn   # returns meta dict per bar
        self.last_seen = last_seen or {}   # map[symbol] -> latest ts we wrote
        self.fetch_fn = fetch_fn
        self.last_ts = {}
        self.debug = print
        #self._shadow_last_seen = {}

    def run_once(self):
        """
        Fetches the latest bars for each symbol and logs them to the shadow learner.
        Only logs the newest bar per symbol when it's newer than the last_seen timestamp.
        """

        # Fetch bars (provider returns DataFrame with MultiIndex: symbol → timestamp)
        bars_by_symbol = self.fetch_fn(self.symbols, self.limit)
        if not bars_by_symbol:
            self.log.debug("[SHADOW] No bars returned by fetch_fn")
            return

        for sym, hist in bars_by_symbol.items():
            try:
                if hist is None or hist.empty:
                    self.log.debug(f"[SHADOW] {sym} empty or no data")
                    continue

                # Ensure DataFrame is sorted by time
                hist = hist.sort_index()

                # --- Determine cutoff for new bars ---
                cutoff = self.last_seen.get(sym)
                last_bar_time = hist.index[-1]

                # Skip if no new bars since last_seen
                if cutoff is not None and last_bar_time <= cutoff:
                    self.log.debug(f"[SHADOW] {sym} no new bars since {cutoff}")
                    continue

                # --- Take only the newest bar
                self.last_seen[sym] = last_bar_time  # record timestamp for this symbol

                row = hist.iloc[-1]
                candle = {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0.0)),
                }

                # compute indicators on full history up to ts
                meta = {}
                if hasattr(self, "indicator"):
                    meta = self.indicator(sym, hist.loc[:last_bar_time]) or {}

                # --- Log to shadow observer ---
                self.logger.observe(
                    ts=last_bar_time.to_pydatetime(),
                    symbol=sym,
                    ohlcv=candle,
                    indicators=meta,
                    policy={"can_buy": True, "can_sell": False, "position_qty": 0.0, "avg_price": 0.0},
                    decision={
                        "signal_name": "shadow",
                        "action": meta.get("shadow_action", "hold"),
                        "reason": meta.get("shadow_reason", ""),
                    },
                    equity=None,
                )

                self.log.debug(f"[SHADOW] {sym} logged new bar at {last_bar_time}")

            except Exception as e:
                self.log.error(f"[SHADOW ERROR] {sym}: {e}", file=sys.stderr)

    def loop(self):
        import time, datetime

        while True:
            try:
                self.run_once()
            except Exception as e:
                print("Shadow poll error:", e)

            # --- Sleep until roughly the next full minute boundary ---
            try:
                now = datetime.datetime.utcnow()
                # seconds + fraction of seconds since the last whole minute
                offset = now.second + now.microsecond / 1e6
                # wait until the next full minute, minimum of 5s
                sleep_for = max(5, 60 - offset)
                time.sleep(sleep_for)
            except Exception:
                # if timing math fails, fallback to fixed sleep
                time.sleep(getattr(self, "sleep", 60))


