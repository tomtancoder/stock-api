from collections.abc import Callable
from typing import Any

import pandas as pd
import yfinance as yf
from cachetools import TTLCache, cached

from app.core.config import get_settings
from app.schemas import FinancialMetrics, QuoteResponse, StockSnapshot

_snapshot_cache = TTLCache(maxsize=512, ttl=get_settings().cache_ttl_seconds)


class YFinanceError(RuntimeError):
    """Raised when yFinance cannot provide usable data."""


def get_stock_snapshot(symbol: str) -> StockSnapshot:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("Stock symbol is required.")
    return _fetch_stock_snapshot(normalized_symbol)


@cached(cache=_snapshot_cache)
def _fetch_stock_snapshot(symbol: str) -> StockSnapshot:
    ticker = yf.Ticker(symbol)

    warnings: list[str] = []
    info = _safe_info(ticker, symbol, warnings)
    fast_info = _safe_fast_info(ticker, warnings)
    quote = _build_quote(symbol, info, fast_info, ticker, warnings)

    if quote.current_price is None and not info:
        raise YFinanceError(f"Could not fetch yFinance data for {symbol}.")
    if info and info.get("quoteType") in (None, "NONE") and quote.current_price is None:
        raise ValueError(f"No yFinance data found for symbol {symbol}.")

    financials = _build_financials(ticker, info, warnings)

    return StockSnapshot(
        symbol=symbol,
        quote=quote,
        financials=financials,
        warnings=_dedupe(warnings),
    )


def _safe_info(ticker: yf.Ticker, symbol: str, warnings: list[str]) -> dict[str, Any]:
    try:
        return ticker.info or {}
    except Exception as exc:  # pragma: no cover - depends on yFinance internals
        warnings.append(f"Full yFinance quote info is unavailable for {symbol}: {exc}")
        return {}


def _safe_fast_info(ticker: yf.Ticker, warnings: list[str]) -> Any:
    try:
        return ticker.fast_info
    except Exception as exc:  # pragma: no cover - depends on yFinance internals
        warnings.append(f"Fast yFinance quote info is unavailable: {exc}")
        return {}


def _build_quote(
    symbol: str,
    info: dict[str, Any],
    fast_info: Any,
    ticker: yf.Ticker,
    warnings: list[str],
) -> QuoteResponse:
    currency = info.get("financialCurrency") or info.get("currency") or _fast_info_value(
        fast_info,
        "currency",
    )
    if not isinstance(currency, str) or not currency.strip():
        currency = None
        warnings.append("Currency is missing; amounts may not be comparable across markets.")

    current_price = (
        _first_number(info, "currentPrice", "regularMarketPrice", "previousClose")
        or _fast_info_number(fast_info, "last_price", "lastPrice", "regularMarketPreviousClose")
        or _latest_close(ticker, warnings)
    )
    if current_price is None:
        warnings.append("Current price is missing.")

    shares_outstanding = _first_number(
        info,
        "sharesOutstanding",
        "impliedSharesOutstanding",
    ) or _fast_info_number(fast_info, "shares")
    if shares_outstanding is None:
        warnings.append("Shares outstanding is missing.")

    return QuoteResponse(
        symbol=symbol,
        short_name=_to_string_or_none(info.get("shortName") or info.get("longName")),
        exchange=_to_string_or_none(info.get("exchange")),
        currency=currency,
        current_price=current_price,
        market_cap=_first_number(info, "marketCap") or _fast_info_number(fast_info, "market_cap"),
        shares_outstanding=shares_outstanding,
        trailing_pe=_first_number(info, "trailingPE"),
        forward_pe=_first_number(info, "forwardPE"),
        price_to_book=_first_number(info, "priceToBook"),
        enterprise_to_ebitda=_first_number(info, "enterpriseToEbitda"),
        dividend_yield=_first_number(info, "dividendYield"),
        warnings=_dedupe(warnings),
    )


def _build_financials(
    ticker: yf.Ticker,
    info: dict[str, Any],
    warnings: list[str],
) -> FinancialMetrics:
    cashflow = _safe_statement(lambda: ticker.cashflow, "cash flow statement", warnings)
    balance_sheet = _safe_statement(lambda: ticker.balance_sheet, "balance sheet", warnings)
    income_stmt = _safe_statement(lambda: ticker.financials, "income statement", warnings)

    operating_cash_flow = _statement_value(
        cashflow,
        "Operating Cash Flow",
        "Total Cash From Operating Activities",
    )
    capital_expenditures = _statement_value(
        cashflow,
        "Capital Expenditure",
        "Capital Expenditures",
    )
    free_cash_flow = _statement_value(cashflow, "Free Cash Flow")

    if free_cash_flow is None and operating_cash_flow is not None and capital_expenditures is not None:
        free_cash_flow = operating_cash_flow + capital_expenditures

    if free_cash_flow is None:
        free_cash_flow = _first_number(info, "freeCashflow")
        if free_cash_flow is None:
            warnings.append("Free cash flow is missing from yFinance statements and quote info.")

    revenue = _statement_value(income_stmt, "Total Revenue", "Revenue")
    net_income = _statement_value(income_stmt, "Net Income", "Net Income Common Stockholders")
    total_debt = _statement_value(
        balance_sheet,
        "Total Debt",
        "Long Term Debt And Capital Lease Obligation",
    )
    cash = _statement_value(
        balance_sheet,
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
    )
    total_equity = _statement_value(
        balance_sheet,
        "Stockholders Equity",
        "Total Stockholder Equity",
    )

    if revenue is None:
        warnings.append("Revenue is missing from the latest income statement.")
    if net_income is None:
        warnings.append("Net income is missing from the latest income statement.")

    return FinancialMetrics(
        revenue=revenue,
        net_income=net_income,
        free_cash_flow=free_cash_flow,
        operating_cash_flow=operating_cash_flow,
        capital_expenditures=capital_expenditures,
        total_debt=total_debt,
        cash_and_equivalents=cash,
        total_equity=total_equity,
    )


def _safe_statement(
    getter: Callable[[], pd.DataFrame],
    label: str,
    warnings: list[str],
) -> pd.DataFrame:
    try:
        statement = getter()
    except Exception:  # pragma: no cover - depends on yFinance internals
        warnings.append(f"Could not fetch {label} from yFinance.")
        return pd.DataFrame()

    if statement is None or statement.empty:
        warnings.append(f"Latest {label} is missing from yFinance.")
        return pd.DataFrame()

    return statement


def _statement_value(statement: pd.DataFrame, *row_names: str) -> float | None:
    if statement.empty:
        return None

    for row_name in row_names:
        if row_name not in statement.index:
            continue

        row = statement.loc[row_name]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]

        for value in row.tolist():
            number = _to_float_or_none(value)
            if number is not None:
                return number

    return None


def _first_number(info: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = _to_float_or_none(info.get(key))
        if number is not None:
            return number
    return None


def _fast_info_number(fast_info: Any, *keys: str) -> float | None:
    for key in keys:
        number = _to_float_or_none(_fast_info_value(fast_info, key))
        if number is not None:
            return number
    return None


def _fast_info_value(fast_info: Any, key: str) -> Any:
    if not fast_info:
        return None
    if hasattr(fast_info, "get"):
        try:
            value = fast_info.get(key)
            if value is not None:
                return value
        except Exception:
            pass
    try:
        return getattr(fast_info, key)
    except Exception:
        return None


def _latest_close(ticker: yf.Ticker, warnings: list[str]) -> float | None:
    try:
        history = ticker.history(period="5d")
    except Exception as exc:  # pragma: no cover - depends on yFinance internals
        warnings.append(f"Recent price history is unavailable from yFinance: {exc}")
        return None

    if history is None or history.empty or "Close" not in history:
        return None

    closes = history["Close"].dropna()
    if closes.empty:
        return None
    return _to_float_or_none(closes.iloc[-1])


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _to_string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _dedupe(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(messages))
