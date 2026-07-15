from __future__ import annotations

import pandas as pd


def ema_series(values: pd.Series, length: int) -> pd.Series:
    return values.astype(float).ewm(span=length, adjust=False, min_periods=length).mean()


def sma_series(values: pd.Series, length: int) -> pd.Series:
    return values.astype(float).rolling(length, min_periods=length).mean()


def rsi_series(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    average_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    average_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = average_gain / average_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))
    result = result.mask((average_loss == 0) & (average_gain > 0), 100.0)
    return result.mask((average_loss == 0) & (average_gain == 0), 50.0)


def _true_range(bars: pd.DataFrame) -> pd.Series:
    high = bars["High"].astype(float)
    low = bars["Low"].astype(float)
    previous_close = bars["Close"].astype(float).shift(1)
    return pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)


def atr_series(bars: pd.DataFrame, length: int = 14) -> pd.Series:
    return _true_range(bars).ewm(
        alpha=1 / length, adjust=False, min_periods=length
    ).mean()


def adx_frame(bars: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    high = bars["High"].astype(float)
    low = bars["Low"].astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr = atr_series(bars, length)
    plus_smoothed = plus_dm.ewm(
        alpha=1 / length, adjust=False, min_periods=length
    ).mean()
    minus_smoothed = minus_dm.ewm(
        alpha=1 / length, adjust=False, min_periods=length
    ).mean()
    plus_di = 100 * plus_smoothed / atr.replace(0, float("nan"))
    minus_di = 100 * minus_smoothed / atr.replace(0, float("nan"))
    denominator = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / denominator
    adx = dx.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    return pd.DataFrame(
        {"adx": adx, "plus_di": plus_di, "minus_di": minus_di},
        index=bars.index,
    )


def cmf_series(bars: pd.DataFrame, length: int = 20) -> pd.Series:
    high = bars["High"].astype(float)
    low = bars["Low"].astype(float)
    close = bars["Close"].astype(float)
    volume = bars["Volume"].astype(float)
    price_range = high - low
    multiplier = (((close - low) - (high - close)) / price_range.replace(0, float("nan"))).fillna(0.0)
    flow = (multiplier * volume).rolling(length, min_periods=length).sum()
    rolling_volume = volume.rolling(length, min_periods=length).sum().replace(0, float("nan"))
    return flow / rolling_volume


def safe_latest(values: pd.Series, minimum_observations: int) -> float | None:
    if values.dropna().shape[0] < 1 or values.astype(float).notna().sum() < 1:
        return None
    source_observations = len(values.dropna())
    if len(values) < minimum_observations or source_observations == 0:
        return None
    latest = values.iloc[-1]
    return None if pd.isna(latest) else float(latest)


def return_over_period(close: pd.Series, periods: int) -> float | None:
    clean = close.astype(float).dropna()
    if len(clean) <= periods:
        return None
    start = float(clean.iloc[-periods - 1])
    end = float(clean.iloc[-1])
    if start == 0:
        return None
    return end / start - 1
