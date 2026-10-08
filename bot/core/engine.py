import os
import asyncio
import traceback
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from os import getenv
from pathlib import Path
from time import monotonic
import pandas as pd

from bot.core.logs import setup_logging
from bot.core.db import DB
from bot.data.alpaca_provider import AlpacaProvider
from bot.broker.alpaca_broker import AlpacaBroker
#from bot.broker.paper_broker import PaperBroker
from bot.core.strategy import SMACrossover, build_meta_from_df
from bot.core.risk import position_size
from bot.core.utils import now_local
from bot.strategy.shadow_learner import ShadowLearner, ShadowPoller
from bot.core.indicators import ema, rsi, atr, adx, vwap_daily, add_indicators
from bot.core.trade_logs import append_trade, shadow_log, append_learning_bar
from bot.strategy.signals import size_by_atr_pct

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BUY_COOLDOWN = timedelta(minutes=2)
MAX_POSITION_DOLLARS = 10_000
DEFAULT_FORCE_FLAT_MINUTES = 1
ET = ZoneInfo("America/New_York")
CONTROL_DIR = Path("control")
CONTROL_DIR.mkdir(exist_ok=True)
FORCE_FILE = CONTROL_DIR / "force_flat.txt"

def check_force_flat(broker, log, tz, default_reason="manual"):
    if not FORCE_FILE.exists():
        return
    raw = FORCE_FILE.read_text().strip()
    FORCE_FILE.unlink(missing_ok=True)

    parts = raw.split()
    symbol = parts[0].upper() if parts and parts[0].lower() != "all" else None
    reason = " ".join(parts[1:]) if len(parts) > 1 else default_reason
    ts = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

    if symbol is None:
        positions = broker.list_positions()
    else:
        qty, _avg = broker.position_info(symbol)
        if qty and qty != 0:
            # fake a minimal position-like record
            positions = [{"symbol": symbol.upper(), "qty": qty}]
        else:
            positions = []

    if not positions:
        log(f"[{ts}] FORCE_FLAT no positions to close (symbol={symbol}) reason={reason}")
        return

    for p in positions:
        # supports either alpaca Position objects or our dict record above
        sym = getattr(p, "symbol", None) or (p.get("symbol") if isinstance(p, dict) else None)
        qty = float(getattr(p, "qty", 0) or (p.get("qty", 0) if isinstance(p, dict) else 0))
        if not sym or qty == 0:
            continue
        if qty > 0:
            oid = broker.market_sell(None, sym, qty, tag="FORCE_FLAT")
            log(f"[{ts}] FORCE_FLAT {sym} qty={qty} reason={reason} order_id={oid}")
        else:
            oid = broker.market_buy_to_cover(sym, abs(qty), tag="FORCE_FLAT")
            log(f"[{ts}] FORCE_FLAT_COVER {sym} qty={abs(qty)} reason={reason} order_id={oid}")

class TradingEngine:

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.log = setup_logging(cfg)
        self.tz = ZoneInfo(cfg.get("timezone", "America/Los_Angeles"))
        self.live_start_ts = None
        self.warmup_complete = False

        # Shadow learner (optional)
        shadow_cfg = cfg.get("shadow", {})
        self._shadow_last_seen = {}
        if shadow_cfg.get("enabled", False):
            out_dir = shadow_cfg.get("out_dir", "logs/shadow")
            os.makedirs(out_dir, exist_ok=True)
            self.shadow = ShadowLearner(
                out_dir=out_dir,
                file_prefix=shadow_cfg.get("file_prefix", "shadow"),
                flush_every=shadow_cfg.get("flush_every", 1),
                include_equity=shadow_cfg.get("include_equity", True),
            )
            self.log.info(f"Shadow learner initialized -> writing to {out_dir}")
        else:
            self.shadow = None

        db_path = cfg.get("database", []).get("path", "marketbot.db")
        self.db = DB(db_path)

        # Data provider
        st = cfg.get("strategy", {})
        dp = cfg.get("data_provider", {})

        sym_list = cfg.get("symbols") or st.get("symbols") or []   # prefer top-level symbols
        sym = st.get("symbol")                # fallback: single symbol under strategy
        print("ENGINE INIT cfg.symbols =", cfg.get("symbols"))
        print("ENGINE INIT st.symbols  =", st.get("symbols"))
        print("ENGINE INIT st.symbol   =", st.get("symbol"))


        if sym_list:
            self.symbols = [str(s).upper() for s in sym_list]
        elif sym:
            self.symbols = [str(sym).upper()]
        else:
            raise ValueError("No symbols configured. Add top-level `symbols: [...]`," " `strategy.symbols: [...]', or'strategy.symbol: TICKER]")
        # per-symbol state
        self.last_ts   = {s: None for s in self.symbols}
        self.last_price = {s: None for s in self.symbols}
        self.last_buy_at = {s: None for s in self.symbols}
        self.log.info(f"Symbols loaded:{self.symbols}")
        #sanity
        if not self.symbols:
            raise ValueError(f"No symbols parsed! st={st} dp={dp}")

        name = dp.get("name", "alpaca").lower()
        self.log.info(f"Engine initialized for symbols {self.symbols}")
        print("DEBUG symbols from config:", self.symbols)
        print("DEBUG sym raw from strategy:", sym)
        print("DEBUG sym raw rom data_provider:", dp.get("symbols"))
        self.interval_seconds = int(dp.get("interval_seconds", 60))

        print(f"DEBUG - dp contetn: {dp}")
        print(f"DEBUG - provider name extracted: {name}")

        self.providers = {}
        name = (dp.get("name", "alpaca") or "").lower()

        if name == "csv":
            # if your CSVProvider takes a per-symbol path, store a template and init per symbol
            csv_path_tpl = dp.get("csv_path", "data/{symbol}.csv")
            for s in self.symbols:
                path = csv_path_tpl.format(symbol=s)
                self.providers[s] = CSVProvider(path)
            self.log.info(f"Engine initialized CSV providers for {self.symbols}")
        elif name == "alpaca":
            api_key = getenv("APCA_API_KEY_ID")
            api_secret = getenv("APCA_API_SECRET_KEY")
            if not api_key or not api_secret:
                raise ValueError("Missing Alpaca API key/secret in config.yaml")
            for s in self.symbols:
                self.providers[s] = AlpacaProvider(symbol=s, feed="iex", timeframe="1Min") # api_key, api_secret)
            self.log.info(f"Engine initialized Alpaca providers for {self.symbols}")
        else:
            raise ValueError(f"Unsupported data provider: {name}")

        # --- Strategy ---
        st = cfg.get("strategy", {})
        if st.get("name") != "sma_crossover":
            raise ValueError("Only 'sma_crossover' is implemented in this MVP")

        entry_threshold = float(st.get("ENTRY_THRESHOLD", 0.6))
        weights = st.get("WEIGHTS", {})
        scoring = st.get("SCORING", {})

        fast = int(st.get("fast_window", 20))
        slow = int(st.get("slow_window", 50))
        cooldown    = int(st.get("COOLDOWN", 10))
        min_adx     = int(st.get("MIN_ADX", 18))
        rsi_len     = int(st.get("RSI_LEN", 14))
        adx_len     = int(st.get("ADX_LEN", 14))
        atr_len     = int(st.get("ATR_LEN", 14))
        min_adx_pad = int(st.get("MIN_ADX_PAD", 0))
        cross_eps   = float(st.get("CROSS_EPS_PCT", 0.001))     # tolerance for cross
        use_close20 = bool(st.get("USE_CLOSE_OVER_EMA20", True))# alt cross rule
        tp_pct      = float(st.get("TP_PCT", 0.04))
        sl_pct      = float(st.get("SL_PCT", 0.03))
        symbols = self.symbols  # Use the single authoritative symbol list parsed earlier

        # one persistent strategy instance per symbol
        self.strategies: dict[str, SMACrossover] = {
            sym: SMACrossover(fast_window=fast,
                slow_window=slow,
                cooldown=cooldown,
                min_adx=min_adx,
                rsi_len=rsi_len,
                adx_len=adx_len,
                atr_len=atr_len,
                min_adx_pad=min_adx_pad,
                cross_eps_pct=cross_eps,
                use_close_over_ema20=use_close20,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                entry_threshold=entry_threshold,
                weights=weights,
                scoring=scoring,
            ) 
            for sym in symbols
        }

        # Risk
        rk = cfg.get("risk", {})
        self.risk_fraction = float(rk.get("risk_fraction", 0.10))
        self.equity = float(rk.get("cash_start", 10_000.0))
        self.force_flat_enabled = bool(rk.get("force_flat_enabled", False))
        self.force_flat_minutes_before_close = float(
            rk.get("force_flat_minutes_before_close", DEFAULT_FORCE_FLAT_MINUTES)
        )
        self.force_flat_seconds = int(self.force_flat_minutes_before_close * 60)

        # Real-money / paper-money sizing controls
        self.trade_fraction = float(rk.get("trade_fraction", 0.01))      # 1% of buying power
        self.min_trade_dollars = float(rk.get("min_trade_dollars", 5.00))
        self.max_trade_dollars = float(rk.get("max_trade_dollars", 25.00))

        # Broker
        br = cfg.get("broker", {})
        bname = (br.get("name", "") or "").lower()

        if bname == "alpaca":
            alp = br.get("alpaca", {})
            api_key    = alp.get("key")    or getenv("APCA_API_KEY_ID")
            api_secret = alp.get("secret") or getenv("APCA_API_SECRET_KEY")
            base_url   = alp.get("base_url", "https://paper-api.alpaca.markets")
            if not api_key or not api_secret:
                raise ValueError("Missing Alpaca API key/secret in engine")
            self.broker = AlpacaBroker(self.db, self.log, api_key, api_secret, base_url=base_url)
            #sanity check
            self.log.info(
                "Broker Debug: type=%s list_positions=%s get_position=%s client=%s module=%s",
                type(self.broker).__name__,
                hasattr(self.broker, "list_positions"),
                hasattr(self.broker, "get_position"),
                hasattr(self.broker, "client"),
                self.broker.__class__.__module__,
            )

        elif bname == "paper":
            from .broker import PaperBroker
            self.broker = PaperBroker(self.db, self.log, start_cash=self.equity)

        else:
            raise ValueError(f"Unsupported broker: {br.get('name')}")

        # symbols and polling params
        poll_syms = shadow_cfg.get("symbols", self.symbols)
        poll_seconds = int(shadow_cfg.get("poll_seconds", 60))
        limit_bars = int(shadow_cfg.get("limit_bars", 200))

        def _fetch_fn(syms, lim):
            syms = [str(s).upper() for s in syms]
            return self.fetch_history(syms, limit=lim)

        if not shadow_cfg.get("enabled", False) or not poll_syms:
            self.shadow_poller = None
        else:
            # lightweight strategy for scoring only
            score_only = SMACrossover(
                fast_window=fast,
                slow_window=slow,
                cooldown=cooldown,
                min_adx=min_adx,
                rsi_len=rsi_len,
                adx_len=adx_len,
                atr_len=atr_len,
                min_adx_pad=min_adx_pad,
                cross_eps_pct=cross_eps,
                use_close_over_ema20=use_close20,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                entry_threshold=entry_threshold,
                weights=weights,
                scoring=scoring,
            )

            def indicator_fn(sym, df):
                if df is None or df.empty or len(df) < score_only.slow_n:
                    return {}

                df = df.copy()
                df = add_indicators(df).bfill().ffill()

                if len(df) < score_only.slow_n:
                    return {}

                i = len(df) - 1
                meta = build_meta_from_df(
                    df,
                    i,
                    min_adx=score_only.min_adx,
                    cooldown=score_only.cooldown,
                )

                score, detail = score_only._entry_confidence(meta)

                meta["entry_score"] = score
                meta["score_components"] = detail["components"]
                meta["shadow_action"] = "buy" if score >= score_only.entry_threshold else "hold"
                meta["shadow_reason"] = "score>=threshold" if score >= score_only.entry_threshold else ""

                return meta

            self.shadow_poller = ShadowPoller(
                fetch_fn=_fetch_fn,
                symbols=poll_syms,
                limit_bars=limit_bars,
                poll_seconds=poll_seconds,
                shadow_logger=self.shadow,
                indicator_fn=indicator_fn,
                last_seen=None
            )

            import threading
            t = threading.Thread(target=self.shadow_poller.loop, daemon=True)
            t.start()
            self.log.info(f"ShadowPoller running on {len(poll_syms)} symbols every {poll_seconds}s")

        # Hours
        th = cfg.get("trading_hours", {})
        self.start_hhmm = th.get("start", "06:30")
        self.end_hhmm   = th.get("end",   "13:00")
        self.start_time = time.fromisoformat(self.start_hhmm)
        self.end_time   = time.fromisoformat(self.end_hhmm)

        self._running = True
        self.last_ts = {s: None for s in self.symbols}

        self.log.info(f"Engine initialized for symbols: {self.symbols}")

    def _within_hours(self, ts_local):
        t = ts_local.timetz()
        return self.start_time <= t.replace(tzinfo=None) <= self.end_time

#    def preload_history(self):
 #       cfg = self.cfg['bootstrap']
  #      n  = int(cfg.get('warmup_bars', 200))
   #     tf = cfg.get('timeframe', '1Min')
    #    feed = cfg.get('feed', 'iex')

        # figure out minimum lookback needed for all strategies
      #  ind_need = 0
       # for strat in self.strategies.values():
#            ind_need = max(
 #               ind_need,
  #              getattr(strat, 'fast_n', 0),
   #             getattr(strat, 'slow_n', 0),
    #            getattr(strat, 'rsi_len', 0),
     #       )
      #  need_total = n + ind_need + int(cfg.get('safety_margin_bars', 5))

 #       now_utc = datetime.now(timezone.utc)

 #       for sym in self.symbols:
  #          try:
   #             provider = self.providers[sym]
    #            strat = self.strategies[sym]
#
 #               hist = provider.history(n=need_total, end=now_utc)
  #              if hist is None or hist.empty:
   #                 self.log.warn(f"[WARMUP] {sym}: empty history; skipping")
    #                continue
#
 #               # feed candles straight into the strategy
  #              for ts, row in hist.iterrows():
   #                 candle = type("Candle", (), {})()
    #                candle.ts = ts
     #               candle.open = float(row["open"])
      #              candle.high = float(row["high"])
       #             candle.low = float(row["low"])
        #            candle.close = float(row["close"])
         #           candle.volume = float(row.get("volume", 0.0))
          #          strat.on_candle(candle, symbol=sym)
#
 #               self._last_ts[sym] = hist.index[-1]
  #              self.log.info(f"[WARMUP_OK] {sym}: seeded {len(hist)} bars, last={hist.index[-1].isoformat()}")
#
 #           except Exception as e:
  #              self.log.error(f"[WARMUP_ERR] {sym}: {e}")

        # Flip a flag so the live loop knows warmup is done
   #     self.warmup_complete = True

    def fetch_history(self, symbols, limit=200, timeframe="1Min", feed="iex"):
        """Reuse provider.history to fetch latest bars for multiple symbols."""
        now_utc = datetime.now(timezone.utc)
        out = {}

        # local aliases
        cfg = self.cfg
        feed = cfg.get("feed", feed)
        timeframe = cfg.get("timeframe", timeframe)

        for s in symbols:
            try:
                #ensure a provider exists for this symbol
                if s not in self.providers:
                    self.log.warning(f"[SHADOW_MISS] no provider for {s}; creating")
                    self.providers[s] = AlpacaProvider(
                        symbol=s, feed=feed, timeframe=timeframe
                    )

                prov = self.providers[s]
                hist = prov.history(n=limit)
                if hist is None or hist.empty:
                    self.log.debug(f"[FETCH] {s}: empty history; skipping")
                    continue

                hist = hist.rename(columns=str.lower)
                if not isinstance(hist.index, pd.DatetimeIndex):
                    hist.index = pd.to_datetime(hist.index, utc=True)

                hist = hist.sort_index()
                out[s] = hist

            except Exception as e:
                self.log.warning(f"[FETCH_ERR] missing provider or data for {s}: {e}\n{traceback.format_exc()}")

        return out

    def on_bar(self, sym, bar, *, is_live: bool):
        # indicators compute every bar (historical + live)
        sig = self.indicators.update(sym, bar)

        # Only evaluate trade logic on live, closed bars AND after warmup
        if not is_live or not getattr(self, 'warmup_complete', False):
            if self.shadow and self.shadow.enabled:
                self.shadow.log(sym, bar, sig, phase="warmup")
            return

        # Normal trade path (same as now)
        self.evaluate_trades(sym, bar, sig)

    async def start(self):
        self.log.info(f"Starting engine (live mode, no warmup)...")
        #self.log.info(f"enginestarting... bootstrapping warmup")
        #self.preload_history()
        self.live_start_ts = datetime.now(timezone.utc)
        self.warmup_complete = True
        #self.warmup_complete = True
        await self.run_forever()

    async def run_forever(self):
        self.log.info(f"Starting loop @ %ss cadence", self.interval_seconds)
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                self.log.exception("Tick error: %s", e)
            await asyncio.sleep(self.interval_seconds)

    async def _tick(self):
        # --- optional force-flat hook you already had ---
        check_force_flat(self.broker, lambda m: self.log.info(m), self.tz)

        # respect trading hours up front
        ts_local = datetime.now(self.tz)
        if not self._within_hours(ts_local):
            self.log.info(f"No new data; sleeping...")
            return

        # ========== PER-SYMBOL PROCESSING ==========
        mtm_total = 0.0  # mark-to-market value across ALL symbols this tick

        for sym in self.symbols:
            try:
                provider = self.providers[sym]
                candle = await provider.next_candle()   # 1-min bar for this symbol
                if not candle:
                    continue

                ts = candle.ts
                ts_naive = ts.replace(tzinfo=None)

                # per-symbol "new candle" guard
                if self.last_ts[sym] is not None and ts_naive <= self.last_ts[sym]:
                    self.log.info(f"[{sym}] No new candle yet; last={self.last_ts[sym]}, got={ts_naive}")
                    continue

                self.last_ts[sym] = ts_naive
                price = float(getattr(candle, "close", 0.0) or 0.0)
                self.last_price[sym] = price

                qty_live, avg_live = self.broker.position_info(sym)
                in_pos = qty_live > 0

                learning_order_action = ""
                learning_order_id = ""
                learning_skip_reason = ""
                learning_tp_hit = False
                learning_sl_hit = False
                learning_pnl_pct = (
                    (price - avg_live) / avg_live
                    if in_pos and avg_live > 0
                    else 0.0
                )

                # ---------- STRATEGY ----------
                # after you set: price = float(getattr(candle, "close", 0.0) or 0.0)

                strat = self.strategies[sym]          # <- persistent strategy for this symbol
                if hasattr(strat, "on_candle"):
                    signal = strat.on_candle(candle, symbol=sym)
                else:
                    price = float(getattr(candle, "close", 0.0))
                    signal = strat.on_price(price)

                # 1) update + decide in one step (your SMACrossover.on_price does both)
                #signal = strat.on_price(price)

                # 2) pull a snapshot for logging
                snap = strat.snapshot()  # {'fast_ma':..., 'slow_ma':..., 'n':..., 'ready':...}

                # 3) (optional) gate trading until indicators ready
                if not strat.is_ready():
                    self.log.info(f"[{sym}] warming up... n={snap['n']}/{strat.slow_n}")
                    continue

                # 4) your debug line
                self.log.info(
                    f"[SIG] [{sym}] price={price:.2f} "
                    f"fast_ma={snap.get('fast_ma','NA')} slow_ma={snap.get('slow_ma','NA')} "
                    f"n={snap.get('n','NA')} ready={snap.get('ready','NA')} "
                    f"action={getattr(signal, 'action', 'NA')}"
                )

                # make sure no fake trades on history bars
                is_live = getattr(candle, "is_live", False) or (ts >= self.live_start_ts)
                if not is_live or not getattr(self, "warmup_complete", False):
                    self.log.info(
                        f"[TRADE_SKIP] [{sym}] action={getattr(signal, 'action', None)} "
                        f"is_live={is_live} warmup_complete={getattr(self, 'warmup_complete', False)} "
                        f"bar_ts={ts} live_start={self.live_start_ts}"
                    )
                    continue

                def write_learning_row(skip_reason=None):
                    try:
                        sig_meta = getattr(signal, "meta", {}) or {}
                        components = sig_meta.get("components", {}) or {}
                        entry_score = sig_meta.get("entry_score")

                        append_learning_bar(
                            candle.ts,
                            sym,
                            price,
                            signal_action=getattr(signal, "action", "NA"),
                            entry_score=entry_score,
                            components=components,
                            position_qty=qty_live,
                            avg_price=avg_live,
                            pnl_pct=learning_pnl_pct,
                            tp_hit=learning_tp_hit,
                            sl_hit=learning_sl_hit,
                            order_action=learning_order_action,
                            order_id=str(learning_order_id) if learning_order_id else "",
                            skip_reason=skip_reason or learning_skip_reason,
                        )
                    except Exception as e:
                        self.log.warning(f"[LEARNING_LOG_ERR] {sym} failed: {e}")

                # ---------- ENGINE EXIT CHECK ----------
                if in_pos:
                    pnl_pct = (price - avg_live) / avg_live if avg_live > 0 else 0.0

                    tp_hit = pnl_pct >= 0.02   # +2% take-profit
                    sl_hit = pnl_pct <= -0.01  # -1% stop-loss

                    self.log.info(
                        f"[SELL_CHECK] [{sym}] qty={qty_live:.6f} avg={avg_live:.2f} "
                        f"last={price:.2f} pnl_pct={pnl_pct:.4f} "
                        f"tp_hit={tp_hit} sl_hit={sl_hit}"
                    )

                    if tp_hit or sl_hit:
                        reason = "tp_hit" if tp_hit else "sl_hit"

                        oid = self.broker.market_sell(candle.ts, sym, qty_live, price=None)

                        learning_tp_hit = tp_hit
                        learning_sl_hit = sl_hit
                        learning_order_action = "SELL"
                        learning_order_id = oid
                        write_learning_row()
        # Submit the actual sell FIRST.
        # Trade logging must never block an emergency exit.

                        self.log.info(
                            f"[SELL] [{sym}] reason={reason} qty={qty_live:.6f} "
                            f"avg={avg_live:.2f} last={price:.2f} "
                            f"pnl_pct={pnl_pct:.4f} order_id={oid}"
                        )

        # Trade log is best-effort only.
        # Use positional args because append_trade rejected keyword args like ts=...
                        try:
                            append_trade(
                                candle.ts,
                                sym,
                                "SELL",
                                qty_live,
                                price,
                                reason,
                                "",
                                {
                                    "avg_price": avg_live,
                                    "pnl_pct": pnl_pct,
                                    "order_id": str(oid) if oid is not None else "",
                                },
                            )
                        except Exception as e:
                            self.log.warning(f"[TRADE_LOG_ERR] SELL {sym} failed: {e}")

                        continue

                # ---------- BUY PATH ----------
                if getattr(signal, "action", None) == "buy":
                    if in_pos:
                        self.log.info(f"[BUY_SKIP] {sym} already in position qt={qty_live:.6f}")
                        learning_skip_reason = "already_in_position"
                        write_learning_row()
                        continue
                    buying_power = float(getattr(self.broker, "buying_power", 0.0))

                    trade_budget = buying_power * self.trade_fraction
                    #trade_budget = min(trade_budget, self.max_trade_dollars)

                    if trade_budget < self.min_trade_dollars:
                        self.log.info(
                            f"SKIP BUY [{sym}] buying_power={buying_power:.2f} "
                            f"budget={trade_budget:.2f} below_min={self.min_trade_dollars:.2f}"
                        )
                        learning_skip_reason = "below_min_trade_dollars"
                        write_learning_row()
                        continue

                    qty = round(trade_budget / price, 6)

                    pos_qty = float(qty_live or 0.0)
                    order_value = float(qty) * price
                    within_cap = (pos_qty * price + order_value) <= MAX_POSITION_DOLLARS
                    ok_by_cooldown = (self.last_buy_at[sym] is None) or (
                        ts_local - self.last_buy_at[sym] >= BUY_COOLDOWN
                    )

                    if qty > 0 and within_cap and ok_by_cooldown:
                        oid = self.broker.market_buy(candle.ts, sym, qty, price)
                        self.last_buy_at[sym] = ts_local

                        learning_order_action = "BUY"
                        learning_order_id = oid

                        self.log.info(
                            f"[BUY] [{sym}] qty={qty:.6f} budget={trade_budget:.2f} "
                            f"buying_power={buying_power:.2f} @ ~{price:.2f} order_id={oid}"
                        )

                        try:
                            append_trade(
                                candle.ts,
                                sym,
                                "BUY",
                                qty,
                                price,
                                getattr(signal, "reason", "entry"),
                                "",
                                {
                                    **(signal.meta or {}),
                                    "order_id": str(oid) if oid is not None else "",
                                    "buying_power": buying_power,
                                    "trade_budget": trade_budget,
                                    "order_value": order_value,
                                },
                            )
                        except Exception as e:
                            self.log.warning(f"[TRADE_LOG_ERR] BUY {sym} failed: {e}")
                    else:
                        reasons = []
                        if qty <= 0: reasons.append("qty<=0")
                        if not within_cap: reasons.append("cap")
                        if not ok_by_cooldown: reasons.append("cooldown")
                        learning_skip_reason = "|".join(reasons)
                        self.log.info(
                            f"SKIP BUY [{sym}] buying_power={buying_power:.2f} "
                            f"budget≈{trade_budget:.2f} qty={qty:.6f} "
                            f"ord_val≈{order_value:.2f} last≈{price:.2f} ({'|'.join(reasons)})"
                        )

                # ---------- SELL PATH ----------
#                elif getattr(signal, "action", None) == "sell":
 #                   # authority: broker truth only
  #                  if not in_pos:
   #                     self.log.info(f"[SELL_SKIP] {sym} broker flat; ignoring sell intent")
    #                    continue

     #               self.log.info(f"[SELL_CHECK] [{sym}] qty={qty_live:.6f} avg_price={avg_live:.2f}")
      #              pnl = (price - avg_live) * qty_live

       #             append_trade(
        #                ts=candle.ts,
         #               sym=sym,
          #              event="SELL",
           #             qty=qty_live,
            #            price=price,
             #           reason=getattr(signal, "reason", "exit"),
              #          extra={"avg_price": avg_live, "pnl": pnl, **(signal.meta or {})},
               #     )

                #    self.broker.market_sell(candle.ts, sym, qty_live, price=None)

                # ---------- AI LEARNING BAR LOG ----------
                write_learning_row()

                # ---------- Shadow snapshot (your existing block, symbolized) ----------
                if self.shadow and self.shadow_poller is None:
                    ohlcv = {
                        "open": float(getattr(candle, "open", 0.0) or 0.0),
                        "high": float(getattr(candle, "high", 0.0) or 0.0),
                        "low":  float(getattr(candle, "low", 0.0) or 0.0),
                        "close": float(getattr(candle, "close", 0.0) or 0.0),
                        "volume": float(getattr(candle, "volume", 0.0) or 0.0),
                    }
                    indicators = {
                        "fast_cur": snap.get("fast_ma"),
                        "slow_cur": snap.get("slow_ma"),
                        "cross_up":  (getattr(signal, "action", None) == "buy"),
                        "cross_dn":  (getattr(signal, "action", None) == "sell"),
                    }
                    policy = {
                        "can_buy": not in_pos,
                        "can_sell": in_pos,
                        "position_qty": float(qty_live),
                        "avg_price": float(avg_live),
                    } 
                    decision = { "signal_name": "sma_crossover", "action": getattr(signal, "action", None), "reason": None }
                    self.shadow.observe(
                        ts=candle.ts,
                        symbol=sym,
                        ohlcv=ohlcv,
                        indicators=indicators,
                        policy=policy,
                        decision=decision,
                        equity=getattr(self, "equity", None),
                    )

                # accumulate mark-to-market for portfolio equity update
                mtm_total += float(qty_live or 0.0) * price

            except Exception as e:
                self.log.exception(f"[{sym}] Tick error: {e}")

        # ========== PORTFOLIO-LEVEL ACCOUNTING (once per tick) ==========
        # Update mark-to-market equity (cash + open positions)
        # 'cash' lives at broker; mtm_total computed above across all symbols
        try:
            self.equity = float(self.broker.cash + mtm_total)
        except Exception:
            # fallback if broker.cash not available
            self.equity = float(getattr(self, "equity", 0.0))

        # Persist equity snapshot (you already had this)
        self.db.insert_equity(datetime.now(timezone.utc), self.equity)

        # Near-close force-flat (keep your logic, but per symbol)
        # Adjust the timezone you prefer; you had `now_et` before:
        now_et = datetime.now(ET)
        close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        secs_to_close = (close_et - now_et).total_seconds()

        if self.force_flat_enabled and 0 <= secs_to_close <= self.force_flat_seconds:
            for sym in self.symbols:
                qty_close, _avg_close = self.broker.position_info(sym)
                if qty_close > 0:
                    self.log.info(
                        f"FORCE_FLAT [{sym}] near close qty={qty_close:.4f} "
                        f"seconds_to_close={secs_to_close:.0f}"
                    )
                    self.broker.market_sell(datetime.now(timezone.utc), sym, qty_close, price=None)

    async def close(self):
        self._running = False
        self.db.close()
        if self.shadow:
            self.shadow.close()
