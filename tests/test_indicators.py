import math

import pandas as pd

from app.services.indicators import (
    adx_frame,
    atr_series,
    cmf_series,
    ema_series,
    return_over_period,
    rsi_series,
    safe_latest,
)


def _bars(rows: int = 80) -> pd.DataFrame:
    close = pd.Series([100 + i * 0.4 + (i % 5) * 0.1 for i in range(rows)])
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": [100_000 + i * 100 for i in range(rows)],
        }
    )


def test_ema_200_requires_200_valid_closes():
    short = ema_series(pd.Series(range(1, 200), dtype=float), 200)
    ready = ema_series(pd.Series(range(1, 201), dtype=float), 200)
    assert safe_latest(short, minimum_observations=200) is None
    assert safe_latest(ready, minimum_observations=200) is not None


def test_indicators_are_bounded_and_aligned():
    bars = _bars()
    rsi = rsi_series(bars["Close"])
    atr = atr_series(bars)
    adx = adx_frame(bars)
    cmf = cmf_series(bars)
    assert rsi.dropna().between(0, 100).all()
    assert (atr.dropna() > 0).all()
    assert list(adx.columns) == ["adx", "plus_di", "minus_di"]
    assert adx.index.equals(bars.index)
    assert cmf.dropna().between(-1, 1).all()


def test_rsi_handles_no_losses_and_flat_prices():
    rising = rsi_series(pd.Series(range(30), dtype=float)).iloc[-1]
    flat = rsi_series(pd.Series([10.0] * 30)).iloc[-1]
    assert rising == 100
    assert flat == 50


def test_cmf_zero_range_and_zero_volume_do_not_produce_infinity():
    bars = _bars()
    bars.loc[:, "High"] = bars["Close"]
    bars.loc[:, "Low"] = bars["Close"]
    bars.loc[:, "Volume"] = 0
    result = cmf_series(bars)
    assert result.dropna().empty
    assert not any(math.isinf(value) for value in result.dropna())


def test_return_over_period_uses_exact_lookback():
    close = _bars()["Close"]
    assert return_over_period(close, 63) == close.iloc[-1] / close.iloc[-64] - 1
    assert return_over_period(close.tail(63), 63) is None
