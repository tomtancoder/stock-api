from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from threading import RLock
from typing import Sequence
from zoneinfo import ZoneInfo

import pandas as pd
from cachetools import TTLCache

from app.core.config import get_settings
from app.services.breakout_config import is_singapore_symbol


class MarketDataError(RuntimeError):
    pass


_cache: TTLCache = TTLCache(maxsize=1024, ttl=get_settings().cache_ttl_seconds)
_cache_lock = RLock()


def clear_market_data_cache() -> None:
    with _cache_lock:
        _cache.clear()


def normalize_symbol(symbol: str) -> str:
    normalized = (symbol or "").strip().upper()
    if not normalized:
        raise ValueError("symbol must not be blank")
    return normalized


def completed_daily_bars(
    frame: pd.DataFrame,
    *,
    symbol: str,
    now_utc: datetime | None = None,
    grace_minutes: int = 15,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame() if frame is None else frame.copy()
    result = frame.copy()
    singapore = is_singapore_symbol(symbol)
    zone = ZoneInfo("Asia/Singapore" if singapore else "America/New_York")
    close_time = time(17, 0) if singapore else time(16, 0)
    current_utc = now_utc or datetime.now(timezone.utc)
    if current_utc.tzinfo is None:
        current_utc = current_utc.replace(tzinfo=timezone.utc)
    local_now = current_utc.astimezone(zone)
    completed_at = datetime.combine(local_now.date(), close_time, zone) + timedelta(
        minutes=grace_minutes
    )
    latest = pd.Timestamp(result.index[-1])
    # Daily indexes are session labels. Converting a midnight UTC label to the
    # exchange timezone can incorrectly move it to the previous calendar day.
    latest_date = latest.date()
    if latest_date == local_now.date() and local_now < completed_at:
        return result.iloc[:-1].copy()
    return result


def validate_ohlcv(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise MarketDataError(f"No market data returned for {symbol}.")
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise MarketDataError(
            f"Market data for {symbol} is missing columns: {', '.join(missing)}."
        )
    result = frame.loc[:, required].copy()
    result.index = pd.to_datetime(result.index)
    result = result.sort_index()
    result = result.loc[~result.index.duplicated(keep="last")]
    for column in required:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["Open", "High", "Low", "Close"])
    if result.empty:
        raise MarketDataError(f"No valid OHLC rows returned for {symbol}.")
    return result


def _download(**kwargs) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(**kwargs)


def _single_symbol_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame
    for level in (0, 1):
        values = {str(value).upper() for value in frame.columns.get_level_values(level)}
        if symbol in values:
            selected = frame.xs(symbol, axis=1, level=level, drop_level=True)
            return selected
    return pd.DataFrame()


def fetch_daily_history(symbol: str, period: str = "2y") -> pd.DataFrame:
    normalized = normalize_symbol(symbol)
    key = ("daily", normalized, period)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached.copy()
    try:
        raw = _download(
            tickers=normalized,
            period=period,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        raw = _single_symbol_frame(raw, normalized)
        result = validate_ohlcv(
            completed_daily_bars(raw, symbol=normalized), normalized
        )
    except MarketDataError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider transports vary.
        raise MarketDataError(f"Market data request failed for {normalized}: {exc}") from exc
    with _cache_lock:
        _cache[key] = result.copy()
    return result


def fetch_hourly_history(symbol: str, period: str = "60d") -> pd.DataFrame:
    normalized = normalize_symbol(symbol)
    key = ("hourly", normalized, period)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached.copy()
    try:
        raw = _download(
            tickers=normalized,
            period=period,
            interval="1h",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        result = validate_ohlcv(_single_symbol_frame(raw, normalized), normalized)
    except MarketDataError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider transports vary.
        raise MarketDataError(f"Hourly market data request failed for {normalized}: {exc}") from exc
    with _cache_lock:
        _cache[key] = result.copy()
    return result


def download_daily_histories(
    symbols: Sequence[str],
    period: str = "2y",
    batch_size: int = 75,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    normalized_symbols = list(dict.fromkeys(normalize_symbol(value) for value in symbols))
    histories: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    for start in range(0, len(normalized_symbols), batch_size):
        chunk = normalized_symbols[start : start + batch_size]
        try:
            raw = _download(
                tickers=" ".join(chunk),
                period=period,
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
        except Exception as exc:  # noqa: BLE001 - isolate provider failures by chunk.
            for symbol in chunk:
                errors[symbol] = f"Market data request failed for {symbol}: {exc}"
            continue
        for symbol in chunk:
            try:
                selected = _single_symbol_frame(raw, symbol)
                if selected.empty and len(chunk) == 1 and not isinstance(raw.columns, pd.MultiIndex):
                    selected = raw
                selected = completed_daily_bars(selected, symbol=symbol)
                histories[symbol] = validate_ohlcv(selected, symbol)
            except MarketDataError as exc:
                errors[symbol] = str(exc)
            except Exception as exc:  # noqa: BLE001 - malformed ticker must not abort scan.
                errors[symbol] = f"Invalid market data for {symbol}: {exc}"
    return histories, errors
