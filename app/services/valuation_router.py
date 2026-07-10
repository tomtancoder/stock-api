from __future__ import annotations

from dataclasses import dataclass

from app.services.owner_earnings_valuation import value_owner_earnings
from app.services.valuation_types import (
    FinancialPeriod,
    ModelResult,
    ValuationFundamentals,
)


@dataclass(frozen=True, slots=True)
class CompanyClassification:
    company_type: str
    supported: bool
    sources: tuple[str, ...]
    reasons: tuple[str, ...]


class ValuationUnreliable(RuntimeError):
    def __init__(self, reasons: list[str] | tuple[str, ...]) -> None:
        self.reasons = list(reasons) or [
            "Valuation model selection is unreliable."
        ]
        super().__init__("; ".join(self.reasons))


_REIT_TERMS = (
    "reit",
    "real estate investment trust",
    "property trust",
)
_BANK_TERMS = (
    "bank",
    "banking",
    "depository institution",
    "depositary institution",
    "savings institution",
    "savings and loan",
    "thrift",
)
_INSURANCE_TERMS = ("insurance", "insurer")
_UNSUPPORTED_SECURITY_TERMS = (
    "commodity",
    "crypto",
    "currency",
    "etf",
    "exchange traded fund",
    "future",
    "index",
    "mutual fund",
    "mutualfund",
)
_GENERIC_EQUITY_TERMS = ("common stock", "equity", "stock")
_BROAD_FINANCIAL_SECTORS = ("financial", "financial services")


def classify_company(
    fundamentals: ValuationFundamentals,
) -> CompanyClassification:
    metadata = _metadata(fundamentals)
    security_metadata = tuple(
        item for item in metadata if item[0] == "provider_security_type"
    )
    unsupported_security_sources = _matching_sources(
        security_metadata, _UNSUPPORTED_SECURITY_TERMS
    )
    if unsupported_security_sources:
        return CompanyClassification(
            company_type="unsupported",
            supported=False,
            sources=unsupported_security_sources,
            reasons=("The security type has no approved valuation model.",),
        )

    reit_sources = _matching_sources(metadata, _REIT_TERMS)
    if reit_sources:
        return CompanyClassification(
            company_type="reit",
            supported=False,
            sources=reit_sources,
            reasons=("REIT valuation is recognized but not supported yet.",),
        )

    bank_metadata = tuple(
        item
        for item in metadata
        if item[0] in {"industry", "issuer_classification"}
    )
    bank_sources = _matching_sources(bank_metadata, _BANK_TERMS)
    insurance_sources = _matching_sources(metadata, _INSURANCE_TERMS)
    ordinary_conflict_sources = _ordinary_conflict_sources(metadata)

    if bank_sources and insurance_sources:
        return CompanyClassification(
            company_type="ambiguous",
            supported=False,
            sources=_unique((*bank_sources, *insurance_sources)),
            reasons=("Bank and insurance metadata conflict.",),
        )

    if insurance_sources:
        return CompanyClassification(
            company_type="unsupported",
            supported=False,
            sources=insurance_sources,
            reasons=("Insurance companies have no approved valuation model.",),
        )

    bank_statements = _has_bank_like_statements(fundamentals)
    if bank_sources:
        if ordinary_conflict_sources:
            return CompanyClassification(
                company_type="ambiguous",
                supported=False,
                sources=_unique((*ordinary_conflict_sources, *bank_sources)),
                reasons=("Bank and operating-company metadata conflict.",),
            )
        if not bank_statements:
            return CompanyClassification(
                company_type="ambiguous",
                supported=False,
                sources=bank_sources,
                reasons=(
                    "Bank industry metadata lacks compatible bank-like "
                    "financial statements.",
                ),
            )
        return CompanyClassification(
            company_type="bank",
            supported=False,
            sources=_unique((*bank_sources, "statement_structure")),
            reasons=("Bank valuation is recognized but not supported yet.",),
        )

    if not _has_compatible_operating_statements(fundamentals):
        sources = _ordinary_metadata_sources(metadata)
        return CompanyClassification(
            company_type="unsupported",
            supported=False,
            sources=sources,
            reasons=(
                "Ordinary-company routing requires compatible cash-flow "
                "and revenue facts.",
            ),
        )

    return CompanyClassification(
        company_type="operating_company",
        supported=True,
        sources=_unique(
            (*_ordinary_metadata_sources(metadata), "statement_structure")
        ),
        reasons=(),
    )


def route_valuation(fundamentals: ValuationFundamentals) -> ModelResult:
    classification = classify_company(fundamentals)
    if not classification.supported or classification.company_type != (
        "operating_company"
    ):
        raise ValuationUnreliable(classification.reasons)
    try:
        return value_owner_earnings(fundamentals)
    except ValueError as exc:
        raise ValuationUnreliable([str(exc)]) from exc


def _metadata(
    fundamentals: ValuationFundamentals,
) -> tuple[tuple[str, str], ...]:
    values = (
        ("provider_security_type", fundamentals.provider_security_type),
        ("sector", fundamentals.sector),
        ("industry", fundamentals.industry),
        ("issuer_classification", fundamentals.issuer_classification),
    )
    return tuple(
        (source, value.strip().casefold())
        for source, value in values
        if value is not None and value.strip()
    )


def _matching_sources(
    metadata: tuple[tuple[str, str], ...], terms: tuple[str, ...]
) -> tuple[str, ...]:
    return tuple(
        source
        for source, value in metadata
        if any(term in value for term in terms)
    )


def _ordinary_conflict_sources(
    metadata: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    sources: list[str] = []
    for source, value in metadata:
        if source == "sector" and not any(
            term in value for term in (*_BROAD_FINANCIAL_SECTORS, "real estate")
        ):
            sources.append(source)
    return _unique(sources)


def _ordinary_metadata_sources(
    metadata: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    sources: list[str] = []
    for source, value in metadata:
        if source == "provider_security_type" and any(
            term in value for term in _GENERIC_EQUITY_TERMS
        ):
            sources.append(source)
        elif source == "sector" and not any(
            term in value for term in _BROAD_FINANCIAL_SECTORS
        ):
            sources.append(source)
        elif source in {"industry", "issuer_classification"} and not any(
            term in value
            for term in (*_REIT_TERMS, *_BANK_TERMS, *_INSURANCE_TERMS)
        ):
            sources.append(source)
    return _unique(sources)


def _has_compatible_operating_statements(
    fundamentals: ValuationFundamentals,
) -> bool:
    currency = fundamentals.currency.strip().upper()
    return any(
        _period_has_compatible_facts(
            period,
            currency,
            ("operating_cash_flow", "revenue"),
        )
        for period in fundamentals.periods
    )


def _has_bank_like_statements(fundamentals: ValuationFundamentals) -> bool:
    currency = fundamentals.currency.strip().upper()
    return any(
        _period_has_compatible_facts(
            period,
            currency,
            ("net_income_common", "common_equity", "total_assets"),
        )
        for period in fundamentals.periods
    )


def _period_has_compatible_facts(
    period: FinancialPeriod,
    currency: str,
    fields: tuple[str, ...],
) -> bool:
    if period.currency.strip().upper() != currency:
        return False
    for field in fields:
        if getattr(period, field) is None:
            return False
        provenance = period.sources.get(field)
        if provenance is None:
            continue
        if provenance.period_end not in {None, period.period_end}:
            return False
        if (
            provenance.unit is not None
            and provenance.unit.strip().upper() != currency
        ):
            return False
    return True


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
