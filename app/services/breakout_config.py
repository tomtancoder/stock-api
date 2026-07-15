from dataclasses import dataclass


US_BENCHMARK = "SPY"
SG_BENCHMARK = "^STI"


@dataclass(frozen=True)
class BreakoutConfig:
    pivot_windows: tuple[int, int] = (20, 55)
    breakout_price_buffer_pct: float = 0.0025
    breakout_atr_buffer_multiplier: float = 0.15
    close_location_minimum: float = 0.65
    pre_breakout_distance_pct: float = 0.02
    pre_breakout_atr_distance: float = 0.50
    breakout_lookback_bars: int = 20
    confirmation_required_closes: int = 2
    confirmation_window_bars: int = 3
    retest_tolerance_atr: float = 0.50
    failed_breakout_buffer_atr: float = 0.50
    failed_breakout_cooldown_bars: int = 7
    base_min_bars: int = 20
    base_max_bars: int = 60
    base_max_depth_pct: float = 0.20
    volume_average_length: int = 50
    volume_ratio_minimum: float = 1.20
    volume_ratio_strong: float = 1.50
    volume_failure_ratio: float = 0.80
    cmf_length: int = 20
    cmf_positive_threshold: float = 0.05
    rsi_length: int = 14
    rsi_full_min: float = 55.0
    rsi_full_max: float = 70.0
    rsi_partial_min: float = 50.0
    rsi_partial_max: float = 75.0
    rsi_overextended: float = 78.0
    atr_length: int = 14
    adx_length: int = 14
    adx_minimum: float = 20.0
    relative_strength_lookback: int = 63
    ema_short: int = 20
    ema_medium: int = 50
    ema_long: int = 200
    ema_long_slope_lookback: int = 20
    invalidation_atr_multiplier: float = 0.75
    full_entry_extension_atr: float = 0.75
    partial_entry_extension_atr: float = 1.25
    maximum_extension_atr: float = 1.50
    full_entry_risk_pct: float = 0.06
    partial_entry_risk_pct: float = 0.08
    minimum_daily_bars: int = 220
    maximum_data_age_days: int = 5
    minimum_average_volume_50: float = 100_000.0
    batch_size: int = 75
    screener_cache_ttl_seconds: int = 3600
    four_hour_candidate_min_score: int = 9
    four_hour_candidate_limit: int = 50
    four_hour_minimum_bars: int = 60
    four_hour_retest_tolerance_atr: float = 0.25
    market_close_grace_minutes: int = 15


def is_singapore_symbol(symbol: str) -> bool:
    normalized = symbol.strip().upper()
    return normalized.endswith(".SI") or normalized == SG_BENCHMARK


def default_benchmark_for(symbol: str) -> str:
    return SG_BENCHMARK if is_singapore_symbol(symbol) else US_BENCHMARK
