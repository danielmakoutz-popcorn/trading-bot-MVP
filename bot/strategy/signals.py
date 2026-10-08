from bot.core.indicators import ema

MIN_ADX_DEFAULT = 18

# ATR sizing guardrails
MIN_ATR_PCT = 0.10
MAX_ATR_PCT = 5.00

# Exit helper defaults
TRAIL_MULT = 2.0
TIME_STOP = 30

def trend_ok(i, df, min_adx: int) -> bool:
    return bool(
        df['htf_up'].iloc[i] and
        df['ema50'].iloc[i] > df['ema200'].iloc[i] and
        df['adx'].iloc[i] >= min_adx and
        df['close'].iloc[i] > df['vwap'].iloc[i]
    )

def crossed_up(a_prev, a_now, b_prev, b_now) -> bool:
    return (a_prev <= b_prev) and (a_now > b_now)

def entry_signal(i, df, last_entry_i, *, min_adx: int, cooldown: int) -> bool:
    if last_entry_i is not None and (i - last_entry_i) < cooldown:
        return False
    if not trend_ok(i, df, min_adx):
        return False
    # pullback & reclaim of EMA20, optionally RSI > 50
    prev = i-1
    if prev < 0:
        return False
    cross = crossed_up(df['close'].iloc[prev], df['close'].iloc[i],
                       df['ema20'].iloc[prev], df['ema20'].iloc[i])
    if not cross:
        return False
    if 'rsi' in df.columns and df['rsi'].iloc[i] <= 50:
        return False
    return True

def size_by_atr_pct(atr_pct, equity: float, risk_fraction: float):
    """
    position sizing is based on ATR%
    - equity: total account equity
    - risk_fraction: fraction of equity to risk per trade
    - atr_pct: ATR as % of price
    """
    atr_pct = float(atr_pct)
    atr_pct = max(MIN_ATR_PCT, min(MAX_ATR_PCT, atr_pct))

    risk_dollars = equity * risk_fraction

    if atr_pct <= 0:
        return 0

    size = int(risk_dollars / atr_pct)
    return max(1, size)

def update_trailing_stop(entry_price, current_atr, trail_mult: float = TRAIL_MULT, cur_trail=None):
    stop_candidate = float(max(entry_price - trail_mult * current_atr, 0))
    # ratchet only upward (for longs)
    if cur_trail is None:
        return stop_candidate
    return max(cur_trail, stop_candidate)

def exit_signal(i, df, entry_i, entry_price, cur_trail):
    # rules: trailing ATR, EMA10<EMA20, price<VWAP, or time stop
    ema10 = ema(df['close'], 10).iloc[i]
    if cur_trail is not None and df['close'].iloc[i] <= cur_trail:
        return "trail_hit"
    if ema10 < df['ema20'].iloc[i]:
        return "ema_roll"
    if df['close'].iloc[i] < df['vwap'].iloc[i]:
        return "below_vwap"
    if entry_i is not None and (i - entry_i) >= TIME_STOP:
        return "time_stop"
    return None
	
