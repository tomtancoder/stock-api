from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any, Literal

import pandas as pd
import yfinance as yf

from app.services.market_symbols import (
    normalize_exchange,
    to_public_symbol,
    to_yahoo_symbol,
)
from app.services.valuation_types import (
    FactProvenance,
    FinancialPeriod,
    ValuationFundamentals,
)


StatementKind = Literal["cashflow", "income", "balance"]


class YFinanceStatementsError(RuntimeError):
    """Raised when Yahoo statements cannot be normalized safely."""


_FIELD_ALIASES: dict[str, tuple[StatementKind, tuple[str, ...]]] = {
    "operating_cash_flow": (
        "cashflow",
        ("Operating Cash Flow", "Total Cash From Operating Activities"),
    ),
    "capital_expenditure": (
        "cashflow",
        ("Capital Expenditure", "Capital Expenditures"),
    ),
    "stock_based_compensation": (
        "cashflow",
        ("Stock Based Compensation", "Share Based Compensation"),
    ),
    "revenue": ("income", ("Total Revenue", "Revenue")),
    "net_income_common": (
        "income",
        ("Net Income Common Stockholders", "Net Income"),
    ),
    "common_equity": (
        "balance",
        (
            "Stockholders Equity",
            "Common Stock Equity",
            "Total Stockholder Equity",
        ),
    ),
    "cash_and_equivalents": (
        "balance",
        (
            "Cash And Cash Equivalents",
            "Cash Cash Equivalents And Short Term Investments",
        ),
    ),
    "total_assets": ("balance", ("Total Assets",)),
    "total_debt": (
        "balance",
        ("Total Debt", "Long Term Debt And Capital Lease Obligation"),
    ),
    "diluted_shares": (
        "income",
        (
            "Diluted Average Shares",
            "Weighted Average Number Of Diluted Shares Outstanding",
        ),
    ),
    "common_dividends": (
        "cashflow",
        ("Cash Dividends Paid", "Common Stock Dividend Paid"),
    ),
}

_INTEREST_ALIASES = ("Interest Paid Supplemental", "Interest Paid")
_FLOW_FIELDS = {
    field
    for field, (kind, _aliases) in _FIELD_ALIASES.items()
    if kind in {"cashflow", "income"}
}
_FACT_FIELDS = (*_FIELD_ALIASES, "interest_paid_outside_operating")
_CLASSIFICATION_KEYS = (
    "interestPaidClassification",
    "interest_paid_classification",
    "interestClassification",
    "interest_classification",
)


def fetch_yfinance_fundamentals(
    exchange: str, symbol: str
) -> ValuationFundamentals:
    venue = normalize_exchange(exchange)
    yahoo_symbol = to_yahoo_symbol(venue, symbol)
    public_symbol = to_public_symbol(venue, symbol)
    warnings: list[str] = []

    try:
        ticker = yf.Ticker(yahoo_symbol)
        info = _read_info(ticker, warnings)
        fast_info = _read_fast_info(ticker, warnings)
        currency = _resolve_currency(info, fast_info)
        if currency is None:
            raise ValueError("Yahoo did not provide a financial or quote currency")
        frames = _read_statement_frames(ticker, warnings)
        shares = _read_shares(ticker, warnings)
    except YFinanceStatementsError as exc:
        raise YFinanceStatementsError(
            f"Unable to fetch yFinance statements for {yahoo_symbol}: {exc}"
        ) from (exc.__cause__ or exc)
    except Exception as exc:  # noqa: BLE001 - normalize provider failures.
        raise YFinanceStatementsError(
            f"Unable to fetch yFinance statements for {yahoo_symbol}: {exc}"
        ) from exc

    valid_frames = _currency_consistent_frames(frames, currency, warnings)
    interest_classification = _interest_classification(info, valid_frames)
    annual_periods = _build_annual_periods(
        valid_frames,
        currency,
        interest_classification,
    )
    trailing_period = _build_trailing_period(
        valid_frames,
        currency,
        interest_classification,
    )
    periods = sorted(
        [*annual_periods, *([trailing_period] if trailing_period else [])],
        key=lambda period: (period.period_end, period.is_ttm),
    )
    current_shares = _current_shares(shares, info, fast_info)
    missing_fields = [
        field
        for field in _FACT_FIELDS
        if not any(getattr(period, field) is not None for period in periods)
    ]
    if current_shares is None:
        missing_fields.append("current_diluted_shares")

    sources = {"financial_statements": "yfinance"}
    if current_shares is not None:
        sources["current_diluted_shares"] = "yfinance"

    return ValuationFundamentals(
        symbol=public_symbol,
        exchange=venue,
        currency=currency,
        primary_source=("yfinance_sgx" if venue == "SGX" else "yfinance_fallback"),
        provider_security_type=_text(info.get("quoteType")),
        sector=_text(info.get("sector")),
        industry=_text(info.get("industry")),
        issuer_classification=_text(info.get("category")),
        current_diluted_shares=current_shares,
        periods=periods,
        fetched_at=datetime.now(timezone.utc),
        sources=sources,
        missing_fields=missing_fields,
        warnings=_unique(warnings),
    )


def _read_info(ticker: Any, warnings: list[str]) -> dict[str, Any]:
    try:
        return _as_dict(ticker.get_info())
    except Exception as exc:  # noqa: BLE001 - quote metadata is best effort.
        warnings.append(f"yFinance metadata unavailable: {exc}")
        return {}


def _read_fast_info(ticker: Any, warnings: list[str]) -> dict[str, Any]:
    try:
        return _as_dict(ticker.fast_info)
    except Exception as exc:  # noqa: BLE001 - quote metadata is best effort.
        warnings.append(f"yFinance fast quote metadata unavailable: {exc}")
        return {}


def _read_statement_frames(
    ticker: Any, warnings: list[str]
) -> dict[tuple[StatementKind, str], pd.DataFrame]:
    getters = {
        "cashflow": ticker.get_cashflow,
        "income": ticker.get_income_stmt,
        "balance": ticker.get_balance_sheet,
    }
    frames: dict[tuple[StatementKind, str], pd.DataFrame] = {}
    for kind, getter in getters.items():
        try:
            frames[(kind, "yearly")] = _as_frame(getter(freq="yearly"))
        except Exception as exc:  # noqa: BLE001 - annual statements are required.
            raise YFinanceStatementsError(
                f"Unable to fetch yearly yFinance {kind} statement: {exc}"
            ) from exc

        for frequency in ("quarterly", "trailing"):
            try:
                frames[(kind, frequency)] = _as_frame(getter(freq=frequency))
            except Exception as exc:  # noqa: BLE001 - these variants are optional.
                frames[(kind, frequency)] = pd.DataFrame()
                warnings.append(
                    f"yFinance {frequency} {kind} statement unavailable: {exc}"
                )
    return frames


def _read_shares(ticker: Any, warnings: list[str]) -> Any:
    try:
        return ticker.get_shares_full()
    except Exception as exc:  # noqa: BLE001 - statement shares remain available.
        warnings.append(f"yFinance share history unavailable: {exc}")
        return None


def _as_frame(value: Any) -> pd.DataFrame:
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {
            key: getattr(value, key)
            for key in (
                "currency",
                "shares",
                "sharesOutstanding",
                "marketCap",
            )
            if hasattr(value, key)
        }


def _resolve_currency(
    info: Mapping[str, Any], fast_info: Mapping[str, Any]
) -> str | None:
    for candidate in (
        info.get("financialCurrency"),
        info.get("currency"),
        fast_info.get("currency"),
    ):
        currency = _text(candidate)
        if currency:
            return currency.upper()
    return None


def _currency_consistent_frames(
    frames: Mapping[tuple[StatementKind, str], pd.DataFrame],
    currency: str,
    warnings: list[str],
) -> dict[tuple[StatementKind, str], pd.DataFrame]:
    valid: dict[tuple[StatementKind, str], pd.DataFrame] = {}
    for key, frame in frames.items():
        statement_currency = _frame_currency(frame)
        if statement_currency and statement_currency != currency:
            kind, frequency = key
            warnings.append(
                f"Ignored {frequency} {kind} statement currency "
                f"{statement_currency}; expected {currency}."
            )
            valid[key] = pd.DataFrame()
        else:
            valid[key] = frame
    return valid


def _frame_currency(frame: pd.DataFrame) -> str | None:
    for key in ("financialCurrency", "financial_currency", "currency"):
        currency = _text(frame.attrs.get(key))
        if currency:
            return currency.upper()
    return None


def _interest_classification(
    info: Mapping[str, Any],
    frames: Mapping[tuple[StatementKind, str], pd.DataFrame],
) -> Literal["inside", "outside"] | None:
    metadata: list[Mapping[str, Any]] = [info]
    metadata.extend(
        frame.attrs
        for (kind, _frequency), frame in frames.items()
        if kind == "cashflow"
    )
    for source in metadata:
        outside = _metadata_boolean(
            source,
            "interestPaidOutsideOperatingCashFlow",
            "interest_paid_outside_operating_cash_flow",
        )
        if outside is not None:
            return "outside" if outside else "inside"
        inside = _metadata_boolean(
            source,
            "interestPaidIncludedInOperatingCashFlow",
            "interest_paid_in_operating_cash_flow",
            "interest_included_in_operating_cash_flow",
        )
        if inside is not None:
            return "inside" if inside else "outside"
        for key in _CLASSIFICATION_KEYS:
            value = _text(source.get(key))
            if not value:
                continue
            normalized = value.casefold().replace("_", " ").replace("-", " ")
            if any(word in normalized for word in ("financing", "investing", "outside")):
                return "outside"
            if any(word in normalized for word in ("operating", "inside", "included", "cfo")):
                return "inside"
    return None


def _metadata_boolean(source: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, bool):
            return value
    return None


def _build_annual_periods(
    frames: Mapping[tuple[StatementKind, str], pd.DataFrame],
    currency: str,
    interest_classification: Literal["inside", "outside"] | None,
) -> list[FinancialPeriod]:
    yearly = {
        kind: frames[(kind, "yearly")]
        for kind in ("cashflow", "income", "balance")
    }
    period_ends = sorted(
        {
            period_end
            for frame in yearly.values()
            for period_end in _selected_columns(frame)
        }
    )
    return [
        _build_period(
            period_end,
            currency,
            yearly,
            interest_classification,
            is_ttm=False,
            form="yearly",
        )
        for period_end in period_ends
    ]


def _build_trailing_period(
    frames: Mapping[tuple[StatementKind, str], pd.DataFrame],
    currency: str,
    interest_classification: Literal["inside", "outside"] | None,
) -> FinancialPeriod | None:
    trailing = {
        kind: frames[(kind, "trailing")]
        for kind in ("cashflow", "income", "balance")
    }
    quarterly = {
        kind: frames[(kind, "quarterly")]
        for kind in ("cashflow", "income", "balance")
    }
    direct_dates = {
        period_end
        for frame in trailing.values()
        for period_end in _selected_columns(frame)
    }
    quarter_dates = sorted(
        {
            period_end
            for frame in quarterly.values()
            for period_end in _selected_columns(frame)
        }
    )
    if not direct_dates and len(quarter_dates) < 4:
        return None

    period_end = max(direct_dates or set(quarter_dates))
    direct = _build_period(
        period_end,
        currency,
        trailing,
        interest_classification,
        is_ttm=True,
        form="trailing",
    )
    if len(quarter_dates) < 4:
        return direct

    latest_quarters = quarter_dates[-4:]
    updates: dict[str, Any] = {}
    sources = dict(direct.sources)
    for field, (kind, aliases) in _FIELD_ALIASES.items():
        if getattr(direct, field) is not None:
            continue
        if field in _FLOW_FIELDS:
            extracted = _sum_quarters(quarterly[kind], latest_quarters, aliases)
        else:
            extracted = _extract_value(quarterly[kind], period_end, aliases)
        if extracted is None:
            continue
        value, concept = extracted
        updates[field] = value
        sources[field] = _provenance(
            concept,
            period_end,
            currency,
            "quarterly_ttm",
        )

    if direct.interest_paid_outside_operating is None:
        interest = _quarterly_interest(
            quarterly["cashflow"],
            latest_quarters,
            interest_classification,
        )
        if interest is not None:
            value, concept = interest
            updates["interest_paid_outside_operating"] = value
            sources["interest_paid_outside_operating"] = _provenance(
                concept,
                period_end,
                currency,
                "quarterly_ttm",
            )

    updates["sources"] = sources
    return direct.model_copy(update=updates)


def _build_period(
    period_end: date,
    currency: str,
    frames: Mapping[StatementKind, pd.DataFrame],
    interest_classification: Literal["inside", "outside"] | None,
    *,
    is_ttm: bool,
    form: str,
) -> FinancialPeriod:
    values: dict[str, Any] = {
        "period_end": period_end,
        "fiscal_year": None if is_ttm else period_end.year,
        "is_ttm": is_ttm,
        "currency": currency,
    }
    sources: dict[str, FactProvenance] = {}
    for field, (kind, aliases) in _FIELD_ALIASES.items():
        extracted = _extract_value(frames[kind], period_end, aliases)
        if extracted is None:
            continue
        value, concept = extracted
        values[field] = value
        sources[field] = _provenance(concept, period_end, currency, form)

    interest = _period_interest(
        frames["cashflow"], period_end, interest_classification
    )
    if interest is not None:
        value, concept = interest
        values["interest_paid_outside_operating"] = value
        sources["interest_paid_outside_operating"] = _provenance(
            concept, period_end, currency, form
        )

    values["sources"] = sources
    return FinancialPeriod(**values)


def _period_interest(
    frame: pd.DataFrame,
    period_end: date,
    classification: Literal["inside", "outside"] | None,
) -> tuple[float, str] | None:
    if classification == "inside":
        return 0.0, "included_in_operating_cash_flow"
    if classification == "outside":
        return _extract_value(frame, period_end, _INTEREST_ALIASES)
    return None


def _quarterly_interest(
    frame: pd.DataFrame,
    period_ends: list[date],
    classification: Literal["inside", "outside"] | None,
) -> tuple[float, str] | None:
    if classification == "inside":
        return 0.0, "included_in_operating_cash_flow"
    if classification == "outside":
        return _sum_quarters(frame, period_ends, _INTEREST_ALIASES)
    return None


def _sum_quarters(
    frame: pd.DataFrame,
    period_ends: list[date],
    aliases: tuple[str, ...],
) -> tuple[float, str] | None:
    values: list[float] = []
    concepts: list[str] = []
    for period_end in period_ends:
        extracted = _extract_value(frame, period_end, aliases)
        if extracted is None:
            return None
        value, concept = extracted
        values.append(value)
        concepts.append(concept)
    concept = concepts[-1] if len(set(concepts)) == 1 else " + ".join(_unique(concepts))
    return sum(values), concept


def _extract_value(
    frame: pd.DataFrame,
    period_end: date,
    aliases: tuple[str, ...],
) -> tuple[float, str] | None:
    column = _selected_columns(frame).get(period_end)
    if column is None:
        return None
    for alias in aliases:
        for row_position, row_name in enumerate(frame.index):
            if row_name != alias:
                continue
            value = _finite_float(frame.iloc[row_position, column])
            if value is not None:
                return value, alias
    return None


def _selected_columns(frame: pd.DataFrame) -> dict[date, int]:
    selected: dict[date, tuple[pd.Timestamp, int]] = {}
    for position, raw_column in enumerate(frame.columns):
        timestamp = _timestamp(raw_column)
        if timestamp is None:
            continue
        period_end = timestamp.date()
        existing = selected.get(period_end)
        if existing is None or timestamp > existing[0]:
            selected[period_end] = (timestamp, position)
    return {period_end: item[1] for period_end, item in selected.items()}


def _timestamp(value: Any) -> pd.Timestamp | None:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp


def _provenance(
    concept: str, period_end: date, currency: str, form: str
) -> FactProvenance:
    return FactProvenance(
        provider="yfinance",
        concept=concept,
        form=form,
        period_end=period_end,
        unit=currency,
    )


def _current_shares(
    shares: Any,
    info: Mapping[str, Any],
    fast_info: Mapping[str, Any],
) -> float | None:
    if isinstance(shares, pd.Series) and not shares.empty:
        candidates: list[tuple[pd.Timestamp, int, float]] = []
        for position, (index, value) in enumerate(shares.items()):
            number = _positive_float(value)
            if number is None:
                continue
            timestamp = _timestamp(index) or pd.Timestamp.min
            candidates.append((timestamp, position, number))
        if candidates:
            return max(candidates, key=lambda item: (item[0], item[1]))[2]
    for candidate in (
        fast_info.get("shares"),
        fast_info.get("sharesOutstanding"),
        info.get("sharesOutstanding"),
    ):
        number = _positive_float(candidate)
        if number is not None:
            return number
    return None


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_float(value: Any) -> float | None:
    number = _finite_float(value)
    return number if number is not None and number > 0 else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
