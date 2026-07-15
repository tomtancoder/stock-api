import pandas as pd

from app.schemas import BreakoutSetupState
from app.services.breakout_config import BreakoutConfig
from app.services.breakout_detection import detect_breakout_state, prior_pivot
from app.services.indicators import atr_series
from tests.fixtures.breakout_bars import (
    make_base_bars,
    make_confirmed_breakout,
    make_failed_breakout,
    make_fresh_breakout,
    make_pre_breakout,
    make_retest,
)


def _detect(bars):
    return detect_breakout_state(bars, atr_series(bars), BreakoutConfig())


def test_breakout_fixtures_are_valid_ohlcv():
    for builder in (
        make_base_bars,
        make_fresh_breakout,
        make_confirmed_breakout,
        make_retest,
        make_failed_breakout,
        make_pre_breakout,
    ):
        bars = builder()
        assert len(bars) >= 220
        assert bars.index.is_monotonic_increasing
        assert set(["Open", "High", "Low", "Close", "Volume"]).issubset(bars)


def test_pivot_excludes_current_bar():
    high = pd.Series([1.0, 2.0, 100.0])
    pivot = prior_pivot(high, 2)
    assert pivot.iloc[-1] == 2.0


def test_detects_fresh_55_day_breakout():
    result = _detect(make_fresh_breakout())
    assert result.state == BreakoutSetupState.FRESH_BREAKOUT
    assert result.window == 55
    assert result.breakout_bars_ago == 0


def test_detects_confirmed_breakout():
    assert _detect(make_confirmed_breakout()).state == BreakoutSetupState.CONFIRMED_BREAKOUT


def test_detects_successful_retest():
    assert _detect(make_retest()).state == BreakoutSetupState.BREAKOUT_RETEST


def test_failed_breakout_has_priority():
    result = _detect(make_failed_breakout())
    assert result.state == BreakoutSetupState.FAILED_BREAKOUT
    assert "FAILED_BREAKOUT_COOLDOWN" in result.flags


def test_detects_pre_breakout_contracting_base():
    assert _detect(make_pre_breakout()).state == BreakoutSetupState.PRE_BREAKOUT


def test_wick_without_close_is_weak_breakout():
    bars = make_base_bars()
    level = bars["High"].iloc[:-1].tail(55).max()
    bars.loc[bars.index[-1], ["High", "Close", "Low"]] = [level + 1, level - 0.2, level - 1]
    result = _detect(bars)
    assert result.weak_breakout is True


def test_no_setup_when_price_is_far_from_pivot():
    bars = make_base_bars()
    bars.loc[bars.index[-1], ["Open", "High", "Low", "Close"]] = [80, 81, 79, 80]
    assert _detect(bars).state == BreakoutSetupState.NO_VALID_SETUP


def test_failed_breakout_cooldown_includes_seventh_bar():
    bars = make_base_bars()
    event_position = len(bars) - 10
    level = bars["High"].iloc[:event_position].tail(55).max()
    bars.loc[bars.index[event_position], ["Open", "High", "Low", "Close"]] = [
        level,
        level + 2,
        level - 0.2,
        level + 1.7,
    ]
    failure_position = len(bars) - 8
    bars.loc[bars.index[failure_position], ["Open", "High", "Low", "Close"]] = [
        level,
        level + 0.2,
        level - 2.5,
        level - 2,
    ]
    for position in range(failure_position + 1, len(bars)):
        bars.loc[bars.index[position], ["Open", "High", "Low", "Close"]] = [
            level + 0.3,
            level + 1.0,
            level,
            level + 0.5,
        ]
    assert _detect(bars).state == BreakoutSetupState.FAILED_BREAKOUT


def test_pre_breakout_selects_nearby_20_day_pivot_when_55_day_pivot_is_distant():
    bars = make_base_bars()
    bars.loc[bars.index[-40], "High"] = 110
    twenty_day_level = bars["High"].iloc[:-1].tail(20).max()
    bars.loc[bars.index[-1], ["Open", "High", "Low", "Close"]] = [
        twenty_day_level - 0.6,
        twenty_day_level - 0.1,
        twenty_day_level - 0.9,
        twenty_day_level - 0.4,
    ]
    result = _detect(bars)
    assert result.state == BreakoutSetupState.PRE_BREAKOUT
    assert result.window == 20
