from __future__ import annotations

import math
import os
from typing import Any, Callable

from app.services.market_symbols import to_public_symbol, to_yahoo_symbol
from app.services.yfinance_analysis import get_analysis as get_yfinance_analysis


class TradingViewProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int = 502,
        retry_after_s: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_s = retry_after_s
        self.headers = (
            {"Retry-After": str(retry_after_s)} if retry_after_s is not None else None
        )


def _register_market_overrides() -> None:
    try:
        from tradingview_mcp.core.utils import validators
    except Exception:
        return

    validators.STOCK_EXCHANGES.add("sgx")
    validators.EXCHANGE_SCREENER["sgx"] = "singapore"
    if hasattr(validators, "_EXCHANGE_TV_PREFIX"):
        validators._EXCHANGE_TV_PREFIX["sgx"] = "SGX"


def _missing_dependency(*args, **kwargs):
    raise TradingViewProviderError(
        "TradingView MCP dependency is not installed. Run python -m pip install -e \".[dev]\".",
        status_code=500,
    )


_register_market_overrides()


try:  # pragma: no cover - exercised through provider tests with monkeypatches
    from tradingview_mcp.core.services.yahoo_finance_service import get_price
except Exception:  # pragma: no cover
    get_price = _missing_dependency

try:  # pragma: no cover
    from tradingview_mcp.core.services.screener_service import (
        analyze_coin,
        fetch_bollinger_analysis,
        fetch_trending_analysis,
        run_multi_timeframe_analysis as _run_multi_timeframe_analysis,
    )
except Exception:  # pragma: no cover
    analyze_coin = _missing_dependency
    fetch_bollinger_analysis = _missing_dependency
    fetch_trending_analysis = _missing_dependency
    _run_multi_timeframe_analysis = _missing_dependency

try:  # pragma: no cover
    from tradingview_mcp.core.services.screener_provider import _scan_with_retry
    from tradingview_mcp.core.utils.validators import (
        normalize_tradingview_symbol,
        resolve_screener_for_symbol,
    )
    from tradingview_screener import Query
except Exception:  # pragma: no cover
    _scan_with_retry = _missing_dependency
    normalize_tradingview_symbol = _missing_dependency
    resolve_screener_for_symbol = _missing_dependency
    Query = None

try:  # pragma: no cover
    from tradingview_mcp.core.services.backtest_service import (
        compare_strategies as _compare_strategies,
        run_backtest as _run_backtest,
        walk_forward_backtest as _walk_forward_backtest,
    )
except Exception:  # pragma: no cover
    _compare_strategies = _missing_dependency
    _run_backtest = _missing_dependency
    _walk_forward_backtest = _missing_dependency

try:  # pragma: no cover
    from tradingview_mcp.core.services.sentiment_service import (
        analyze_sentiment as _reddit_sentiment,
    )
except Exception:  # pragma: no cover
    _reddit_sentiment = _missing_dependency

try:  # pragma: no cover
    from tradingview_mcp.core.services.news_service import fetch_news_summary as _rss_news
except Exception:  # pragma: no cover
    _rss_news = _missing_dependency

try:  # pragma: no cover
    from tradingview_mcp.core.services.marketaux_service import (
        analyze_sentiment as _marketaux_sentiment,
        fetch_news_summary as _marketaux_news,
    )
except Exception:  # pragma: no cover
    _marketaux_sentiment = _missing_dependency
    _marketaux_news = _missing_dependency


def get_quote(exchange: str, symbol: str) -> dict[str, Any]:
    payload = get_price(_quote_symbol(exchange, symbol))
    _raise_if_error(payload)
    return {
        "symbol": payload.get("symbol", _normalize_symbol(symbol)),
        "exchange": _normalize_exchange(exchange),
        "price": payload.get("price"),
        "previous_close": payload.get("previous_close"),
        "change": payload.get("change"),
        "change_percent": payload.get("change_pct"),
        "currency": payload.get("currency"),
        "market_state": payload.get("market_state"),
        "fifty_two_week_high": payload.get("52w_high"),
        "fifty_two_week_low": payload.get("52w_low"),
        "source": payload.get("source"),
        "timestamp": payload.get("timestamp"),
        "warnings": [],
    }


def get_analysis(exchange: str, symbol: str, timeframe: str) -> dict[str, Any]:
    normalized_timeframe = _normalize_timeframe(timeframe)
    payload = get_yfinance_analysis(_normalize_exchange(exchange), symbol, normalized_timeframe)
    _raise_if_error(payload)
    return _with_public_timeframe(payload, normalized_timeframe)


def get_technical_analysis(
    exchange: str,
    symbol: str,
    timeframe: str,
    include_multi_timeframe: bool = False,
) -> dict[str, Any]:
    normalized_timeframe = _normalize_timeframe(timeframe)
    normalized_exchange = _normalize_exchange(exchange)
    payload = analyze_coin(
        _analysis_symbol(exchange, symbol),
        normalized_exchange,
        normalized_timeframe,
    )
    _raise_if_error(payload)
    response = _with_public_timeframe(_plain_dict(payload), normalized_timeframe)
    warnings = list(response.get("warnings") or [])

    try:
        reference_data = _get_tradingview_reference_data(exchange, symbol)
    except Exception:  # noqa: BLE001 - reference data must not block core TA.
        reference_data = {
            "trailing_pe": None,
            "fifty_two_week_high": None,
            "fifty_two_week_low": None,
        }
        warnings.append("TradingView reference data is temporarily unavailable.")

    price_data = response.get("price_data")
    if not isinstance(price_data, dict):
        price_data = {}

    response = {
        **response,
        "price_data": {
            **price_data,
            "fifty_two_week_high": reference_data["fifty_two_week_high"],
            "fifty_two_week_low": reference_data["fifty_two_week_low"],
        },
        "valuation_metrics": {
            "trailing_pe": reference_data["trailing_pe"],
            "primary_pe": "trailing",
        },
        "source": "tradingview_mcp",
        "warnings": warnings,
    }

    if include_multi_timeframe:
        full_symbol = _tradingview_symbol(exchange, symbol)
        try:
            multi_timeframe = _plain_dict(
                _run_multi_timeframe_analysis(full_symbol, normalized_exchange)
            )
        except Exception as exc:  # noqa: BLE001 - preserve the base TA response.
            multi_timeframe = {
                "error": {
                    "code": "UPSTREAM_ERROR",
                    "message": f"Multi-timeframe analysis failed: {exc}",
                    "retryable": True,
                }
            }
        response["multi_timeframe"] = multi_timeframe
        if _multi_timeframe_is_incomplete(multi_timeframe):
            warnings.append("TradingView multi-timeframe analysis is incomplete.")

    return response


def _get_tradingview_reference_data(exchange: str, symbol: str) -> dict[str, float | None]:
    if Query is None:
        _missing_dependency()

    full_symbol = _tradingview_symbol(exchange, symbol)
    normalized_exchange = _normalize_exchange(exchange)
    screener = resolve_screener_for_symbol(full_symbol, normalized_exchange)
    query = (
        Query()
        .select(
            "price_earnings_ttm",
            "price_52_week_high",
            "price_52_week_low",
        )
        .set_tickers(full_symbol)
        .set_markets(screener)
    )
    _, rows = _scan_with_retry(
        query,
        cache_key=("technical_reference_v1", screener, full_symbol),
    )
    if rows.empty:
        raise LookupError(f"No TradingView reference data found for {full_symbol}.")

    row = rows.iloc[0]
    return {
        "trailing_pe": _positive_float(row.get("price_earnings_ttm")),
        "fifty_two_week_high": _finite_float(row.get("price_52_week_high")),
        "fifty_two_week_low": _finite_float(row.get("price_52_week_low")),
    }


def get_gainers(exchange: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
    rows = fetch_trending_analysis(
        _provider_exchange(exchange),
        timeframe=_provider_timeframe(timeframe),
        limit=limit,
    )
    _raise_if_error(rows)
    return _row_dicts(rows)


def get_losers(exchange: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
    rows = fetch_trending_analysis(
        _provider_exchange(exchange),
        timeframe=_provider_timeframe(timeframe),
        limit=limit,
    )
    _raise_if_error(rows)
    normalized_rows = _row_dicts(rows)
    normalized_rows.sort(key=lambda row: row.get("changePercent") or 0)
    return normalized_rows[:limit]


def get_bollinger_scan(
    exchange: str,
    timeframe: str,
    bbw_threshold: float,
    limit: int,
) -> list[dict[str, Any]]:
    rows = fetch_bollinger_analysis(
        _provider_exchange(exchange),
        timeframe=_provider_timeframe(timeframe),
        bbw_filter=bbw_threshold,
        limit=limit,
    )
    _raise_if_error(rows)
    return _row_dicts(rows)


def get_rating_filter(
    exchange: str,
    timeframe: str,
    rating: int,
    limit: int,
) -> list[dict[str, Any]]:
    rows = fetch_trending_analysis(
        _provider_exchange(exchange),
        timeframe=_provider_timeframe(timeframe),
        filter_type="rating",
        rating_filter=rating,
        limit=limit,
    )
    _raise_if_error(rows)
    return _row_dicts(rows)


def run_backtest(exchange: str, symbol: str, request) -> dict[str, Any]:
    return _call_payload(
        _run_backtest,
        _quote_symbol(exchange, symbol),
        request.strategy,
        request.period,
        request.initial_capital,
        request.commission_pct,
        request.slippage_pct,
        request.interval,
        request.include_trade_log,
        request.include_equity_curve,
    )


def compare_strategies(exchange: str, symbol: str, request) -> dict[str, Any]:
    return _call_payload(
        _compare_strategies,
        _quote_symbol(exchange, symbol),
        request.period,
        request.initial_capital,
        request.commission_pct,
        request.slippage_pct,
        request.interval,
    )


def walk_forward_backtest(exchange: str, symbol: str, request) -> dict[str, Any]:
    return _call_payload(
        _walk_forward_backtest,
        _quote_symbol(exchange, symbol),
        request.strategy,
        request.period,
        request.initial_capital,
        request.commission_pct,
        request.slippage_pct,
        request.n_splits,
        request.train_ratio,
        request.interval,
    )


def get_sentiment(symbol: str, category: str, limit: int) -> dict[str, Any]:
    sentiment_source = _marketaux_sentiment if os.getenv("MARKETAUX_API_TOKEN") else _reddit_sentiment
    return _call_payload(sentiment_source, _normalize_symbol(symbol), category, limit)


def get_news(symbol: str | None, category: str, limit: int) -> dict[str, Any]:
    news_source = _marketaux_news if os.getenv("MARKETAUX_API_TOKEN") else _rss_news
    return _call_payload(news_source, _normalize_symbol(symbol) if symbol else None, category, limit)


def _call_payload(func: Callable, *args) -> dict[str, Any]:
    payload = func(*args)
    _raise_if_error(payload)
    return payload


def _raise_if_error(payload: Any) -> None:
    if not isinstance(payload, dict) or "error" not in payload:
        return

    error = payload["error"]
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("code") or "TradingView provider error.")
        code = str(error.get("code") or "")
        retryable = bool(error.get("retryable", False))
        retry_after_s = _retry_after_s(error.get("retry_after_s"))
    else:
        message = str(error)
        code = ""
        retryable = False
        retry_after_s = None

    if code == "SYMBOL_NOT_FOUND" or "No data found" in message:
        status_code = 404
    elif retryable:
        status_code = 503
    else:
        status_code = 502
    raise TradingViewProviderError(
        message,
        status_code=status_code,
        retry_after_s=retry_after_s if status_code == 503 else None,
    )


def _row_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [_plain_dict(row) for row in rows]


def _with_public_timeframe(payload: dict[str, Any], timeframe: str) -> dict[str, Any]:
    if "timeframe" not in payload:
        return payload
    return {**payload, "timeframe": timeframe}


def _plain_dict(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _plain_dict(item) for key, item in value.items()}
    if hasattr(value, "items"):
        return {key: _plain_dict(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_dict(item) for item in value]
    return value


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _quote_symbol(exchange: str, symbol: str) -> str:
    return to_yahoo_symbol(exchange, symbol)


def _analysis_symbol(exchange: str, symbol: str) -> str:
    normalized_symbol = _normalize_symbol(symbol)
    if _provider_exchange(exchange) == "sgx" and normalized_symbol.endswith(".SI"):
        return normalized_symbol[:-3]
    return normalized_symbol


def _tradingview_symbol(exchange: str, symbol: str) -> str:
    return normalize_tradingview_symbol(
        _analysis_symbol(exchange, symbol),
        _normalize_exchange(exchange),
    )


def _normalize_exchange(exchange: str) -> str:
    return exchange.strip().upper()


def _provider_exchange(exchange: str) -> str:
    return exchange.strip().lower()


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


def _provider_timeframe(timeframe: str) -> str:
    normalized_timeframe = _normalize_timeframe(timeframe)
    return {
        "1D": "1d",
        "1W": "1W",
        "1M": "1M",
    }.get(normalized_timeframe, normalized_timeframe)


def _retry_after_s(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_float(value: Any) -> float | None:
    number = _finite_float(value)
    return number if number is not None and number > 0 else None


def _multi_timeframe_is_incomplete(payload: Any) -> bool:
    if not isinstance(payload, dict) or "error" in payload:
        return True
    timeframes = payload.get("timeframes")
    if not isinstance(timeframes, dict) or not timeframes:
        return True
    return any(
        isinstance(result, dict) and "error" in result
        for result in timeframes.values()
    )
