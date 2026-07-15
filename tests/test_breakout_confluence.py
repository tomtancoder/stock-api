import pytest

from app.schemas import BreakoutRating, BreakoutSetupState, DataStatus
from app.services.breakout_config import BreakoutConfig
from app.services.breakout_confluence import (
    calculate_breakout_confluence,
    determine_rating,
    score_entry_quality,
    score_momentum,
    score_volume,
)
from app.services.breakout_detection import BreakoutDetectionResult


def _detection(state=BreakoutSetupState.CONFIRMED_BREAKOUT):
    return BreakoutDetectionResult(
        state=state,
        level=100.0,
        window=55,
        buffer=0.5,
        breakout_index=None,
        breakout_bars_ago=2,
        close_location=0.8,
        base_depth_pct=0.1,
        atr_contracted=None,
        weak_breakout=False,
    )


@pytest.mark.parametrize(
    ("ratio", "cmf", "score"),
    [(1.1999, 0.05, 0), (1.2, 0.05, 1), (1.5, 0.05, 2), (1.5, 0.0501, 3)],
)
def test_volume_boundaries(ratio, cmf, score):
    assert score_volume(ratio, cmf).score == score


@pytest.mark.parametrize(
    ("rsi", "expected"),
    [(54.99, 1), (55, 2), (70, 2), (70.01, 1), (75.01, 0)],
)
def test_rsi_boundaries(rsi, expected):
    assert score_momentum(rsi, 19, 18, 30, 10).score == expected


def test_adx_point_requires_strength_rising_and_positive_direction():
    assert score_momentum(55, 20, 19, 30, 10).score == 3
    assert score_momentum(55, 20, 20, 30, 10).score == 2
    assert score_momentum(55, 20, 19, 10, 30).score == 2


@pytest.mark.parametrize(
    ("close", "atr", "expected"),
    [(100.75, 1.0, 2), (101.25, 1.0, 1), (101.26, 1.0, 0)],
)
def test_entry_extension_boundaries(close, atr, expected):
    component, *_ = score_entry_quality(close, 100, atr, BreakoutConfig())
    assert component.score == expected


def test_strong_rating_requires_completed_breakout_and_gates():
    kwargs = dict(
        total_score=16,
        state=BreakoutSetupState.CONFIRMED_BREAKOUT,
        breakout_score=4,
        trend_score=4,
        volume_score=2,
        entry_quality_score=2,
        close_above_ema200=True,
        ema200_rising=True,
        volume_ratio=1.5,
        extension_atr=0.5,
        rsi14=62,
        data_status=DataStatus.READY,
        config=BreakoutConfig(),
    )
    assert determine_rating(**kwargs)[0] == BreakoutRating.STRONG_SETUP
    kwargs["state"] = BreakoutSetupState.NO_VALID_SETUP
    assert determine_rating(**kwargs)[0] == BreakoutRating.WATCHLIST


@pytest.mark.parametrize(
    ("change", "flag"),
    [
        ({"close_above_ema200": False}, "PRICE_BELOW_EMA200"),
        ({"volume_ratio": 0.79}, "VOLUME_WEAK"),
        ({"extension_atr": 1.51}, "ENTRY_EXTENDED"),
        ({"rsi14": 78.1}, "RSI_OVEREXTENDED"),
        ({"data_status": DataStatus.PARTIAL}, "PARTIAL_DATA_CAP"),
    ],
)
def test_rating_caps(change, flag):
    kwargs = dict(
        total_score=16,
        state=BreakoutSetupState.CONFIRMED_BREAKOUT,
        breakout_score=4,
        trend_score=4,
        volume_score=2,
        entry_quality_score=2,
        close_above_ema200=True,
        ema200_rising=True,
        volume_ratio=1.5,
        extension_atr=0.5,
        rsi14=62,
        data_status=DataStatus.READY,
        config=BreakoutConfig(),
    )
    kwargs.update(change)
    rating, flags, explanations = determine_rating(**kwargs)
    assert rating == BreakoutRating.WATCHLIST
    assert flag in flags
    assert explanations


def test_failed_breakout_is_always_avoid():
    rating, *_ = determine_rating(
        total_score=18,
        state=BreakoutSetupState.FAILED_BREAKOUT,
        breakout_score=4,
        trend_score=4,
        volume_score=3,
        entry_quality_score=2,
        close_above_ema200=True,
        ema200_rising=True,
        volume_ratio=2,
        extension_atr=0.2,
        rsi14=60,
        data_status=DataStatus.READY,
        config=BreakoutConfig(),
    )
    assert rating == BreakoutRating.AVOID


def test_missing_benchmark_is_partial_and_insufficient_ema200_is_unrated():
    common = dict(
        detection=_detection(),
        close=101,
        ema20=99,
        ema50=95,
        ema200=90,
        ema200_prior=89,
        rsi14=60,
        adx14=25,
        adx14_prior=24,
        plus_di=30,
        minus_di=15,
        cmf20=0.1,
        volume_ratio=1.5,
        stock_return=0.2,
        benchmark_return=None,
        benchmark_regime=None,
        atr14=2,
        data_status=DataStatus.READY,
        config=BreakoutConfig(),
    )
    partial = calculate_breakout_confluence(**common)
    assert partial.data_status == DataStatus.PARTIAL
    assert partial.rating == BreakoutRating.WATCHLIST
    insufficient = calculate_breakout_confluence(**(common | {"ema200": None}))
    assert insufficient.data_status == DataStatus.INSUFFICIENT_HISTORY
    assert insufficient.rating is None
    assert insufficient.total_score is None
