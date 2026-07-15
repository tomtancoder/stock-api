from __future__ import annotations

from dataclasses import dataclass

from app.schemas import BreakoutRating, BreakoutSetupState, DataStatus
from app.services.breakout_config import BreakoutConfig
from app.services.breakout_detection import BreakoutDetectionResult


@dataclass(frozen=True)
class ComponentResult:
    score: int
    max_score: int
    flags: tuple[str, ...] = ()
    explanation: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfluenceScoreResult:
    data_status: DataStatus
    rating: BreakoutRating | None
    total_score: int | None
    breakout: ComponentResult | None
    trend: ComponentResult | None
    volume: ComponentResult | None
    momentum: ComponentResult | None
    relative_strength: ComponentResult | None
    entry_quality: ComponentResult | None
    invalidation_price: float | None
    extension_atr: float | None
    initial_risk_pct: float | None
    flags: tuple[str, ...]
    explanation: tuple[str, ...]
    warnings: tuple[str, ...]


def score_breakout(detection: BreakoutDetectionResult) -> ComponentResult:
    mapping = {
        BreakoutSetupState.PRE_BREAKOUT: (1, "PRE_BREAKOUT_BASE", "Price is approaching a prior resistance level from a controlled base."),
        BreakoutSetupState.FRESH_BREAKOUT: (3, "DAILY_BREAKOUT_FRESH", "Price completed a fresh buffered breakout on a strong close."),
        BreakoutSetupState.CONFIRMED_BREAKOUT: (4, "DAILY_BREAKOUT_CONFIRMED", "Multiple closes confirmed the breakout above prior resistance."),
        BreakoutSetupState.BREAKOUT_RETEST: (4, "BREAKOUT_RETEST_HELD", "The prior resistance level was retested and held."),
        BreakoutSetupState.FAILED_BREAKOUT: (0, "FAILED_BREAKOUT_COOLDOWN", "The breakout failed and remains in its cooldown period."),
    }
    if detection.state in mapping:
        score, flag, explanation = mapping[detection.state]
        return ComponentResult(score, 4, (flag,), (explanation,))
    if detection.weak_breakout:
        return ComponentResult(
            2,
            4,
            ("WEAK_BREAKOUT",),
            ("Price tested resistance without satisfying the full breakout rule.",),
        )
    return ComponentResult(0, 4)


def score_trend(
    close: float | None,
    ema20: float | None,
    ema50: float | None,
    ema200: float | None,
    ema200_prior: float | None,
) -> ComponentResult:
    conditions = (
        close is not None and ema20 is not None and close > ema20,
        ema20 is not None and ema50 is not None and ema20 > ema50,
        ema50 is not None and ema200 is not None and ema50 > ema200,
        ema200 is not None and ema200_prior is not None and ema200 > ema200_prior,
    )
    score = sum(bool(value) for value in conditions)
    flags: list[str] = []
    explanations: list[str] = []
    if all(conditions):
        flags.append("EMA_STACK_BULLISH")
        explanations.append("Price and EMA20, EMA50, and EMA200 are positively aligned.")
    elif score:
        flags.append("TREND_PARTIALLY_ALIGNED")
        explanations.append(f"{score} of 4 bullish trend conditions are present.")
    if ema200 is not None and ema200_prior is not None and ema200 <= ema200_prior:
        flags.append("EMA200_NOT_RISING")
    return ComponentResult(score, 4, tuple(flags), tuple(explanations))


def score_volume(volume_ratio: float | None, cmf20: float | None) -> ComponentResult:
    score = 0
    flags: list[str] = []
    explanations: list[str] = []
    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            score += 2
            flags.append("VOLUME_CONFIRMED")
        elif volume_ratio >= 1.2:
            score += 1
            flags.append("VOLUME_ABOVE_AVERAGE")
        if score:
            explanations.append(f"Volume was {volume_ratio:.2f} times its 50-session average.")
        elif volume_ratio < 0.8:
            flags.append("VOLUME_WEAK")
    if cmf20 is not None and cmf20 > 0.05:
        score += 1
        flags.append("CMF_POSITIVE")
        explanations.append("Chaikin Money Flow indicates positive accumulation.")
    return ComponentResult(score, 3, tuple(flags), tuple(explanations))


def score_momentum(
    rsi14: float | None,
    adx14: float | None,
    adx14_prior: float | None,
    plus_di: float | None,
    minus_di: float | None,
) -> ComponentResult:
    score = 0
    flags: list[str] = []
    explanations: list[str] = []
    if rsi14 is not None:
        if 55 <= rsi14 <= 70:
            score += 2
            flags.append("RSI_BULLISH")
            explanations.append(f"RSI14 was {rsi14:.1f}, within the preferred bullish range.")
        elif 50 <= rsi14 < 55 or 70 < rsi14 <= 75:
            score += 1
            flags.append("RSI_SUPPORTIVE")
            explanations.append(f"RSI14 was {rsi14:.1f}, providing partial momentum support.")
    if (
        adx14 is not None
        and adx14_prior is not None
        and plus_di is not None
        and minus_di is not None
        and adx14 >= 20
        and adx14 > adx14_prior
        and plus_di > minus_di
    ):
        score += 1
        flags.append("ADX_TREND_STRENGTH")
        explanations.append("ADX is rising above 20 with positive directional strength.")
    return ComponentResult(score, 3, tuple(flags), tuple(explanations))


def score_relative_strength(
    stock_return: float | None,
    benchmark_return: float | None,
    benchmark_regime: bool | None,
) -> ComponentResult:
    score = 0
    flags: list[str] = []
    explanations: list[str] = []
    if stock_return is not None and benchmark_return is not None and stock_return > benchmark_return:
        score += 1
        flags.append("RS_OUTPERFORMING")
        explanations.append("The stock outperformed its benchmark over 63 sessions.")
    if benchmark_regime is True:
        score += 1
        flags.append("MARKET_REGIME_POSITIVE")
        explanations.append("The benchmark is above a positive long-term trend structure.")
    if benchmark_return is None or benchmark_regime is None:
        flags.append("MISSING_BENCHMARK_DATA")
    return ComponentResult(score, 2, tuple(flags), tuple(explanations))


def score_entry_quality(
    close: float | None,
    level: float | None,
    atr14: float | None,
    config: BreakoutConfig,
) -> tuple[ComponentResult, float | None, float | None, float | None]:
    if close is None or level is None or atr14 is None or close <= 0 or level <= 0 or atr14 <= 0:
        return ComponentResult(0, 2, ("INVALID_RISK_GEOMETRY",)), None, None, None
    invalidation = level - atr14 * config.invalidation_atr_multiplier
    if invalidation >= close:
        return ComponentResult(0, 2, ("INVALID_RISK_GEOMETRY",)), invalidation, None, None
    extension = (close - level) / atr14
    risk = max(close - invalidation, 0.0) / close
    if extension <= config.full_entry_extension_atr and risk <= config.full_entry_risk_pct:
        return (
            ComponentResult(
                2,
                2,
                ("ENTRY_NOT_EXTENDED",),
                (f"Price is {extension:.2f} ATR above the breakout level with {risk:.1%} initial risk.",),
            ),
            invalidation,
            extension,
            risk,
        )
    if extension <= config.partial_entry_extension_atr and risk <= config.partial_entry_risk_pct:
        return (
            ComponentResult(
                1,
                2,
                ("ENTRY_ACCEPTABLE",),
                (f"Entry geometry is acceptable at {extension:.2f} ATR extension and {risk:.1%} initial risk.",),
            ),
            invalidation,
            extension,
            risk,
        )
    return (
        ComponentResult(
            0,
            2,
            ("ENTRY_EXTENDED",),
            ("Price or initial risk is beyond the preferred entry range.",),
        ),
        invalidation,
        extension,
        risk,
    )


_RATING_RANK = {
    BreakoutRating.AVOID: 0,
    BreakoutRating.WATCHLIST: 1,
    BreakoutRating.STARTER_SETUP: 2,
    BreakoutRating.STRONG_SETUP: 3,
}


def cap_rating(current: BreakoutRating, maximum: BreakoutRating) -> BreakoutRating:
    return current if _RATING_RANK[current] <= _RATING_RANK[maximum] else maximum


def determine_rating(
    *,
    total_score: int,
    state: BreakoutSetupState,
    breakout_score: int,
    trend_score: int,
    volume_score: int,
    entry_quality_score: int,
    close_above_ema200: bool,
    ema200_rising: bool,
    volume_ratio: float | None,
    extension_atr: float | None,
    rsi14: float | None,
    data_status: DataStatus,
    config: BreakoutConfig,
) -> tuple[BreakoutRating, tuple[str, ...], tuple[str, ...]]:
    if total_score >= 15:
        rating = BreakoutRating.STRONG_SETUP
    elif total_score >= 12:
        rating = BreakoutRating.STARTER_SETUP
    elif total_score >= 9:
        rating = BreakoutRating.WATCHLIST
    else:
        rating = BreakoutRating.AVOID
    flags: list[str] = []
    explanations: list[str] = []
    valid_states = {
        BreakoutSetupState.FRESH_BREAKOUT,
        BreakoutSetupState.CONFIRMED_BREAKOUT,
        BreakoutSetupState.BREAKOUT_RETEST,
    }
    if state == BreakoutSetupState.FAILED_BREAKOUT:
        return (
            BreakoutRating.AVOID,
            ("FAILED_BREAKOUT_COOLDOWN",),
            ("The failed-breakout cooldown forces an Avoid rating.",),
        )
    strong_gates = (
        breakout_score >= 3
        and trend_score >= 3
        and volume_score >= 1
        and entry_quality_score >= 1
        and state in valid_states
    )
    starter_gates = (
        breakout_score >= 3
        and trend_score >= 2
        and entry_quality_score >= 1
        and state in valid_states
    )
    if rating == BreakoutRating.STRONG_SETUP and not strong_gates:
        rating = BreakoutRating.STARTER_SETUP if starter_gates else BreakoutRating.WATCHLIST
        flags.append("STRONG_GATE_NOT_MET")
        explanations.append("The score reached the strong band, but its hard gates were not all met.")
    if rating == BreakoutRating.STARTER_SETUP and not starter_gates:
        rating = BreakoutRating.WATCHLIST
        flags.append("STARTER_GATE_NOT_MET")
        explanations.append("The score reached the starter band, but its hard gates were not all met.")

    def apply_watchlist_cap(condition: bool, flag: str, explanation: str) -> None:
        nonlocal rating
        if condition:
            previous = rating
            rating = cap_rating(rating, BreakoutRating.WATCHLIST)
            flags.append(flag)
            if rating != previous:
                explanations.append(explanation)

    apply_watchlist_cap(
        state in {
            BreakoutSetupState.PRE_BREAKOUT,
            BreakoutSetupState.TREND_TRANSITION,
            BreakoutSetupState.NO_VALID_SETUP,
        },
        "NO_COMPLETED_BREAKOUT",
        "A completed breakout is required for a Starter or Strong rating.",
    )
    apply_watchlist_cap(
        not close_above_ema200,
        "PRICE_BELOW_EMA200",
        "Price below EMA200 caps the rating at Watchlist.",
    )
    apply_watchlist_cap(
        not ema200_rising,
        "EMA200_NOT_RISING",
        "A non-rising EMA200 caps the rating at Watchlist.",
    )
    apply_watchlist_cap(
        volume_ratio is not None and volume_ratio < config.volume_failure_ratio,
        "VOLUME_WEAK",
        "Breakout volume below 0.80 times average caps the rating at Watchlist.",
    )
    apply_watchlist_cap(
        extension_atr is not None and extension_atr > config.maximum_extension_atr,
        "ENTRY_EXTENDED",
        "Extension above 1.50 ATR caps the rating at Watchlist.",
    )
    apply_watchlist_cap(
        rsi14 is not None and rsi14 > config.rsi_overextended,
        "RSI_OVEREXTENDED",
        "RSI above 78 caps the rating at Watchlist.",
    )
    apply_watchlist_cap(
        data_status == DataStatus.PARTIAL,
        "PARTIAL_DATA_CAP",
        "Partial market data caps the rating at Watchlist.",
    )
    return rating, tuple(dict.fromkeys(flags)), tuple(explanations)


def calculate_breakout_confluence(
    *,
    detection: BreakoutDetectionResult,
    close: float | None,
    ema20: float | None,
    ema50: float | None,
    ema200: float | None,
    ema200_prior: float | None,
    rsi14: float | None,
    adx14: float | None,
    adx14_prior: float | None,
    plus_di: float | None,
    minus_di: float | None,
    cmf20: float | None,
    volume_ratio: float | None,
    stock_return: float | None,
    benchmark_return: float | None,
    benchmark_regime: bool | None,
    atr14: float | None,
    data_status: DataStatus,
    config: BreakoutConfig,
) -> ConfluenceScoreResult:
    warnings: list[str] = []
    if ema200 is None:
        return ConfluenceScoreResult(
            data_status=DataStatus.INSUFFICIENT_HISTORY,
            rating=None,
            total_score=None,
            breakout=None,
            trend=None,
            volume=None,
            momentum=None,
            relative_strength=None,
            entry_quality=None,
            invalidation_price=None,
            extension_atr=None,
            initial_risk_pct=None,
            flags=("INSUFFICIENT_EMA200_HISTORY",),
            explanation=(),
            warnings=("EMA200 requires at least 200 valid closes.",),
        )
    resolved_status = data_status
    if resolved_status == DataStatus.READY and (
        volume_ratio is None
        or cmf20 is None
        or benchmark_return is None
        or benchmark_regime is None
    ):
        resolved_status = DataStatus.PARTIAL
    if benchmark_return is None or benchmark_regime is None:
        warnings.append("Benchmark data are incomplete; relative-strength scoring is partial.")
    if volume_ratio is None or cmf20 is None:
        warnings.append("Volume data are incomplete; volume scoring is partial.")
    breakout = score_breakout(detection)
    trend = score_trend(close, ema20, ema50, ema200, ema200_prior)
    volume = score_volume(volume_ratio, cmf20)
    momentum = score_momentum(rsi14, adx14, adx14_prior, plus_di, minus_di)
    relative = score_relative_strength(stock_return, benchmark_return, benchmark_regime)
    entry, invalidation, extension, risk = score_entry_quality(
        close, detection.level, atr14, config
    )
    components = (breakout, trend, volume, momentum, relative, entry)
    total = sum(component.score for component in components)
    rating, cap_flags, cap_explanations = determine_rating(
        total_score=total,
        state=detection.state,
        breakout_score=breakout.score,
        trend_score=trend.score,
        volume_score=volume.score,
        entry_quality_score=entry.score,
        close_above_ema200=close is not None and close > ema200,
        ema200_rising=ema200_prior is not None and ema200 > ema200_prior,
        volume_ratio=volume_ratio,
        extension_atr=extension,
        rsi14=rsi14,
        data_status=resolved_status,
        config=config,
    )
    flags = tuple(
        dict.fromkeys(
            flag
            for component in components
            for flag in component.flags
        )
    ) + tuple(flag for flag in cap_flags if flag not in {flag for component in components for flag in component.flags})
    explanation = tuple(
        item for component in components for item in component.explanation
    ) + cap_explanations
    return ConfluenceScoreResult(
        data_status=resolved_status,
        rating=rating,
        total_score=total,
        breakout=breakout,
        trend=trend,
        volume=volume,
        momentum=momentum,
        relative_strength=relative,
        entry_quality=entry,
        invalidation_price=invalidation,
        extension_atr=extension,
        initial_risk_pct=risk,
        flags=flags,
        explanation=explanation,
        warnings=tuple(warnings),
    )
