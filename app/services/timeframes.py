from __future__ import annotations

import pandas as pd

from app.schemas import FourHourStatus
from app.services.breakout_config import BreakoutConfig
from app.services.indicators import atr_series, ema_series, rsi_series, safe_latest


def resample_hourly_to_four_hour(hourly: pd.DataFrame) -> pd.DataFrame:
    if hourly.empty:
        return hourly.copy()
    rows: list[dict[str, float]] = []
    indices: list[pd.Timestamp] = []
    for _, session in hourly.sort_index().groupby(hourly.sort_index().index.date):
        for start in range(0, len(session), 4):
            bucket = session.iloc[start : start + 4]
            rows.append(
                {
                    "Open": float(bucket["Open"].iloc[0]),
                    "High": float(bucket["High"].max()),
                    "Low": float(bucket["Low"].min()),
                    "Close": float(bucket["Close"].iloc[-1]),
                    "Volume": float(bucket["Volume"].sum(min_count=1)),
                }
            )
            indices.append(pd.Timestamp(bucket.index[0]))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(indices))


def four_hour_confirmation(
    four_hour_bars: pd.DataFrame,
    daily_breakout_level: float | None,
    config: BreakoutConfig | None = None,
) -> FourHourStatus:
    config = config or BreakoutConfig()
    if daily_breakout_level is None or len(four_hour_bars) < config.four_hour_minimum_bars:
        return FourHourStatus.UNAVAILABLE
    close = four_hour_bars["Close"].astype(float)
    ema20 = safe_latest(ema_series(close, config.ema_short), config.ema_short)
    ema50 = safe_latest(ema_series(close, config.ema_medium), config.ema_medium)
    rsi = safe_latest(rsi_series(close, config.rsi_length), config.rsi_length)
    atr = safe_latest(atr_series(four_hour_bars, config.atr_length), config.atr_length)
    if None in (ema20, ema50, rsi, atr):
        return FourHourStatus.WAIT
    latest_close = float(close.iloc[-1])
    latest_low = float(four_hour_bars["Low"].iloc[-1])
    bullish = ema20 > ema50 and rsi > 50
    if latest_close < daily_breakout_level or ema20 <= ema50:
        return FourHourStatus.WEAK
    tolerance = atr * config.four_hour_retest_tolerance_atr
    if latest_low <= daily_breakout_level + tolerance and latest_close >= daily_breakout_level and bullish:
        return FourHourStatus.RETEST_HELD
    if latest_close > daily_breakout_level and bullish:
        return FourHourStatus.CONFIRMED
    return FourHourStatus.WEAK
