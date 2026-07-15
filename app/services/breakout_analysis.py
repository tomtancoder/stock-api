from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd

from app.schemas import (
    BreakoutAnalysisResponse,
    BreakoutComponentScore,
    BreakoutIndicatorSnapshot,
    BreakoutLevelInfo,
    BreakoutRiskSnapshot,
    BreakoutSetupState,
    DataStatus,
    FourHourStatus,
)
from app.services.breakout_config import BreakoutConfig, default_benchmark_for
from app.services.breakout_confluence import ComponentResult, calculate_breakout_confluence
from app.services.breakout_detection import detect_breakout_state
from app.services.indicators import (
    adx_frame,
    atr_series,
    cmf_series,
    ema_series,
    return_over_period,
    rsi_series,
    safe_latest,
    sma_series,
)
from app.services.market_data import (
    MarketDataError,
    fetch_daily_history,
    fetch_hourly_history,
)
from app.services.market_symbols import normalize_exchange, to_yahoo_symbol
from app.services.timeframes import four_hour_confirmation, resample_hourly_to_four_hour


def get_breakout_analysis(
    exchange: str,
    symbol: str,
    benchmark_symbol: str | None = None,
    include_four_hour: bool = False,
    config: BreakoutConfig | None = None,
) -> BreakoutAnalysisResponse:
    config = config or BreakoutConfig()
    venue = normalize_exchange(exchange)
    public_symbol = symbol.strip().upper()
    if venue == "SGX" and public_symbol.endswith(".SI"):
        public_symbol = public_symbol[:-3]
    yahoo_symbol = to_yahoo_symbol(venue, public_symbol)
    benchmark = (benchmark_symbol or default_benchmark_for(yahoo_symbol)).strip().upper()
    stock_bars = fetch_daily_history(yahoo_symbol)
    try:
        benchmark_bars = fetch_daily_history(benchmark)
    except MarketDataError:
        benchmark_bars = None
    response = analyze_breakout_frames(
        exchange=venue,
        symbol=public_symbol,
        stock_bars=stock_bars,
        benchmark_symbol=benchmark,
        benchmark_bars=benchmark_bars,
        config=config,
    )
    if not include_four_hour or response.level is None or response.level.price is None:
        return response
    try:
        hourly = fetch_hourly_history(yahoo_symbol)
        four_hour = resample_hourly_to_four_hour(hourly)
        status = four_hour_confirmation(four_hour, response.level.price, config)
        return response.model_copy(update={"four_hour_status": status})
    except MarketDataError as exc:
        return response.model_copy(
            update={
                "four_hour_status": FourHourStatus.UNAVAILABLE,
                "warnings": [*response.warnings, f"4H confirmation unavailable: {exc}"],
            }
        )


def analyze_breakout_frames(
    *,
    exchange: str,
    symbol: str,
    stock_bars: pd.DataFrame,
    benchmark_symbol: str,
    benchmark_bars: pd.DataFrame | None,
    config: BreakoutConfig,
) -> BreakoutAnalysisResponse:
    venue = normalize_exchange(exchange)
    normalized_symbol = symbol.strip().upper()
    as_of = _as_of(stock_bars)
    if len(stock_bars) < config.minimum_daily_bars:
        return _unavailable_response(
            exchange=venue,
            symbol=normalized_symbol,
            benchmark_symbol=benchmark_symbol,
            as_of=as_of,
            status=DataStatus.INSUFFICIENT_HISTORY,
            flag="INSUFFICIENT_EMA200_HISTORY",
            warning=(
                f"At least {config.minimum_daily_bars} daily bars are required; "
                f"{len(stock_bars)} were available."
            ),
        )
    if _is_stale(stock_bars, config.maximum_data_age_days):
        return _unavailable_response(
            exchange=venue,
            symbol=normalized_symbol,
            benchmark_symbol=benchmark_symbol,
            as_of=as_of,
            status=DataStatus.STALE,
            flag="STALE_DAILY_HISTORY",
            warning=f"Latest daily bar is older than {config.maximum_data_age_days} days.",
        )

    close = stock_bars["Close"].astype(float)
    volume_values = stock_bars["Volume"].astype(float)
    ema20_series = ema_series(close, config.ema_short)
    ema50_series = ema_series(close, config.ema_medium)
    ema200_series = ema_series(close, config.ema_long)
    rsi = rsi_series(close, config.rsi_length)
    atr = atr_series(stock_bars, config.atr_length)
    adx = adx_frame(stock_bars, config.adx_length)
    cmf = cmf_series(stock_bars, config.cmf_length)
    volume_average = sma_series(volume_values, config.volume_average_length)
    latest_volume = _latest(volume_values)
    latest_average_volume = _latest(volume_average)
    volume_ratio = (
        latest_volume / latest_average_volume
        if latest_volume is not None and latest_average_volume not in (None, 0)
        else None
    )
    ema20 = safe_latest(ema20_series, config.ema_short)
    ema50 = safe_latest(ema50_series, config.ema_medium)
    ema200 = safe_latest(ema200_series, config.ema_long)
    ema200_prior = _at_offset(ema200_series, config.ema_long_slope_lookback)
    rsi14 = safe_latest(rsi, config.rsi_length)
    atr14 = safe_latest(atr, config.atr_length)
    adx14 = safe_latest(adx["adx"], config.adx_length)
    adx14_prior = _at_offset(adx["adx"], 1)
    plus_di = _latest(adx["plus_di"])
    minus_di = _latest(adx["minus_di"])
    cmf20 = safe_latest(cmf, config.cmf_length)
    stock_return = return_over_period(close, config.relative_strength_lookback)
    benchmark_return, benchmark_regime = _benchmark_context(benchmark_bars, config)
    detection = detect_breakout_state(stock_bars, atr, config)
    latest_close = _latest(close)
    if (
        detection.state == BreakoutSetupState.NO_VALID_SETUP
        and latest_close is not None
        and ema20 is not None
        and ema50 is not None
        and latest_close > ema20 > ema50
        and (ema200 is None or ema50 <= ema200 or ema200_prior is None or ema200 <= ema200_prior)
    ):
        detection = replace(
            detection,
            state=BreakoutSetupState.TREND_TRANSITION,
            flags=(*detection.flags, "TREND_TRANSITION"),
        )
    status = DataStatus.READY
    if (
        latest_volume is None
        or latest_average_volume is None
        or cmf20 is None
        or benchmark_bars is None
        or benchmark_return is None
        or benchmark_regime is None
    ):
        status = DataStatus.PARTIAL
    confluence = calculate_breakout_confluence(
        detection=detection,
        close=latest_close,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        ema200_prior=ema200_prior,
        rsi14=rsi14,
        adx14=adx14,
        adx14_prior=adx14_prior,
        plus_di=plus_di,
        minus_di=minus_di,
        cmf20=cmf20,
        volume_ratio=volume_ratio,
        stock_return=stock_return,
        benchmark_return=benchmark_return,
        benchmark_regime=benchmark_regime,
        atr14=atr14,
        data_status=status,
        config=config,
    )
    return BreakoutAnalysisResponse(
        symbol=normalized_symbol,
        exchange=venue,
        benchmark_symbol=benchmark_symbol,
        as_of=as_of,
        data_status=confluence.data_status,
        rating=confluence.rating,
        setup_state=detection.state,
        total_score=confluence.total_score,
        breakout=_public_component(confluence.breakout),
        trend=_public_component(confluence.trend),
        volume=_public_component(confluence.volume),
        momentum=_public_component(confluence.momentum),
        relative_strength=_public_component(confluence.relative_strength),
        entry_quality=_public_component(confluence.entry_quality),
        level=BreakoutLevelInfo(
            window=detection.window,
            price=_round(detection.level),
            buffer=_round(detection.buffer),
            breakout_bars_ago=detection.breakout_bars_ago,
            breakout_percent=_ratio(latest_close, detection.level),
            close_location=_round(detection.close_location),
            base_depth_pct=_round(detection.base_depth_pct),
        ),
        indicators=BreakoutIndicatorSnapshot(
            close=_round(latest_close),
            ema20=_round(ema20),
            ema50=_round(ema50),
            ema200=_round(ema200),
            ema200_prior=_round(ema200_prior),
            rsi14=_round(rsi14),
            atr14=_round(atr14),
            adx14=_round(adx14),
            plus_di14=_round(plus_di),
            minus_di14=_round(minus_di),
            cmf20=_round(cmf20),
            volume_ratio=_round(volume_ratio),
            stock_return_63=_round(stock_return),
            benchmark_return_63=_round(benchmark_return),
        ),
        risk=BreakoutRiskSnapshot(
            invalidation_price=_round(confluence.invalidation_price),
            extension_atr=_round(confluence.extension_atr),
            initial_risk_pct=_round(confluence.initial_risk_pct),
        ),
        flags=list(confluence.flags),
        explanation=list(confluence.explanation),
        warnings=list(confluence.warnings),
    )


def _benchmark_context(
    bars: pd.DataFrame | None,
    config: BreakoutConfig,
) -> tuple[float | None, bool | None]:
    if bars is None or bars.empty or "Close" not in bars:
        return None, None
    close = bars["Close"].astype(float)
    benchmark_return = return_over_period(close, config.relative_strength_lookback)
    ema50 = safe_latest(ema_series(close, config.ema_medium), config.ema_medium)
    ema200 = safe_latest(ema_series(close, config.ema_long), config.ema_long)
    latest_close = _latest(close)
    regime = None
    if latest_close is not None and ema50 is not None and ema200 is not None:
        regime = latest_close > ema200 and ema50 > ema200
    return benchmark_return, regime


def _unavailable_response(
    *,
    exchange: str,
    symbol: str,
    benchmark_symbol: str,
    as_of: str | None,
    status: DataStatus,
    flag: str,
    warning: str,
) -> BreakoutAnalysisResponse:
    return BreakoutAnalysisResponse(
        symbol=symbol,
        exchange=exchange,
        benchmark_symbol=benchmark_symbol,
        as_of=as_of,
        data_status=status,
        rating=None,
        setup_state=BreakoutSetupState.NO_VALID_SETUP,
        total_score=None,
        flags=[flag],
        warnings=[warning],
    )


def _public_component(value: ComponentResult | None) -> BreakoutComponentScore | None:
    if value is None:
        return None
    return BreakoutComponentScore(
        score=value.score,
        max_score=value.max_score,
        flags=list(value.flags),
        explanation=list(value.explanation),
    )


def _latest(series: pd.Series) -> float | None:
    if series.empty or pd.isna(series.iloc[-1]):
        return None
    return float(series.iloc[-1])


def _at_offset(series: pd.Series, offset: int) -> float | None:
    if len(series) <= offset:
        return None
    value = series.iloc[-offset - 1]
    return None if pd.isna(value) else float(value)


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _ratio(close: float | None, level: float | None) -> float | None:
    if close is None or level in (None, 0):
        return None
    return round((close - level) / level, 4)


def _as_of(bars: pd.DataFrame) -> str | None:
    if bars is None or bars.empty:
        return None
    return pd.Timestamp(bars.index[-1]).isoformat()


def _is_stale(bars: pd.DataFrame, maximum_days: int) -> bool:
    latest = pd.Timestamp(bars.index[-1])
    latest_date = latest.date()
    current_date = datetime.now(timezone.utc).date()
    return (current_date - latest_date).days > maximum_days
