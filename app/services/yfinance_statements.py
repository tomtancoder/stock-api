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
            "Unitholder Equity",
            "Unitholders Equity",
            "Unitholders' Funds",
            "Net Assets Attributable To Unitholders",
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
            "Weighted Average Number Of Units Outstanding",
            "Basic Average Shares",
            "Diluted Average Shares",
            "Weighted Average Number Of Diluted Shares Outstanding",
        ),
    ),
    "common_dividends": (
        "cashflow",
        ("Cash Dividends Paid", "Common Stock Dividend Paid"),
    ),
    "distribution_per_unit": (
        "cashflow",
        ("Distribution Per Unit", "Distributions Per Unit", "DPU"),
    ),
    "nav_per_unit": (
        "balance",
        ("NAV Per Unit", "Net Asset Value Per Unit"),
    ),
}

_INTEREST_ALIASES = ("Interest Paid Supplemental", "Interest Paid")
_ADDITIVE_FIELDS = {
    field
    for field, (kind, _aliases) in _FIELD_ALIASES.items()
    if kind == "cashflow" or field in {"revenue", "net_income_common"}
}
_FACT_FIELDS = (*_FIELD_ALIASES, "interest_paid_outside_operating")
_REIT_ONLY_FACT_FIELDS = frozenset({"distribution_per_unit", "nav_per_unit"})
_REIT_REQUIRED_FACT_FIELDS = (
    "distribution_per_unit",
    "diluted_shares",
    "nav_per_unit",
)
_CLASSIFICATION_KEYS = (
    "interestPaidClassification",
    "interest_paid_classification",
    "interestClassification",
    "interest_classification",
)
_MAX_UNIT_OBSERVATION_AGE_DAYS = 31


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
        is_reit = _is_reit(info)
        dividends = _read_dividends(ticker, warnings) if is_reit else None
    except YFinanceStatementsError as exc:
        raise YFinanceStatementsError(
            f"Unable to fetch yFinance statements for {yahoo_symbol}: {exc}"
        ) from (exc.__cause__ or exc)
    except Exception as exc:  # noqa: BLE001 - normalize provider failures.
        raise YFinanceStatementsError(
            f"Unable to fetch yFinance statements for {yahoo_symbol}: {exc}"
        ) from exc

    valid_frames = _currency_consistent_frames(frames, currency, warnings)
    annual_periods = _build_annual_periods(
        valid_frames,
        currency,
        info,
    )
    trailing_period = _build_trailing_period(
        valid_frames,
        currency,
        info,
    )
    periods = sorted(
        [*annual_periods, *([trailing_period] if trailing_period else [])],
        key=lambda period: (period.period_end, period.is_ttm),
    )
    if is_reit:
        periods = _normalize_reit_periods(
            periods,
            dividends,
            shares,
            currency,
            info,
            warnings,
        )
    current_shares = (
        _current_reit_shares(periods, info, fast_info)
        if is_reit
        else _current_shares(shares, info, fast_info)
    )
    required_fact_fields = (
        _REIT_REQUIRED_FACT_FIELDS
        if is_reit
        else tuple(
            field
            for field in _FACT_FIELDS
            if field not in _REIT_ONLY_FACT_FIELDS
        )
    )
    missing_fields = [
        field
        for field in required_fact_fields
        if not any(getattr(period, field) is not None for period in periods)
    ]
    if current_shares is None:
        missing_fields.append("current_diluted_shares")

    sources = {"financial_statements": "yfinance"}
    if current_shares is not None:
        sources["current_diluted_shares"] = "yfinance"
    reit_metrics = (
        _normalize_reit_metrics(periods, info, currency, warnings, sources)
        if is_reit
        else {}
    )

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
        reit_metrics=reit_metrics,
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

        optional_frequencies = (
            ("quarterly",)
            if kind == "balance"
            else ("quarterly", "trailing")
        )
        for frequency in optional_frequencies:
            try:
                frames[(kind, frequency)] = _as_frame(getter(freq=frequency))
            except Exception as exc:  # noqa: BLE001 - these variants are optional.
                frames[(kind, frequency)] = pd.DataFrame()
                warnings.append(
                    f"yFinance {frequency} {kind} statement unavailable: {exc}"
                )
        if kind == "balance":
            frames[(kind, "trailing")] = pd.DataFrame()
    return frames


def _read_shares(ticker: Any, warnings: list[str]) -> Any:
    try:
        return ticker.get_shares_full()
    except Exception as exc:  # noqa: BLE001 - statement shares remain available.
        warnings.append(f"yFinance share history unavailable: {exc}")
        return None


def _read_dividends(ticker: Any, warnings: list[str]) -> Any:
    try:
        return ticker.dividends
    except Exception as exc:  # noqa: BLE001 - dividend history is best effort.
        warnings.append(f"yFinance dividend history unavailable: {exc}")
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
    period_end: date,
    *metadata_sources: Mapping[str, Any],
) -> Literal["inside", "outside"] | None:
    for source in metadata_sources:
        outside = _metadata_boolean(
            source,
            period_end,
            "interestPaidOutsideOperatingCashFlow",
            "interest_paid_outside_operating_cash_flow",
        )
        if outside is not None:
            return "outside" if outside else "inside"
        inside = _metadata_boolean(
            source,
            period_end,
            "interestPaidIncludedInOperatingCashFlow",
            "interest_paid_in_operating_cash_flow",
            "interest_included_in_operating_cash_flow",
        )
        if inside is not None:
            return "inside" if inside else "outside"
        for key in _CLASSIFICATION_KEYS:
            value = _text(_metadata_value(source, key, period_end))
            if not value:
                continue
            normalized = value.casefold().replace("_", " ").replace("-", " ")
            if any(word in normalized for word in ("financing", "investing", "outside")):
                return "outside"
            if any(word in normalized for word in ("operating", "inside", "included", "cfo")):
                return "inside"
    return None


def _metadata_boolean(
    source: Mapping[str, Any], period_end: date, *keys: str
) -> bool | None:
    for key in keys:
        value = _metadata_value(source, key, period_end)
        if isinstance(value, bool):
            return value
    return None


def _metadata_value(
    source: Mapping[str, Any], key: str, period_end: date
) -> Any:
    value = source.get(key)
    if not isinstance(value, Mapping):
        return value
    for raw_period, period_value in value.items():
        timestamp = _timestamp(raw_period)
        if timestamp is not None and timestamp.date() == period_end:
            return period_value
    return None


def _is_reit(info: Mapping[str, Any]) -> bool:
    evidence = " ".join(
        value.casefold()
        for value in (
            _text(info.get("quoteType")),
            _text(info.get("industry")),
        )
        if value is not None
    )
    compact = evidence.replace("_", "").replace("-", "").replace(" ", "")
    return any(
        term in evidence or term.replace(" ", "") in compact
        for term in (
            "reit",
            "real estate investment trust",
            "property trust",
        )
    )


def _normalize_reit_periods(
    periods: list[FinancialPeriod],
    dividends: Any,
    shares: Any,
    currency: str,
    info: Mapping[str, Any],
    warnings: list[str],
) -> list[FinancialPeriod]:
    normalized = list(periods)
    observations = _dividend_observations(dividends)
    if observations:
        fiscal_year_end = _fiscal_year_end(info)
        if fiscal_year_end is None:
            warnings.append(
                "REIT dividend history was grouped by calendar year because "
                "fiscal year-end metadata was unavailable."
            )
        annual_totals: dict[date, float] = {}
        for dividend_date, amount in observations:
            period_end = _distribution_period_end(
                dividend_date, fiscal_year_end
            )
            annual_totals[period_end] = (
                annual_totals.get(period_end, 0.0) + amount
            )
        issuer_distribution_buckets = {
            _distribution_period_end(period.period_end, fiscal_year_end)
            for period in normalized
            if not period.is_ttm
            and period.distribution_per_unit is not None
        }
        current_date = _current_date()
        for period_end, amount in annual_totals.items():
            if (
                period_end > current_date
                or period_end in issuer_distribution_buckets
            ):
                continue
            normalized = _add_distribution_period(
                normalized,
                period_end=period_end,
                amount=amount,
                currency=currency,
                is_ttm=False,
                form="dividend_history_annual",
            )

        latest_dividend_date = observations[-1][0]
        window_start = (
            pd.Timestamp(latest_dividend_date) - pd.DateOffset(years=1)
        ).date()
        trailing_amount = sum(
            amount
            for dividend_date, amount in observations
            if window_start < dividend_date <= latest_dividend_date
        )
        if not any(
            period.is_ttm and period.distribution_per_unit is not None
            for period in normalized
        ):
            normalized = _add_distribution_period(
                normalized,
                period_end=latest_dividend_date,
                amount=trailing_amount,
                currency=currency,
                is_ttm=True,
                form="dividend_history_ttm",
            )

    normalized = [
        _add_reit_units(period, shares)
        if period.currency.strip().upper() == currency
        else period
        for period in normalized
    ]
    normalized = [_derive_nav_per_unit(period) for period in normalized]
    return sorted(
        normalized,
        key=lambda period: (period.period_end, period.is_ttm),
    )


def _add_reit_units(period: FinancialPeriod, shares: Any) -> FinancialPeriod:
    if _positive_float(period.diluted_shares) is not None:
        return period
    observation = _units_at_or_before(shares, period.period_end)
    if observation is None:
        return period
    observation_date, units = observation
    sources = dict(period.sources)
    sources["diluted_shares"] = _provenance(
        "get_shares_full",
        observation_date,
        "units",
        "share_history",
    )
    return period.model_copy(
        update={"diluted_shares": units, "sources": sources}
    )


def _units_at_or_before(
    shares: Any, period_end: date
) -> tuple[date, float] | None:
    if not isinstance(shares, pd.Series) or shares.empty:
        return None
    candidates: list[tuple[date, int, float]] = []
    for position, (raw_date, raw_value) in enumerate(shares.items()):
        observation_date = _provider_local_date(raw_date)
        units = _positive_float(raw_value)
        if observation_date is None or units is None:
            continue
        age_days = (period_end - observation_date).days
        if not 0 <= age_days <= _MAX_UNIT_OBSERVATION_AGE_DAYS:
            continue
        candidates.append((observation_date, position, units))
    if not candidates:
        return None
    observation_date, _position, units = max(
        candidates, key=lambda item: (item[0], item[1])
    )
    return observation_date, units


def _dividend_observations(dividends: Any) -> list[tuple[date, float]]:
    if not isinstance(dividends, pd.Series) or dividends.empty:
        return []
    observations: list[tuple[date, float]] = []
    seen: set[tuple[date, float]] = set()
    for raw_date, raw_value in dividends.items():
        local_date = _provider_local_date(raw_date)
        amount = _positive_float(raw_value)
        if local_date is None or amount is None:
            continue
        observation = (local_date, amount)
        if observation in seen:
            continue
        seen.add(observation)
        observations.append(observation)
    return sorted(observations, key=lambda item: item[0])


def _provider_local_date(value: Any) -> date | None:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    return timestamp.date()


def _current_date() -> date:
    return datetime.now(timezone.utc).date()


def _fiscal_year_end(info: Mapping[str, Any]) -> tuple[int, int] | None:
    for key in ("fiscalYearEnd", "lastFiscalYearEnd", "nextFiscalYearEnd"):
        raw_value = info.get(key)
        if isinstance(raw_value, str):
            parts = raw_value.strip().replace("/", "-").split("-")
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                month, day = (int(part) for part in parts)
                if _valid_month_day(month, day):
                    return month, day
        timestamp: pd.Timestamp | None = None
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            try:
                timestamp = pd.Timestamp(raw_value, unit="s")
            except (TypeError, ValueError, OverflowError):
                timestamp = None
        elif raw_value is not None:
            timestamp = _timestamp(raw_value)
        if timestamp is not None:
            return timestamp.month, timestamp.day
    return None


def _valid_month_day(month: int, day: int) -> bool:
    try:
        date(2000, month, day)
    except ValueError:
        return False
    return True


def _distribution_period_end(
    dividend_date: date, fiscal_year_end: tuple[int, int] | None
) -> date:
    if fiscal_year_end is None:
        return date(dividend_date.year, 12, 31)
    month, day = fiscal_year_end
    year = (
        dividend_date.year
        if (dividend_date.month, dividend_date.day) <= fiscal_year_end
        else dividend_date.year + 1
    )
    while day > 0:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    raise AssertionError("validated fiscal year-end could not be constructed")


def _add_distribution_period(
    periods: list[FinancialPeriod],
    *,
    period_end: date,
    amount: float,
    currency: str,
    is_ttm: bool,
    form: str,
) -> list[FinancialPeriod]:
    source = _provenance(
        "Ticker.dividends",
        period_end,
        f"{currency}/unit",
        form,
    )
    updated: list[FinancialPeriod] = []
    matched = False
    for period in periods:
        if period.period_end != period_end or period.is_ttm != is_ttm:
            updated.append(period)
            continue
        matched = True
        if period.distribution_per_unit is not None:
            updated.append(period)
            continue
        sources = dict(period.sources)
        sources["distribution_per_unit"] = source
        updated.append(
            period.model_copy(
                update={
                    "distribution_per_unit": amount,
                    "sources": sources,
                }
            )
        )
    if not matched:
        updated.append(
            FinancialPeriod(
                period_end=period_end,
                fiscal_year=None if is_ttm else period_end.year,
                is_ttm=is_ttm,
                currency=currency,
                distribution_per_unit=amount,
                sources={"distribution_per_unit": source},
            )
        )
    return updated


def _derive_nav_per_unit(period: FinancialPeriod) -> FinancialPeriod:
    if period.nav_per_unit is not None:
        return period
    equity = _positive_float(period.common_equity)
    units = _positive_float(period.diluted_shares)
    equity_source = period.sources.get("common_equity")
    unit_source = period.sources.get("diluted_shares")
    if (
        equity is None
        or units is None
        or not _compatible_period_source(
            equity_source, period, {period.currency.strip().upper()}
        )
        or not _compatible_period_source(
            unit_source, period, {"SHARES", "UNITS"}
        )
    ):
        return period
    sources = dict(period.sources)
    sources["nav_per_unit"] = _provenance(
        "derived_nav_per_unit",
        period.period_end,
        f"{period.currency}/unit",
        "derived",
    )
    return period.model_copy(
        update={"nav_per_unit": equity / units, "sources": sources}
    )


def _compatible_period_source(
    source: FactProvenance | None,
    period: FinancialPeriod,
    accepted_units: set[str],
) -> bool:
    return bool(
        source is not None
        and source.period_end == period.period_end
        and source.unit is not None
        and source.unit.strip().upper() in accepted_units
    )


def _normalize_reit_metrics(
    periods: list[FinancialPeriod],
    info: Mapping[str, Any],
    currency: str,
    warnings: list[str],
    sources: dict[str, str],
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    aliases: dict[str, tuple[tuple[str, ...], bool]] = {
        "aggregate_leverage": (
            ("aggregateLeverage", "aggregate_leverage"),
            True,
        ),
        "interest_coverage": (
            ("interestCoverage", "interest_coverage"),
            False,
        ),
        "occupancy": (("occupancy", "occupancyRate"), True),
        "wale_years": (
            ("waleYears", "weightedAverageLeaseExpiry", "wale"),
            False,
        ),
        "recurring_property_capex": (
            ("recurringPropertyCapex", "recurring_property_capex"),
            False,
        ),
        "material_currency_exposure": (
            ("materialCurrencyExposure", "material_currency_exposure"),
            True,
        ),
    }
    for metric, (provider_keys, is_ratio) in aliases.items():
        for key in provider_keys:
            value = _nonnegative_float(info.get(key))
            if value is None:
                continue
            if is_ratio:
                value = _normalized_ratio(value)
                if value is None:
                    continue
            metrics[metric] = value
            sources[metric] = f"yfinance_info.{key}"
            break

    if "aggregate_leverage" not in metrics:
        for period in sorted(
            periods,
            key=lambda candidate: (candidate.period_end, candidate.is_ttm),
            reverse=True,
        ):
            assets = _positive_float(period.total_assets)
            debt = _nonnegative_float(period.total_debt)
            if (
                period.currency.strip().upper() != currency
                or assets is None
                or debt is None
                or not _compatible_period_source(
                    period.sources.get("total_assets"), period, {currency}
                )
                or not _compatible_period_source(
                    period.sources.get("total_debt"), period, {currency}
                )
            ):
                continue
            leverage = debt / assets
            if not math.isfinite(leverage):
                continue
            metrics["aggregate_leverage"] = leverage
            sources["aggregate_leverage"] = "derived_aggregate_leverage"
            warnings.append(
                "REIT derived aggregate leverage from same-period total "
                "debt and total assets; no provider-reported aggregate "
                "leverage was available."
            )
            break
    return metrics


def _normalized_ratio(value: float) -> float | None:
    if value > 1.0:
        if value > 100.0:
            return None
        value /= 100.0
    return value


def _build_annual_periods(
    frames: Mapping[tuple[StatementKind, str], pd.DataFrame],
    currency: str,
    info: Mapping[str, Any],
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
            _interest_classification(
                period_end,
                yearly["cashflow"].attrs,
                info,
            ),
            is_ttm=False,
            form="yearly",
        )
        for period_end in period_ends
    ]


def _build_trailing_period(
    frames: Mapping[tuple[StatementKind, str], pd.DataFrame],
    currency: str,
    info: Mapping[str, Any],
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
    direct_period_end = max(direct_dates) if direct_dates else None
    quarterly_period_end = quarter_dates[-1] if len(quarter_dates) >= 4 else None
    if direct_period_end is None and quarterly_period_end is None:
        return None

    use_direct = direct_period_end is not None and (
        quarterly_period_end is None or direct_period_end >= quarterly_period_end
    )
    period_end = direct_period_end if use_direct else quarterly_period_end
    assert period_end is not None
    base_frames = (
        trailing
        if use_direct
        else {kind: pd.DataFrame() for kind in ("cashflow", "income", "balance")}
    )
    direct_interest_classification = (
        _interest_classification(
            period_end,
            trailing["cashflow"].attrs,
            info,
        )
        if use_direct
        else None
    )
    direct = _build_period(
        period_end,
        currency,
        base_frames,
        direct_interest_classification,
        is_ttm=True,
        form="trailing",
    )
    if quarterly_period_end != period_end:
        return direct

    latest_quarters = quarter_dates[-4:]
    updates: dict[str, Any] = {}
    sources = dict(direct.sources)
    for field, (kind, aliases) in _FIELD_ALIASES.items():
        if getattr(direct, field) is not None:
            continue
        if field in _ADDITIVE_FIELDS:
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
            _unit_for_field(field, currency),
            "quarterly_ttm",
        )

    if direct.interest_paid_outside_operating is None:
        quarterly_interest_classification = _interest_classification(
            latest_quarters[-1],
            quarterly["cashflow"].attrs,
            info,
        )
        interest = _quarterly_interest(
            quarterly["cashflow"],
            latest_quarters,
            quarterly_interest_classification,
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
        sources[field] = _provenance(
            concept,
            period_end,
            _unit_for_field(field, currency),
            form,
        )

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
        normalized_alias = _normalized_label(alias)
        for row_position, row_name in enumerate(frame.index):
            if _normalized_label(row_name) != normalized_alias:
                continue
            value = _finite_float(frame.iloc[row_position, column])
            if value is not None:
                return value, str(row_name)
    return None


def _normalized_label(value: Any) -> str:
    return "".join(character.casefold() for character in str(value) if character.isalnum())


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
    concept: str, period_end: date, unit: str, form: str
) -> FactProvenance:
    return FactProvenance(
        provider="yfinance",
        concept=concept,
        form=form,
        period_end=period_end,
        unit=unit,
    )


def _unit_for_field(field: str, currency: str) -> str:
    if field == "diluted_shares":
        return "shares"
    if field in {"distribution_per_unit", "nav_per_unit"}:
        return f"{currency}/unit"
    return currency


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
    return _current_shares_metadata(info, fast_info)


def _current_reit_shares(
    periods: list[FinancialPeriod],
    info: Mapping[str, Any],
    fast_info: Mapping[str, Any],
) -> float | None:
    candidates = [
        period
        for period in periods
        if _positive_float(period.diluted_shares) is not None
    ]
    if candidates:
        latest = max(
            candidates,
            key=lambda period: (period.period_end, period.is_ttm),
        )
        return _positive_float(latest.diluted_shares)
    return _current_shares_metadata(info, fast_info)


def _current_shares_metadata(
    info: Mapping[str, Any], fast_info: Mapping[str, Any]
) -> float | None:
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


def _nonnegative_float(value: Any) -> float | None:
    number = _finite_float(value)
    return number if number is not None and number >= 0 else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
