import pandas as pd


def make_base_bars() -> pd.DataFrame:
    index = pd.bdate_range(end="2026-07-15", periods=260)
    closes = []
    for position in range(260):
        if position < 190:
            closes.append(70 + position * 0.15)
        else:
            closes.append(98.8 + ((position % 10) - 5) * 0.08)
    close = pd.Series(closes, index=index, dtype=float)
    bars = pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.2,
            "Low": close - 1.2,
            "Close": close,
            "Volume": 200_000.0,
        },
        index=index,
    )
    return bars


def _breakout_level(bars: pd.DataFrame, position: int) -> float:
    return float(bars["High"].iloc[:position].tail(55).max())


def _set_bar(
    bars: pd.DataFrame,
    position: int,
    *,
    close: float,
    high: float,
    low: float,
    volume: float = 350_000.0,
) -> None:
    index = bars.index[position]
    bars.loc[index, ["Open", "High", "Low", "Close", "Volume"]] = [
        close - 0.5,
        high,
        low,
        close,
        volume,
    ]


def make_fresh_breakout() -> pd.DataFrame:
    bars = make_base_bars()
    level = _breakout_level(bars, -1)
    _set_bar(bars, -1, close=level + 1.5, high=level + 1.8, low=level - 0.3)
    assert bars["Close"].iloc[-1] > bars["High"].iloc[:-1].tail(55).max()
    return bars


def make_confirmed_breakout() -> pd.DataFrame:
    bars = make_base_bars()
    level = _breakout_level(bars, -3)
    _set_bar(bars, -3, close=level + 1.5, high=level + 1.8, low=level - 0.2)
    _set_bar(bars, -2, close=level + 3.0, high=level + 3.3, low=level + 2.0)
    _set_bar(bars, -1, close=level + 2.8, high=level + 3.2, low=level + 1.9)
    assert (bars["Close"].tail(3) > level).sum() >= 2
    return bars


def make_retest() -> pd.DataFrame:
    bars = make_base_bars()
    level = _breakout_level(bars, -6)
    _set_bar(bars, -6, close=level + 1.5, high=level + 1.8, low=level - 0.2)
    for position in (-5, -4, -3, -2):
        _set_bar(bars, position, close=level + 1.0, high=level + 1.4, low=level + 0.3)
    _set_bar(bars, -1, close=level + 0.8, high=level + 1.2, low=level - 0.1)
    assert bars["Low"].iloc[-1] <= level
    assert bars["Close"].iloc[-1] > level
    return bars


def make_failed_breakout() -> pd.DataFrame:
    bars = make_base_bars()
    level = _breakout_level(bars, -5)
    _set_bar(bars, -5, close=level + 1.5, high=level + 1.8, low=level - 0.2)
    _set_bar(bars, -4, close=level + 1.0, high=level + 1.3, low=level + 0.2)
    _set_bar(bars, -3, close=level + 0.8, high=level + 1.2, low=level + 0.1)
    _set_bar(bars, -2, close=level - 2.0, high=level + 0.2, low=level - 2.5)
    _set_bar(bars, -1, close=level - 1.4, high=level - 0.8, low=level - 2.0)
    assert bars["Close"].iloc[-2] < level
    return bars


def make_pre_breakout() -> pd.DataFrame:
    bars = make_base_bars()
    level = _breakout_level(bars, -1)
    _set_bar(
        bars,
        -1,
        close=level - 0.45,
        high=level - 0.1,
        low=level - 0.9,
        volume=180_000,
    )
    assert bars["Close"].iloc[-1] < level
    return bars
