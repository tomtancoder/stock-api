from __future__ import annotations

import math
from threading import Lock
from time import monotonic
from typing import Any

import yfinance as yf

from app.core.config import get_settings


_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = Lock()


def get_company_name(metadata: dict[str, Any] | None) -> str | None:
    for value in ((metadata or {}).get("short_name"), (metadata or {}).get("long_name")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_valuation_metadata(symbol: str) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    now = monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(normalized_symbol)
        if cached is not None and cached[0] > now:
            return dict(cached[1])

    try:
        metadata = _download_valuation_metadata(normalized_symbol)
    except Exception:  # noqa: BLE001 - fundamentals must not block analysis.
        return {}

    expires_at = monotonic() + get_settings().cache_ttl_seconds
    with _CACHE_LOCK:
        _CACHE[normalized_symbol] = (expires_at, dict(metadata))
    return metadata


def _download_valuation_metadata(symbol: str) -> dict[str, Any]:
    ticker = yf.Ticker(symbol)
    try:
        info = ticker.get_info() or {}
    except Exception:  # noqa: BLE001 - retain statement fallback when info fails.
        info = {}
    if not isinstance(info, dict):
        try:
            info = dict(info)
        except (TypeError, ValueError):
            info = {}

    diluted_eps_ttm = _positive_float(info.get("trailingEps"))
    if diluted_eps_ttm is None:
        try:
            diluted_eps_ttm = _statement_diluted_eps(
                ticker.get_income_stmt(freq="trailing")
            )
        except Exception:  # noqa: BLE001 - an absent statement is a valid null.
            diluted_eps_ttm = None

    return {
        "short_name": info.get("shortName"),
        "long_name": info.get("longName"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "diluted_eps_ttm": diluted_eps_ttm,
        "forward_eps": info.get("forwardEps"),
    }


def _statement_diluted_eps(statement: Any) -> float | None:
    try:
        row = statement.loc["DilutedEPS"]
    except (AttributeError, KeyError, TypeError):
        return None

    values = row.tolist() if hasattr(row, "tolist") else [row]
    for value in values:
        number = _positive_float(value)
        if number is not None:
            return number
    return None


def _clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def build_valuation_metrics(
    current_price: Any,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = metadata or {}
    price = _positive_float(current_price)
    diluted_eps_ttm = _positive_float(metadata.get("diluted_eps_ttm"))
    forward_eps = _positive_float(metadata.get("forward_eps"))
    trailing_pe = _positive_float(metadata.get("trailing_pe"))
    forward_pe = _positive_float(metadata.get("forward_pe"))
    pe_calculated = False

    if trailing_pe is None and price is not None and diluted_eps_ttm is not None:
        trailing_pe = round(price / diluted_eps_ttm, 4)
        pe_calculated = True

    if forward_pe is None and price is not None and forward_eps is not None:
        forward_pe = round(price / forward_eps, 4)

    return {
        "trailing_pe": trailing_pe,
        "forward_pe": forward_pe,
        "diluted_eps_ttm": diluted_eps_ttm,
        "forward_eps": forward_eps,
        "primary_pe": "trailing",
        "pe_calculated": pe_calculated,
    }


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number
