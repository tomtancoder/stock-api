from app.schemas import (
    BreakoutAnalysisResponse,
    BreakoutComponentScore,
    BreakoutIndicatorSnapshot,
    BreakoutLevelInfo,
    BreakoutRiskSnapshot,
    BreakoutSetupState,
    DataStatus,
)
from app.services.breakout_config import BreakoutConfig, default_benchmark_for
from app.services.breakout_analysis import analyze_breakout_frames, get_breakout_analysis
from tests.fixtures.breakout_bars import make_confirmed_breakout, make_fresh_breakout


def test_breakout_response_allows_ready_scored_result():
    response = BreakoutAnalysisResponse(
        symbol="ACME",
        exchange="NASDAQ",
        benchmark_symbol="SPY",
        as_of="2026-07-15",
        data_status=DataStatus.READY,
        rating="Strong Setup",
        setup_state=BreakoutSetupState.CONFIRMED_BREAKOUT,
        total_score=16,
        breakout=BreakoutComponentScore(score=4, max_score=4),
        trend=BreakoutComponentScore(score=4, max_score=4),
        volume=BreakoutComponentScore(score=2, max_score=3),
        momentum=BreakoutComponentScore(score=2, max_score=3),
        relative_strength=BreakoutComponentScore(score=2, max_score=2),
        entry_quality=BreakoutComponentScore(score=2, max_score=2),
        level=BreakoutLevelInfo(window=55, price=100.0, buffer=0.5),
        indicators=BreakoutIndicatorSnapshot(close=101.2),
        risk=BreakoutRiskSnapshot(
            invalidation_price=97.5,
            extension_atr=0.4,
            initial_risk_pct=0.0366,
        ),
    )
    assert response.total_score == 16


def test_breakout_response_represents_insufficient_data_without_avoid_rating():
    response = BreakoutAnalysisResponse(
        symbol="NEW",
        exchange="NASDAQ",
        benchmark_symbol="SPY",
        data_status=DataStatus.INSUFFICIENT_HISTORY,
        rating=None,
        setup_state=BreakoutSetupState.NO_VALID_SETUP,
        total_score=None,
    )
    assert response.rating is None
    assert response.total_score is None


def test_breakout_config_and_benchmark_defaults_are_stable():
    config = BreakoutConfig()
    assert sum((4, 4, 3, 3, 2, 2)) == 18
    assert config.minimum_daily_bars == 220
    assert default_benchmark_for("D05.SI") == "^STI"
    assert default_benchmark_for("MSFT") == "SPY"


def _benchmark():
    bars = make_fresh_breakout()
    bars.loc[:, "Volume"] = 1_000_000
    return bars


def test_analyze_breakout_frames_builds_six_component_18_point_response():
    result = analyze_breakout_frames(
        exchange="NASDAQ",
        symbol="ACME",
        stock_bars=make_confirmed_breakout(),
        benchmark_symbol="SPY",
        benchmark_bars=_benchmark(),
        config=BreakoutConfig(maximum_data_age_days=10_000),
    )
    components = [
        result.breakout,
        result.trend,
        result.volume,
        result.momentum,
        result.relative_strength,
        result.entry_quality,
    ]
    assert all(component is not None for component in components)
    assert sum(component.max_score for component in components) == 18
    assert result.total_score == sum(component.score for component in components)
    assert "intrinsic_value" not in result.model_dump()


def test_analysis_statuses_are_not_misrepresented_as_avoid():
    short = make_fresh_breakout().tail(219)
    insufficient = analyze_breakout_frames(
        exchange="NASDAQ",
        symbol="NEW",
        stock_bars=short,
        benchmark_symbol="SPY",
        benchmark_bars=_benchmark(),
        config=BreakoutConfig(maximum_data_age_days=10_000),
    )
    assert insufficient.data_status == DataStatus.INSUFFICIENT_HISTORY
    assert insufficient.rating is None
    assert insufficient.total_score is None

    stale_bars = make_fresh_breakout().copy()
    stale_bars.index = stale_bars.index - stale_bars.index[-1] + stale_bars.index[-1].replace(year=2020)
    stale = analyze_breakout_frames(
        exchange="NASDAQ",
        symbol="OLD",
        stock_bars=stale_bars,
        benchmark_symbol="SPY",
        benchmark_bars=_benchmark(),
        config=BreakoutConfig(maximum_data_age_days=5),
    )
    assert stale.data_status == DataStatus.STALE
    assert stale.rating is None


def test_missing_volume_or_benchmark_is_partial_and_capped():
    no_volume = make_confirmed_breakout()
    no_volume.loc[:, "Volume"] = float("nan")
    volume_result = analyze_breakout_frames(
        exchange="NASDAQ",
        symbol="ACME",
        stock_bars=no_volume,
        benchmark_symbol="SPY",
        benchmark_bars=_benchmark(),
        config=BreakoutConfig(maximum_data_age_days=10_000),
    )
    benchmark_result = analyze_breakout_frames(
        exchange="NASDAQ",
        symbol="ACME",
        stock_bars=make_confirmed_breakout(),
        benchmark_symbol="SPY",
        benchmark_bars=None,
        config=BreakoutConfig(maximum_data_age_days=10_000),
    )
    assert volume_result.data_status == DataStatus.PARTIAL
    assert benchmark_result.data_status == DataStatus.PARTIAL
    assert volume_result.rating in (None, "Avoid", "Watchlist")
    assert benchmark_result.rating in (None, "Avoid", "Watchlist")


def test_get_breakout_analysis_preserves_exchange_and_maps_sgx(monkeypatch):
    calls = []

    def fake_fetch(symbol):
        calls.append(symbol)
        return _benchmark() if symbol == "^STI" else make_confirmed_breakout()

    monkeypatch.setattr("app.services.breakout_analysis.fetch_daily_history", fake_fetch)
    result = get_breakout_analysis(
        exchange="SGX",
        symbol="D05",
        config=BreakoutConfig(maximum_data_age_days=10_000),
    )
    assert result.exchange == "SGX"
    assert result.symbol == "D05"
    assert calls == ["D05.SI", "^STI"]
