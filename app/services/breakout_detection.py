from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.schemas import BreakoutSetupState
from app.services.breakout_config import BreakoutConfig


@dataclass(frozen=True)
class BreakoutDetectionResult:
    state: BreakoutSetupState
    level: float | None
    window: int | None
    buffer: float | None
    breakout_index: pd.Timestamp | None
    breakout_bars_ago: int | None
    close_location: float | None
    base_depth_pct: float | None
    atr_contracted: bool | None
    weak_breakout: bool
    flags: tuple[str, ...] = ()


def prior_pivot(high: pd.Series, window: int) -> pd.Series:
    return high.shift(1).rolling(window, min_periods=window).max()


def breakout_buffer(level: float, atr: float, config: BreakoutConfig) -> float:
    return max(
        level * config.breakout_price_buffer_pct,
        atr * config.breakout_atr_buffer_multiplier,
    )


def _close_location(row: pd.Series) -> float:
    spread = float(row["High"] - row["Low"])
    if spread == 0:
        return 0.5
    return float((row["Close"] - row["Low"]) / spread)


def detect_breakout_state(
    bars: pd.DataFrame,
    atr: pd.Series,
    config: BreakoutConfig,
) -> BreakoutDetectionResult:
    if bars.empty:
        return _empty_result()
    closes = bars["Close"].astype(float)
    highs = bars["High"].astype(float)
    lows = bars["Low"].astype(float)
    pivots = {window: prior_pivot(highs, window) for window in config.pivot_windows}
    events: list[tuple[int, int, float, float]] = []

    start = max(config.pivot_windows)
    for position in range(start, len(bars)):
        if pd.isna(atr.iloc[position]):
            continue
        candidates: list[tuple[int, float, float]] = []
        for window in sorted(config.pivot_windows, reverse=True):
            level_value = pivots[window].iloc[position]
            if pd.isna(level_value):
                continue
            level = float(level_value)
            buffer = breakout_buffer(level, float(atr.iloc[position]), config)
            previous_level = pivots[window].iloc[position - 1]
            previous_atr = atr.iloc[position - 1]
            if pd.isna(previous_level) or pd.isna(previous_atr):
                continue
            previous_buffer = breakout_buffer(
                float(previous_level), float(previous_atr), config
            )
            crossed = (
                closes.iloc[position - 1] <= float(previous_level) + previous_buffer
                and closes.iloc[position] > level + buffer
                and _close_location(bars.iloc[position])
                >= config.close_location_minimum
            )
            if crossed:
                candidates.append((window, level, buffer))
        if candidates:
            window, level, buffer = candidates[0]
            events.append((position, window, level, buffer))

    recent_start = max(0, len(bars) - config.breakout_lookback_bars - 1)
    recent = [event for event in events if event[0] >= recent_start]
    latest_event = recent[-1] if recent else None
    candidates = _latest_candidates(pivots, atr, config)
    selected_window, selected_level, selected_buffer = (
        candidates[0] if candidates else (None, None, None)
    )
    weak_breakout = any(
        highs.iloc[-1] > level or closes.iloc[-1] > level
        for _, level, _ in candidates
    ) and not (latest_event is not None and latest_event[0] == len(bars) - 1)

    if latest_event is not None:
        event_position, event_window, event_level, event_buffer = latest_event
        later_positions = range(event_position + 1, len(bars))
        failures = [
            position
            for position in later_positions
            if not pd.isna(atr.iloc[position])
            and closes.iloc[position]
            < event_level
            - float(atr.iloc[position]) * config.failed_breakout_buffer_atr
        ]
        if failures and len(bars) - 1 - failures[-1] <= config.failed_breakout_cooldown_bars:
            return _result(
                BreakoutSetupState.FAILED_BREAKOUT,
                bars,
                atr,
                config,
                latest_event,
                weak_breakout,
                ("FAILED_BREAKOUT_COOLDOWN",),
            )

        retest_positions = [position for position in (len(bars) - 2, len(bars) - 1) if position > event_position]
        for position in retest_positions:
            current_atr = atr.iloc[position]
            if pd.isna(current_atr):
                continue
            tolerance = float(current_atr) * config.retest_tolerance_atr
            revisited = (
                lows.iloc[position] <= event_level + tolerance
                and highs.iloc[position] >= event_level - tolerance
            )
            held = closes.iloc[position] >= event_level - tolerance
            if revisited and held and closes.iloc[-1] > event_level:
                return _result(
                    BreakoutSetupState.BREAKOUT_RETEST,
                    bars,
                    atr,
                    config,
                    latest_event,
                    weak_breakout,
                    ("BREAKOUT_RETEST_HELD",),
                )

        if event_position < len(bars) - 1:
            confirmation = closes.tail(config.confirmation_window_bars)
            if (
                int((confirmation > event_level).sum())
                >= config.confirmation_required_closes
                and closes.iloc[-1] > event_level
            ):
                return _result(
                    BreakoutSetupState.CONFIRMED_BREAKOUT,
                    bars,
                    atr,
                    config,
                    latest_event,
                    weak_breakout,
                    ("DAILY_BREAKOUT_CONFIRMED",),
                )
        if event_position == len(bars) - 1:
            return _result(
                BreakoutSetupState.FRESH_BREAKOUT,
                bars,
                atr,
                config,
                latest_event,
                weak_breakout,
                ("DAILY_BREAKOUT_FRESH",),
            )

    for candidate_window, candidate_level, candidate_buffer in candidates:
        pre_breakout = _pre_breakout_details(
            bars,
            atr,
            candidate_window,
            candidate_level,
            candidate_buffer,
            config,
        )
        if pre_breakout is not None:
            base_depth, atr_contracted = pre_breakout
            return BreakoutDetectionResult(
                state=BreakoutSetupState.PRE_BREAKOUT,
                level=candidate_level,
                window=candidate_window,
                buffer=candidate_buffer,
                breakout_index=None,
                breakout_bars_ago=None,
                close_location=_close_location(bars.iloc[-1]),
                base_depth_pct=base_depth,
                atr_contracted=atr_contracted,
                weak_breakout=weak_breakout,
                flags=("PRE_BREAKOUT_BASE",),
            )

    return BreakoutDetectionResult(
        state=BreakoutSetupState.NO_VALID_SETUP,
        level=selected_level,
        window=selected_window,
        buffer=selected_buffer,
        breakout_index=None,
        breakout_bars_ago=None,
        close_location=_close_location(bars.iloc[-1]),
        base_depth_pct=None,
        atr_contracted=None,
        weak_breakout=weak_breakout,
    )


def _latest_candidates(
    pivots: dict[int, pd.Series],
    atr: pd.Series,
    config: BreakoutConfig,
) -> list[tuple[int, float, float]]:
    latest_atr = atr.iloc[-1]
    if pd.isna(latest_atr):
        return []
    candidates: list[tuple[int, float, float]] = []
    for window in sorted(config.pivot_windows, reverse=True):
        value = pivots[window].iloc[-1]
        if not pd.isna(value):
            level = float(value)
            candidates.append(
                (window, level, breakout_buffer(level, float(latest_atr), config))
            )
    return candidates


def _pre_breakout_details(
    bars: pd.DataFrame,
    atr: pd.Series,
    window: int | None,
    level: float | None,
    buffer: float | None,
    config: BreakoutConfig,
) -> tuple[float, bool] | None:
    if window is None or level is None or buffer is None or pd.isna(atr.iloc[-1]):
        return None
    close = float(bars["Close"].iloc[-1])
    latest_atr = float(atr.iloc[-1])
    distance = level - close
    allowed = min(
        level * config.pre_breakout_distance_pct,
        latest_atr * config.pre_breakout_atr_distance,
    )
    base = bars.tail(config.base_max_bars)
    base_high = float(base["High"].max())
    base_low = float(base["Low"].min())
    base_depth = (base_high - base_low) / base_high if base_high > 0 else 1.0
    atr_pct = atr / bars["Close"].astype(float)
    prior_median = atr_pct.shift(1).tail(config.base_max_bars).median()
    contracted = not pd.isna(prior_median) and latest_atr / close < float(prior_median)
    if 0 < distance <= allowed and base_depth <= config.base_max_depth_pct and contracted:
        return base_depth, True
    return None


def _result(
    state: BreakoutSetupState,
    bars: pd.DataFrame,
    atr: pd.Series,
    config: BreakoutConfig,
    event: tuple[int, int, float, float],
    weak_breakout: bool,
    flags: tuple[str, ...],
) -> BreakoutDetectionResult:
    position, window, level, buffer = event
    base = bars.iloc[max(0, position - config.base_max_bars) : position]
    base_high = float(base["High"].max()) if not base.empty else level
    base_low = float(base["Low"].min()) if not base.empty else level
    base_depth = (base_high - base_low) / base_high if base_high > 0 else None
    return BreakoutDetectionResult(
        state=state,
        level=level,
        window=window,
        buffer=buffer,
        breakout_index=pd.Timestamp(bars.index[position]),
        breakout_bars_ago=len(bars) - 1 - position,
        close_location=_close_location(bars.iloc[position]),
        base_depth_pct=base_depth,
        atr_contracted=None,
        weak_breakout=weak_breakout,
        flags=flags,
    )


def _empty_result() -> BreakoutDetectionResult:
    return BreakoutDetectionResult(
        state=BreakoutSetupState.NO_VALID_SETUP,
        level=None,
        window=None,
        buffer=None,
        breakout_index=None,
        breakout_bars_ago=None,
        close_location=None,
        base_depth_pct=None,
        atr_contracted=None,
        weak_breakout=False,
    )
