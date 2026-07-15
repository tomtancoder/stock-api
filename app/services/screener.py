from __future__ import annotations

from datetime import date
from time import monotonic
from typing import Literal

import pandas as pd
from app.schemas import (
    BreakoutAnalysisResponse,
    BreakoutRating,
    BreakoutScreenerError,
    BreakoutScreenerResponse,
    BreakoutSetupState,
    FourHourStatus,
)
from app.services.breakout_analysis import analyze_breakout_frames
from app.services.breakout_config import (
    BreakoutConfig,
    SG_BENCHMARK,
    US_BENCHMARK,
)
from app.services.market_data import (
    MarketDataError,
    download_daily_histories,
    fetch_hourly_history,
)
from app.services.timeframes import four_hour_confirmation, resample_hourly_to_four_hour
from app.ticker_universe import SG_TICKERS, US_TICKERS


_scan_cache: dict[tuple, tuple[float, BreakoutScreenerResponse]] = {}


def clear_screener_cache() -> None:
    _scan_cache.clear()


def run_breakout_screener(
    *,
    market: Literal["all", "us", "sg"] = "all",
    minimum_score: int = 0,
    rating: BreakoutRating | None = None,
    setup_state: BreakoutSetupState | None = None,
    maximum_extension_atr: float | None = None,
    include_four_hour: bool = True,
    limit: int = 200,
    config: BreakoutConfig | None = None,
) -> BreakoutScreenerResponse:
    config = config or BreakoutConfig()
    cache_key = (market, date.today().isoformat(), config)
    cached_entry = _scan_cache.get(cache_key)
    cached = None
    if cached_entry is not None:
        cached_at, cached_response = cached_entry
        if monotonic() - cached_at <= config.screener_cache_ttl_seconds:
            cached = cached_response
        else:
            _scan_cache.pop(cache_key, None)
    if cached is None:
        cached = _run_daily_scan(market, config)
        _scan_cache[cache_key] = (monotonic(), cached.model_copy(deep=True))
    base = cached.model_copy(deep=True)
    results = [
        item
        for item in base.results
        if (
            (item.total_score is None and minimum_score == 0)
            or (
                item.total_score is not None
                and item.total_score >= minimum_score
            )
        )
    ]
    if rating is not None:
        results = [item for item in results if item.rating == rating]
    if setup_state is not None:
        results = [item for item in results if item.setup_state == setup_state]
    if maximum_extension_atr is not None:
        results = [
            item
            for item in results
            if item.risk is not None
            and item.risk.extension_atr is not None
            and item.risk.extension_atr <= maximum_extension_atr
        ]
    results.sort(key=_sort_key)
    if include_four_hour:
        results = _add_four_hour_confirmations(results, config)
    results = results[:limit]
    return base.model_copy(
        update={
            "returned_count": len(results),
            "results": results,
        }
    )


def _run_daily_scan(
    market: Literal["all", "us", "sg"],
    config: BreakoutConfig,
) -> BreakoutScreenerResponse:
    universe = _universe(market)
    benchmarks = _benchmarks(market)
    download_symbols = [*universe, *[value for value in benchmarks if value not in universe]]
    histories, download_errors = download_daily_histories(
        download_symbols,
        batch_size=config.batch_size,
    )
    results: list[BreakoutAnalysisResponse] = []
    errors: list[BreakoutScreenerError] = []
    excluded_low_liquidity = 0
    warnings: list[str] = []
    for benchmark in benchmarks:
        if benchmark not in histories:
            warnings.append(f"Benchmark {benchmark} is unavailable; affected results are partial.")
    for yahoo_symbol in universe:
        bars = histories.get(yahoo_symbol)
        if bars is None:
            errors.append(
                BreakoutScreenerError(
                    symbol=yahoo_symbol,
                    error_type="market_data_error",
                    message=download_errors.get(yahoo_symbol, "No market data returned."),
                )
            )
            continue
        average_volume = _average_volume_50(bars)
        if (
            average_volume is not None
            and average_volume < config.minimum_average_volume_50
        ):
            excluded_low_liquidity += 1
            continue
        singapore = yahoo_symbol.endswith(".SI")
        exchange = "SGX" if singapore else "NASDAQ"
        public_symbol = yahoo_symbol[:-3] if singapore else yahoo_symbol
        benchmark = SG_BENCHMARK if singapore else US_BENCHMARK
        try:
            results.append(
                analyze_breakout_frames(
                    exchange=exchange,
                    symbol=public_symbol,
                    stock_bars=bars,
                    benchmark_symbol=benchmark,
                    benchmark_bars=histories.get(benchmark),
                    config=config,
                )
            )
        except Exception as exc:  # noqa: BLE001 - a single malformed ticker must not abort a scan.
            errors.append(
                BreakoutScreenerError(
                    symbol=yahoo_symbol,
                    error_type="analysis_error",
                    message=str(exc),
                )
            )
    as_of_values = [item.as_of for item in results if item.as_of]
    return BreakoutScreenerResponse(
        as_of=max(as_of_values) if as_of_values else None,
        market=market,
        benchmark_symbols=benchmarks,
        scanned_count=len(universe),
        eligible_count=len(results),
        excluded_low_liquidity_count=excluded_low_liquidity,
        returned_count=len(results),
        results=results,
        errors=errors,
        warnings=warnings,
    )


def _universe(market: str) -> list[str]:
    if market == "us":
        values = US_TICKERS
    elif market == "sg":
        values = SG_TICKERS
    else:
        values = [*US_TICKERS, *SG_TICKERS]
    return list(dict.fromkeys(value.strip().upper() for value in values))


def _benchmarks(market: str) -> list[str]:
    if market == "us":
        return [US_BENCHMARK]
    if market == "sg":
        return [SG_BENCHMARK]
    return [US_BENCHMARK, SG_BENCHMARK]


def _average_volume_50(bars: pd.DataFrame) -> float | None:
    if "Volume" not in bars:
        return None
    values = pd.to_numeric(bars["Volume"], errors="coerce").tail(50)
    average = values.mean()
    return None if pd.isna(average) else float(average)


_STATE_PRIORITY = {
    BreakoutSetupState.BREAKOUT_RETEST: 0,
    BreakoutSetupState.CONFIRMED_BREAKOUT: 1,
    BreakoutSetupState.FRESH_BREAKOUT: 2,
    BreakoutSetupState.PRE_BREAKOUT: 3,
    BreakoutSetupState.TREND_TRANSITION: 4,
    BreakoutSetupState.FAILED_BREAKOUT: 5,
    BreakoutSetupState.NO_VALID_SETUP: 6,
}
_RATING_PRIORITY = {
    BreakoutRating.STRONG_SETUP: 0,
    BreakoutRating.STARTER_SETUP: 1,
    BreakoutRating.WATCHLIST: 2,
    BreakoutRating.AVOID: 3,
    None: 4,
}


def _sort_key(item: BreakoutAnalysisResponse) -> tuple:
    extension = item.risk.extension_atr if item.risk else None
    volume_ratio = item.indicators.volume_ratio if item.indicators else None
    return (
        _STATE_PRIORITY[item.setup_state],
        _RATING_PRIORITY[item.rating],
        -(item.total_score if item.total_score is not None else -1),
        extension is None,
        extension if extension is not None else 0,
        volume_ratio is None,
        -(volume_ratio if volume_ratio is not None else 0),
        item.symbol,
    )


def _add_four_hour_confirmations(
    results: list[BreakoutAnalysisResponse],
    config: BreakoutConfig,
) -> list[BreakoutAnalysisResponse]:
    qualifying_states = {
        BreakoutSetupState.FRESH_BREAKOUT,
        BreakoutSetupState.CONFIRMED_BREAKOUT,
        BreakoutSetupState.BREAKOUT_RETEST,
    }
    remaining = config.four_hour_candidate_limit
    enriched: list[BreakoutAnalysisResponse] = []
    for item in results:
        qualifies = (
            remaining > 0
            and item.total_score is not None
            and item.total_score >= config.four_hour_candidate_min_score
            and item.setup_state in qualifying_states
            and item.level is not None
            and item.level.price is not None
        )
        if not qualifies:
            enriched.append(item)
            continue
        remaining -= 1
        yahoo_symbol = f"{item.symbol}.SI" if item.exchange == "SGX" else item.symbol
        try:
            hourly = fetch_hourly_history(yahoo_symbol)
            status = four_hour_confirmation(
                resample_hourly_to_four_hour(hourly), item.level.price, config
            )
            enriched.append(item.model_copy(update={"four_hour_status": status}))
        except MarketDataError as exc:
            enriched.append(
                item.model_copy(
                    update={
                        "four_hour_status": FourHourStatus.UNAVAILABLE,
                        "warnings": [*item.warnings, f"4H confirmation unavailable: {exc}"],
                    }
                )
            )
    return enriched
