from __future__ import annotations

from datetime import timezone
from typing import Any

import pandas as pd

from app.services.yfinance_fundamentals import (
    build_valuation_metrics,
    get_valuation_metadata,
)


def get_analysis(exchange: str, symbol: str, timeframe: str) -> dict[str, Any]:
    public_timeframe = _normalize_timeframe(timeframe)
    yahoo_symbol = _yahoo_symbol(exchange, symbol)
    period, interval = _history_request(public_timeframe)

    try:
        history = _download_history(yahoo_symbol, period, interval)
    except Exception as exc:  # noqa: BLE001 - yfinance can raise transport/parser errors.
        return {
            "error": {
                "code": "UPSTREAM_ERROR",
                "message": f"yfinance analysis failed for {yahoo_symbol}: {exc}",
                "retryable": True,
                "retry_after_s": 60,
            }
        }

    if public_timeframe == "4h":
        history = _resample_four_hour(history)
    else:
        history = _clean_history(history)

    if history.empty:
        return {
            "error": {
                "code": "SYMBOL_NOT_FOUND",
                "message": f"No yfinance history found for {yahoo_symbol}.",
                "retryable": False,
            }
        }

    fast_info = _safe_download_fast_info(yahoo_symbol)
    valuation_metadata = get_valuation_metadata(yahoo_symbol)

    return _build_analysis(
        exchange,
        symbol,
        yahoo_symbol,
        public_timeframe,
        history,
        fast_info,
        valuation_metadata,
    )


def _download_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf

    return yf.Ticker(symbol).history(
        period=period,
        interval=interval,
        auto_adjust=False,
        prepost=False,
    )


def _safe_download_fast_info(symbol: str) -> dict[str, Any]:
    try:
        return _download_fast_info(symbol)
    except Exception:  # noqa: BLE001 - metadata should not block technical analysis.
        return {}


def _download_fast_info(symbol: str) -> dict[str, Any]:
    import yfinance as yf

    fast_info = yf.Ticker(symbol).fast_info
    if fast_info is None:
        return {}
    if isinstance(fast_info, dict):
        return dict(fast_info)

    values: dict[str, Any] = {}
    keys = fast_info.keys() if hasattr(fast_info, "keys") else []
    for key in keys:
        try:
            values[str(key)] = fast_info.get(key)
        except Exception:  # noqa: BLE001 - keep usable keys if one lazy field fails.
            continue
    return values


def _build_analysis(
    exchange: str,
    original_symbol: str,
    yahoo_symbol: str,
    timeframe: str,
    history: pd.DataFrame,
    fast_info: dict[str, Any] | None = None,
    valuation_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    close = history["Close"].astype(float)
    high = history["High"].astype(float)
    low = history["Low"].astype(float)
    open_ = history["Open"].astype(float)
    volume = history["Volume"].fillna(0).astype(float)

    rsi = _rsi(close)
    macd_line, macd_signal, macd_histogram = _macd(close)
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    bb_middle = sma20
    bb_std = close.rolling(20).std()
    bb_upper = bb_middle + (2 * bb_std)
    bb_lower = bb_middle - (2 * bb_std)
    atr = _atr(high, low, close)
    stoch_k, stoch_d = _stochastic(high, low, close)

    current_price = _last_float(close)
    previous_close = _previous_float(close)
    latest_open = _last_float(open_)
    latest_high = _last_float(high)
    latest_low = _last_float(low)
    latest_volume = _last_float(volume)
    fast_info = fast_info or {}
    market_cap = _market_cap(fast_info, current_price)
    rating = _technical_rating(
        current_price=current_price,
        sma20=_last_float(sma20),
        sma50=_last_float(sma50),
        macd=_last_float(macd_line),
        macd_signal=_last_float(macd_signal),
        rsi=_last_float(rsi),
    )
    signal = _rating_signal(rating)
    trend_state = _trend_state(rating)

    return {
        "symbol": _public_symbol(exchange, original_symbol),
        "exchange": exchange.strip().upper(),
        "timeframe": timeframe,
        "source": "yfinance",
        "timestamp": _latest_timestamp(history),
        "price_data": {
            "current_price": _round(current_price),
            "open": _round(latest_open),
            "high": _round(latest_high),
            "low": _round(latest_low),
            "close": _round(current_price),
            "previous_close": _round(previous_close),
            "change_percent": _change_percent(current_price, previous_close),
            "volume": int(latest_volume) if latest_volume is not None else None,
            "market_cap": _round(market_cap, 2),
            "fifty_two_week_high": _round(
                _metadata_float(
                    fast_info,
                    "yearHigh",
                    "year_high",
                    "fiftyTwoWeekHigh",
                    "fifty_two_week_high",
                )
            ),
            "fifty_two_week_low": _round(
                _metadata_float(
                    fast_info,
                    "yearLow",
                    "year_low",
                    "fiftyTwoWeekLow",
                    "fifty_two_week_low",
                )
            ),
            "yahoo_symbol": yahoo_symbol,
        },
        "valuation_metrics": build_valuation_metrics(current_price, valuation_metadata),
        "rsi": {
            "value": _round(_last_float(rsi)),
            "signal": _rsi_signal(_last_float(rsi)),
        },
        "macd": {
            "macd": _round(_last_float(macd_line)),
            "signal": _round(_last_float(macd_signal)),
            "histogram": _round(_last_float(macd_histogram)),
            "trend": _macd_trend(_last_float(macd_line), _last_float(macd_signal)),
        },
        "sma": {
            "sma20": _round(_last_float(sma20)),
            "sma50": _round(_last_float(sma50)),
            "sma200": _round(_last_float(sma200)),
        },
        "ema": {
            "ema12": _round(_last_float(ema12)),
            "ema26": _round(_last_float(ema26)),
            "ema50": _round(_last_float(ema50)),
        },
        "bollinger_bands": {
            "upper": _round(_last_float(bb_upper)),
            "middle": _round(_last_float(bb_middle)),
            "lower": _round(_last_float(bb_lower)),
            "bandwidth": _bandwidth(_last_float(bb_upper), _last_float(bb_lower), _last_float(bb_middle)),
            "position": _bollinger_position(
                current_price,
                _last_float(bb_upper),
                _last_float(bb_lower),
            ),
        },
        "atr": {"value": _round(_last_float(atr))},
        "volume_analysis": {
            "current_volume": int(latest_volume) if latest_volume is not None else None,
            "average_volume_20": _round(_last_float(volume.rolling(20).mean())),
            "relative_volume": _relative_volume(latest_volume, _last_float(volume.rolling(20).mean())),
        },
        "support_resistance": {
            "support": _round(_last_float(low.tail(20).min())),
            "resistance": _round(_last_float(high.tail(20).max())),
        },
        "stochastic": {
            "k": _round(_last_float(stoch_k)),
            "d": _round(_last_float(stoch_d)),
            "signal": _stochastic_signal(_last_float(stoch_k), _last_float(stoch_d)),
        },
        "market_sentiment": {
            "overall_rating": rating,
            "buy_sell_signal": signal,
            "momentum": "Bullish" if _change_percent(current_price, previous_close) and _change_percent(current_price, previous_close) > 0 else "Bearish",
            "volatility": _volatility_label(_last_float(bb_upper), _last_float(bb_lower), _last_float(bb_middle)),
        },
        "trend_state": trend_state,
    }


def _clean_history(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame()
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in history.columns]
    if missing:
        return pd.DataFrame()
    return history[required].dropna(subset=["Open", "High", "Low", "Close"])


def _resample_four_hour(history: pd.DataFrame) -> pd.DataFrame:
    history = _clean_history(history)
    if history.empty:
        return history
    return (
        history.resample("4h")
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        .dropna(subset=["Open", "High", "Low", "Close"])
    )


def _history_request(timeframe: str) -> tuple[str, str]:
    return {
        "5m": ("60d", "5m"),
        "15m": ("60d", "15m"),
        "1h": ("730d", "1h"),
        "4h": ("730d", "1h"),
        "1D": ("1y", "1d"),
        "1W": ("5y", "1wk"),
        "1M": ("10y", "1mo"),
    }.get(timeframe, ("1y", "1d"))


def _normalize_timeframe(timeframe: str) -> str:
    normalized_timeframe = (timeframe or "").strip()
    if not normalized_timeframe:
        return "1D"
    return {
        "5m": "5m",
        "5M": "5m",
        "15m": "15m",
        "15M": "15m",
        "1h": "1h",
        "1H": "1h",
        "4h": "4h",
        "4H": "4h",
        "1d": "1D",
        "1D": "1D",
        "1w": "1W",
        "1W": "1W",
        "1m": "1M",
        "1M": "1M",
    }.get(normalized_timeframe, normalized_timeframe)


def _yahoo_symbol(exchange: str, symbol: str) -> str:
    normalized_symbol = symbol.strip().upper()
    provider_exchange = exchange.strip().lower()
    if provider_exchange in {"tvc", "capitalcom"} and normalized_symbol in {"XAUUSD", "GOLD", "TVC:GOLD"}:
        return "GC=F"
    if provider_exchange == "sgx" and "." not in normalized_symbol:
        return f"{normalized_symbol}.SI"
    return normalized_symbol


def _public_symbol(exchange: str, symbol: str) -> str:
    normalized_exchange = exchange.strip().upper()
    normalized_symbol = symbol.strip().upper()
    if normalized_exchange == "SGX" and normalized_symbol.endswith(".SI"):
        normalized_symbol = normalized_symbol[:-3]
    if normalized_exchange in {"TVC", "CAPITALCOM"} and normalized_symbol in {"GOLD", "TVC:GOLD"}:
        normalized_symbol = "XAUUSD"
    return f"{normalized_exchange}:{normalized_symbol}"


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.mask((loss == 0) & (gain > 0), 100)


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line, signal, macd_line - signal


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period).mean()


def _stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> tuple[pd.Series, pd.Series]:
    lowest_low = low.rolling(period).min()
    highest_high = high.rolling(period).max()
    k = ((close - lowest_low) / (highest_high - lowest_low).replace(0, pd.NA)) * 100
    return k, k.rolling(3).mean()


def _technical_rating(
    current_price: float | None,
    sma20: float | None,
    sma50: float | None,
    macd: float | None,
    macd_signal: float | None,
    rsi: float | None,
) -> float | None:
    signals: list[int] = []
    if current_price is not None and sma20 is not None:
        signals.append(1 if current_price > sma20 else -1)
    if current_price is not None and sma50 is not None:
        signals.append(1 if current_price > sma50 else -1)
    if macd is not None and macd_signal is not None:
        signals.append(1 if macd > macd_signal else -1)
    if rsi is not None:
        signals.append(1 if rsi >= 50 else -1)
    if not signals:
        return None
    return round(sum(signals) / len(signals) * 3, 2)


def _rating_signal(rating: float | None) -> str | None:
    if rating is None:
        return None
    if rating >= 1:
        return "BUY"
    if rating <= -1:
        return "SELL"
    return "NEUTRAL"


def _trend_state(rating: float | None) -> str | None:
    signal = _rating_signal(rating)
    return {"BUY": "bullish", "SELL": "bearish", "NEUTRAL": "neutral"}.get(signal)


def _last_float(value: Any) -> float | None:
    if hasattr(value, "iloc"):
        if len(value) == 0:
            return None
        value = value.iloc[-1]
    if value is None or pd.isna(value):
        return None
    return float(value)


def _previous_float(series: pd.Series) -> float | None:
    if len(series) < 2:
        return None
    value = series.iloc[-2]
    if value is None or pd.isna(value):
        return None
    return float(value)


def _metadata_float(values: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = values.get(key)
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _market_cap(fast_info: dict[str, Any], fallback_price: float | None) -> float | None:
    market_cap = _metadata_float(fast_info, "marketCap", "market_cap")
    if market_cap is not None:
        return market_cap

    shares = _metadata_float(fast_info, "shares")
    price = _metadata_float(fast_info, "lastPrice", "last_price")
    if price is None:
        price = fallback_price
    if shares is None or price is None:
        return None
    return shares * price


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _change_percent(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round(((current - previous) / previous) * 100, 2)


def _rsi_signal(value: float | None) -> str | None:
    if value is None:
        return None
    if value >= 70:
        return "Overbought"
    if value <= 30:
        return "Oversold"
    return "Neutral"


def _macd_trend(macd: float | None, signal: float | None) -> str | None:
    if macd is None or signal is None:
        return None
    if macd > signal:
        return "Bullish"
    if macd < signal:
        return "Bearish"
    return "Neutral"


def _bollinger_position(
    current_price: float | None,
    upper: float | None,
    lower: float | None,
) -> str | None:
    if current_price is None or upper is None or lower is None:
        return None
    if current_price >= upper:
        return "upper"
    if current_price <= lower:
        return "lower"
    return "middle"


def _bandwidth(upper: float | None, lower: float | None, middle: float | None) -> float | None:
    if upper is None or lower is None or middle in (None, 0):
        return None
    return round((upper - lower) / middle, 4)


def _relative_volume(current: float | None, average: float | None) -> float | None:
    if current is None or average in (None, 0):
        return None
    return round(current / average, 2)


def _stochastic_signal(k: float | None, d: float | None) -> str | None:
    if k is None or d is None:
        return None
    if k >= 80:
        return "Overbought"
    if k <= 20:
        return "Oversold"
    if k > d:
        return "Bullish"
    if k < d:
        return "Bearish"
    return "Neutral"


def _volatility_label(upper: float | None, lower: float | None, middle: float | None) -> str | None:
    bandwidth = _bandwidth(upper, lower, middle)
    if bandwidth is None:
        return None
    if bandwidth > 0.08:
        return "High"
    if bandwidth > 0.03:
        return "Medium"
    return "Low"


def _latest_timestamp(history: pd.DataFrame) -> str:
    latest = history.index[-1]
    if hasattr(latest, "to_pydatetime"):
        latest = latest.to_pydatetime()
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return latest.isoformat()
