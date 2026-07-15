from app.schemas import (
    BreakoutAnalysisResponse,
    BreakoutRating,
    BreakoutSetupState,
    DataStatus,
)
from app.services import screener
from app.services.breakout_config import BreakoutConfig
from tests.fixtures.breakout_bars import make_confirmed_breakout


def _result(
    symbol,
    *,
    state=BreakoutSetupState.CONFIRMED_BREAKOUT,
    rating=BreakoutRating.WATCHLIST,
    score=10,
    extension=0.5,
    volume_ratio=1.5,
):
    from app.schemas import BreakoutIndicatorSnapshot, BreakoutRiskSnapshot

    return BreakoutAnalysisResponse(
        symbol=symbol,
        exchange="SGX" if symbol.endswith(".SI") else "NASDAQ",
        benchmark_symbol="^STI" if symbol.endswith(".SI") else "SPY",
        data_status=DataStatus.READY,
        rating=rating,
        setup_state=state,
        total_score=score,
        indicators=BreakoutIndicatorSnapshot(volume_ratio=volume_ratio),
        risk=BreakoutRiskSnapshot(extension_atr=extension),
    )


def test_market_selects_only_requested_universe_and_fetches_benchmark_once(monkeypatch):
    calls = []
    monkeypatch.setattr(screener, "US_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(screener, "SG_TICKERS", ["D05.SI"])

    def fake_download(symbols, **kwargs):
        calls.append(list(symbols))
        bars = make_confirmed_breakout()
        return ({symbol: bars for symbol in symbols}, {})

    monkeypatch.setattr(screener, "download_daily_histories", fake_download)
    monkeypatch.setattr(
        screener,
        "analyze_breakout_frames",
        lambda **kwargs: _result(kwargs["symbol"]),
    )
    screener.clear_screener_cache()
    response = screener.run_breakout_screener(
        market="us",
        include_four_hour=False,
        config=BreakoutConfig(maximum_data_age_days=10_000),
    )
    assert response.scanned_count == 2
    assert calls == [["AAA", "BBB", "SPY"]]


def test_partial_failures_and_low_liquidity_do_not_abort_scan(monkeypatch):
    monkeypatch.setattr(screener, "US_TICKERS", ["GOOD", "THIN", "FAIL"])
    monkeypatch.setattr(screener, "SG_TICKERS", [])
    good = make_confirmed_breakout()
    thin = make_confirmed_breakout()
    thin.loc[:, "Volume"] = 10_000

    def fake_download(symbols, **kwargs):
        return (
            {"GOOD": good, "THIN": thin, "SPY": good},
            {"FAIL": "provider failed"},
        )

    monkeypatch.setattr(screener, "download_daily_histories", fake_download)
    monkeypatch.setattr(
        screener,
        "analyze_breakout_frames",
        lambda **kwargs: _result(kwargs["symbol"]),
    )
    screener.clear_screener_cache()
    response = screener.run_breakout_screener(
        market="us",
        include_four_hour=False,
        config=BreakoutConfig(maximum_data_age_days=10_000),
    )
    assert response.scanned_count == 3
    assert response.eligible_count == 1
    assert response.excluded_low_liquidity_count == 1
    assert [item.symbol for item in response.results] == ["GOOD"]
    assert response.errors[0].symbol == "FAIL"


def test_screener_sorting_and_limit_happen_after_filters(monkeypatch):
    monkeypatch.setattr(screener, "US_TICKERS", ["FRESH", "RETEST", "CONFIRMED"])
    monkeypatch.setattr(screener, "SG_TICKERS", [])
    bars = make_confirmed_breakout()
    monkeypatch.setattr(
        screener,
        "download_daily_histories",
        lambda symbols, **kwargs: ({symbol: bars for symbol in symbols}, {}),
    )
    values = {
        "FRESH": _result("FRESH", state=BreakoutSetupState.FRESH_BREAKOUT, score=16),
        "RETEST": _result("RETEST", state=BreakoutSetupState.BREAKOUT_RETEST, score=12),
        "CONFIRMED": _result(
            "CONFIRMED", state=BreakoutSetupState.CONFIRMED_BREAKOUT, score=14
        ),
    }
    monkeypatch.setattr(
        screener,
        "analyze_breakout_frames",
        lambda **kwargs: values[kwargs["symbol"]],
    )
    screener.clear_screener_cache()
    response = screener.run_breakout_screener(
        market="us",
        minimum_score=13,
        limit=2,
        include_four_hour=False,
        config=BreakoutConfig(maximum_data_age_days=10_000),
    )
    assert [item.symbol for item in response.results] == ["CONFIRMED", "FRESH"]
    assert response.returned_count == 2


def test_default_scan_retains_non_ready_results_without_calling_them_avoid(monkeypatch):
    monkeypatch.setattr(screener, "US_TICKERS", ["NEW"])
    monkeypatch.setattr(screener, "SG_TICKERS", [])
    bars = make_confirmed_breakout()
    monkeypatch.setattr(
        screener,
        "download_daily_histories",
        lambda symbols, **kwargs: ({symbol: bars for symbol in symbols}, {}),
    )
    non_ready = BreakoutAnalysisResponse(
        symbol="NEW",
        exchange="NASDAQ",
        benchmark_symbol="SPY",
        data_status=DataStatus.INSUFFICIENT_HISTORY,
        rating=None,
        setup_state=BreakoutSetupState.NO_VALID_SETUP,
        total_score=None,
    )
    monkeypatch.setattr(screener, "analyze_breakout_frames", lambda **kwargs: non_ready)
    screener.clear_screener_cache()
    default = screener.run_breakout_screener(
        market="us", include_four_hour=False, minimum_score=0
    )
    filtered = screener.run_breakout_screener(
        market="us", include_four_hour=False, minimum_score=1
    )
    assert [item.symbol for item in default.results] == ["NEW"]
    assert default.results[0].rating is None
    assert filtered.results == []


def test_four_hour_fetches_only_qualifying_daily_candidates(monkeypatch):
    from app.schemas import BreakoutLevelInfo, FourHourStatus

    monkeypatch.setattr(screener, "US_TICKERS", ["GOOD", "LOW", "PRE"])
    monkeypatch.setattr(screener, "SG_TICKERS", [])
    bars = make_confirmed_breakout()
    monkeypatch.setattr(
        screener,
        "download_daily_histories",
        lambda symbols, **kwargs: ({symbol: bars for symbol in symbols}, {}),
    )
    values = {
        "GOOD": _result("GOOD", score=9),
        "LOW": _result("LOW", score=8),
        "PRE": _result("PRE", state=BreakoutSetupState.PRE_BREAKOUT, score=12),
    }
    for value in values.values():
        value.level = BreakoutLevelInfo(window=55, price=100)
    monkeypatch.setattr(
        screener,
        "analyze_breakout_frames",
        lambda **kwargs: values[kwargs["symbol"]],
    )
    calls = []
    monkeypatch.setattr(
        screener,
        "fetch_hourly_history",
        lambda symbol: calls.append(symbol) or bars,
    )
    monkeypatch.setattr(screener, "resample_hourly_to_four_hour", lambda value: value)
    monkeypatch.setattr(
        screener,
        "four_hour_confirmation",
        lambda *args: FourHourStatus.CONFIRMED,
    )
    screener.clear_screener_cache()
    response = screener.run_breakout_screener(market="us", include_four_hour=True)
    assert calls == ["GOOD"]
    assert next(item for item in response.results if item.symbol == "GOOD").four_hour_status == FourHourStatus.CONFIRMED
