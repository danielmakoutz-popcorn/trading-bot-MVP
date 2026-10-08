import numpy as np
import pandas as pd
from datetime import datetime

EMA_FAST = 20
EMA_SLOW = 50
EMA_LONG = 200
RSI_LEN = 10
ATR_LEN = 14
ADX_LEN = 14

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, length=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    rs = up.ewm(alpha=1/length, adjust=False).mean() / \
         down.ewm(alpha=1/length, adjust=False).mean()
    return 100 - (100/(1+rs))

def true_range(df):
    prev_close = df['close'].shift(1)
    rng = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low'] - prev_close).abs()
    ], axis=1).max(axis=1)
    return rng

def atr(df, length=14):
    return true_range(df).rolling(length).mean()

def adx(df, length=14):
    up_move   = df['high'].diff()
    down_move = df['low'].diff().abs().shift(1) - df['low'].diff().abs()
    plus_dm  = ((df['high'] - df['high'].shift(1)).clip(lower=0)).where(
                (df['high'] - df['high'].shift(1)) > (df['low'].shift(1) - df['low']), 0.0)
    minus_dm = ((df['low'].shift(1) - df['low']).clip(lower=0)).where(
                (df['low'].shift(1) - df['low']) > (df['high'] - df['high'].shift(1)), 0.0)

    tr = true_range(df)
    atr_ = tr.rolling(length).sum()

    plus_di  = 100 * (plus_dm.rolling(length).sum()  / atr_)
    minus_di = 100 * (minus_dm.rolling(length).sum() / atr_)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_ = dx.rolling(length).mean()
    return adx_, plus_di, minus_di

def vwap_daily(df):
    # expects a DatetimeIndex
    pv = df['close'] * df['volume']
    return (pv.groupby(df.index.date).cumsum() / df['volume'].groupby(df.index.date).cumsum()).rename('vwap')

def add_indicators(df_1m, df_15m=None):
    df = df_1m.copy()

    # EMAs (consistent windows)
    df['ema20']  = ema(df['close'], EMA_FAST)
    df['ema50']  = ema(df['close'], EMA_SLOW)
    df['ema200'] = ema(df['close'], EMA_LONG)

    # ADX/DI
    adx_v, di_p, di_m = adx(df, ADX_LEN)
    df['adx']  = adx_v
    df['di_p'] = di_p
    df['di_m'] = di_m

    # ATR (+ percent of price for sizing if you use it)
    df['atr']     = atr(df, ATR_LEN)
    df['atr_pct'] = (df['atr'] / df['close']) * 100

    # Daily VWAP (reset each session)
    df['vwap'] = vwap_daily(df).reindex(df.index).ffill()

    # RSI
    df['rsi'] = rsi(df['close'], RSI_LEN)

    # Optional: 15m “HTF” slope of EMA200 mapped to 1m (if df_15m provided)
    if df_15m is not None:
        htf = df_15m[['close']].copy()

        # ensure 15m ema200 and its slope exist
        if 'ema200' not in htf.columns:
            htf['ema200'] = ema(htf['close'], EMA_LONG)
        htf['slope200'] = htf['ema200'].diff()

        # HTF trend flag (uptrend = slope up AND price above ema200)
        htf['htf_up'] = (htf['slope200'] > 0) & (htf['close'] > htf['ema200'])

        # map down to 1m timeline
        df['htf_ema200']   = htf['ema200'].reindex(df.index, method='ffill')
        df['htf_slope200'] = htf['slope200'].reindex(df.index, method='ffill')
        df['htf_up']       = htf['htf_up'].reindex(df.index, method='ffill').fillna(False)
    else:
        # fallback: approximate HTF from 1m
        df['htf_ema200']   = df['ema200']
        df['htf_slope200'] = df['ema200'].diff()
        df['htf_up']       = (df['htf_slope200'] > 0) & (df['close'] > df['ema200'])

    return df
