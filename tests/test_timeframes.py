import pandas as pd

from app.schemas import FourHourStatus
from app.services.timeframes import four_hour_confirmation, resample_hourly_to_four_hour


def test_resample_hourly_to_four_hour_never_crosses_sessions():
    timestamps = []
    for day in ("2026-07-13", "2026-07-14"):
        timestamps.extend(pd.date_range(f"{day} 09:30", periods=7, freq="1h", tz="America/New_York"))
    close = pd.Series(range(100, 114), index=pd.DatetimeIndex(timestamps), dtype=float)
    hourly = pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 100,
        }
    )
    result = resample_hourly_to_four_hour(hourly)
    assert len(result) == 4
    assert list(result.groupby(result.index.date).size()) == [2, 2]
    assert result.iloc[0]["Open"] == hourly.iloc[0]["Open"]
    assert result.iloc[0]["Close"] == hourly.iloc[3]["Close"]
    assert result.iloc[0]["Volume"] == 400


def _four_hour_bars(rows=70):
    index = pd.date_range("2026-01-01", periods=rows, freq="4h", tz="UTC")
    close = pd.Series([90 + i * 0.2 for i in range(rows)], index=index)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 200_000,
        }
    )


def test_four_hour_confirmation_statuses():
    bars = _four_hour_bars()
    level = float(bars["Close"].iloc[-1] - 1)
    assert four_hour_confirmation(bars, level) == FourHourStatus.CONFIRMED

    retest = bars.copy()
    retest.loc[retest.index[-1], "Low"] = level
    assert four_hour_confirmation(retest, level) == FourHourStatus.RETEST_HELD

    weak = bars.copy()
    weak.loc[weak.index[-1], "Close"] = level - 1
    assert four_hour_confirmation(weak, level) == FourHourStatus.WEAK
    assert four_hour_confirmation(bars.tail(59), level) == FourHourStatus.UNAVAILABLE
