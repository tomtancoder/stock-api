import pandas as pd
import yfinance as yf
from cachetools import TTLCache, cached

from app.core.config import get_settings
from app.schemas import EmaValues, TechnicalsResponse
from app.services.yfinance_client import YFinanceError

EMA_WINDOWS = (21, 50, 100, 200)
_technicals_cache = TTLCache(maxsize=512, ttl=get_settings().cache_ttl_seconds)


def get_stock_technicals(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
) -> TechnicalsResponse:
    normalized_symbol = _normalize_symbol(symbol)
    normalized_period = period.strip() or "1y"
    normalized_interval = interval.strip() or "1d"
    return _fetch_stock_technicals(normalized_symbol, normalized_period, normalized_interval)


@cached(cache=_technicals_cache)
def _fetch_stock_technicals(
    symbol: str,
    period: str,
    interval: str,
) -> TechnicalsResponse:
    history = _fetch_history(symbol, period, interval)
    close_prices = _close_prices(history)
    warnings: list[str] = []

    ema_values = {}
    for window in EMA_WINDOWS:
        if len(close_prices) < window:
            warnings.append(
                f"Only {len(close_prices)} close prices available; EMA {window} uses limited history."
            )
        ema_values[f"ema_{window}"] = _round_indicator(
            close_prices.ewm(span=window, adjust=False).mean().iloc[-1]
        )

    latest_timestamp = close_prices.index[-1]
    return TechnicalsResponse(
        symbol=symbol,
        period=period,
        interval=interval,
        as_of=_timestamp_to_string(latest_timestamp),
        latest_close=_round_indicator(close_prices.iloc[-1]),
        ema=EmaValues(**ema_values),
        warnings=warnings,
    )


def _fetch_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    try:
        history = yf.Ticker(symbol).history(period=period, interval=interval)
    except Exception as exc:  # pragma: no cover - depends on yFinance internals
        raise YFinanceError(f"Could not fetch yFinance price history for {symbol}.") from exc

    if history is None or history.empty:
        raise ValueError(f"No yFinance price history found for symbol {symbol}.")
    return history


def _close_prices(history: pd.DataFrame) -> pd.Series:
    if "Close" not in history:
        raise ValueError("Price history does not include close prices.")

    close_prices = history["Close"].dropna()
    if close_prices.empty:
        raise ValueError("Price history does not include usable close prices.")
    return close_prices


def _normalize_symbol(symbol: str) -> str:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("Stock symbol is required.")
    return normalized_symbol


def _round_indicator(value: float) -> float:
    return round(float(value), 4)


def _timestamp_to_string(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
