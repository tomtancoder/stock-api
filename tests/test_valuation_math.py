from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import (
    IntrinsicValueRange,
    OwnerEarningsHistoryEntry,
    OwnerEarningsValuationDetails,
    ValuationResponse,
)
from app.services.valuation_math import classify_price, validate_scenarios


def _intrinsic_value_payload(**overrides):
    payload = {
        "bear": 80.0,
        "base": 100.0,
        "bull": 130.0,
        "margin_of_safety_price": 75.0,
        "price_to_base_value": 0.9,
        "upside_downside_percent": 11.11,
    }
    payload.update(overrides)
    return payload


def _owner_earnings_details_payload():
    return {
        "method": "owner_earnings_dcf",
        "normalized_owner_earnings": 1_000.0,
        "owner_earnings_per_share": 5.0,
        "maintenance_capex_method": "depreciation_proxy",
        "annual_history": [],
        "derived_growth": 0.05,
        "usable_years": 5,
    }


def _owner_earnings_history_payload(**overrides):
    payload = {
        "period_end": date(2025, 12, 31),
        "currency": "USD",
        "operating_cash_flow": 1_000.0,
        "maintenance_capex": 200.0,
        "maintenance_capex_method": "depreciation_proxy",
        "stock_based_compensation": 50.0,
        "interest_paid_outside_operating": 0.0,
        "owner_earnings": 750.0,
    }
    payload.update(overrides)
    return payload


def _valuation_response_payload(status="fair"):
    reliable = status != "valuation_unreliable"
    return {
        "symbol": "NASDAQ:ACME",
        "exchange": "NASDAQ",
        "currency": "USD",
        "detected_company_type": "operating_company",
        "method": "owner_earnings_dcf" if reliable else None,
        "classification_sources": ["provider_industry"],
        "status": status,
        "confidence": "medium" if reliable else None,
        "current_price": 90.0,
        "price_as_of": datetime(2026, 7, 10, tzinfo=timezone.utc),
        "intrinsic_value": _intrinsic_value_payload() if reliable else None,
        "model_details": _owner_earnings_details_payload() if reliable else None,
        "quality": {"eligible": reliable},
        "data_quality": {
            "primary_source": "sec_companyfacts",
            "valuation_as_of": datetime(2026, 7, 10, tzinfo=timezone.utc),
        },
    }


def test_price_classification_uses_approved_precedence():
    assert classify_price(70, bear=80, base=100, bull=130).status == "cheap"
    assert classify_price(75, bear=80, base=100, bull=130).status == "cheap"
    assert classify_price(100, bear=80, base=100, bull=130).status == "fair"
    assert classify_price(111, bear=80, base=100, bull=130).status == "expensive"
    assert classify_price(131, bear=80, base=100, bull=130).status == "very_expensive"


def test_price_classification_reports_ratios():
    result = classify_price(80, bear=70, base=100, bull=120)
    assert result.margin_of_safety_price == 75.0
    assert result.price_to_base_value == 0.8
    assert result.upside_downside_percent == 25.0


@pytest.mark.parametrize(
    "values",
    [(100, 90, 120), (0, 100, 120), (80, 100, float("inf"))],
)
def test_validate_scenarios_rejects_invalid_ranges(values):
    with pytest.raises(ValueError):
        validate_scenarios(*values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bear", 0),
        ("base", -1),
        ("bull", float("inf")),
        ("bear", float("nan")),
    ],
)
def test_intrinsic_value_range_rejects_invalid_scenarios(field, value):
    with pytest.raises(ValidationError):
        IntrinsicValueRange(**_intrinsic_value_payload(**{field: value}))


@pytest.mark.parametrize(
    "scenarios",
    [
        {"bear": 101, "base": 100, "bull": 130},
        {"bear": 80, "base": 131, "bull": 130},
    ],
)
def test_intrinsic_value_range_rejects_unordered_scenarios(scenarios):
    with pytest.raises(ValidationError):
        IntrinsicValueRange(**_intrinsic_value_payload(**scenarios))


def test_intrinsic_value_range_accepts_equal_ordered_scenarios():
    result = IntrinsicValueRange(
        **_intrinsic_value_payload(bear=100, base=100, bull=100)
    )

    assert (result.bear, result.base, result.bull) == (100, 100, 100)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("method", "owner_earnings_dcf"),
        ("confidence", "low"),
        ("intrinsic_value", _intrinsic_value_payload()),
        ("model_details", _owner_earnings_details_payload()),
    ],
)
def test_unreliable_response_rejects_unsupported_claims(field, value):
    payload = _valuation_response_payload("valuation_unreliable")
    payload[field] = value

    with pytest.raises(ValidationError):
        ValuationResponse(**payload)


@pytest.mark.parametrize(
    "field",
    ["method", "confidence", "intrinsic_value", "model_details"],
)
def test_reliable_response_requires_supported_claims(field):
    payload = _valuation_response_payload()
    payload[field] = None

    with pytest.raises(ValidationError):
        ValuationResponse(**payload)


def test_reliable_and_unreliable_response_relationships_accept_valid_payloads():
    reliable = ValuationResponse(**_valuation_response_payload())
    unreliable = ValuationResponse(
        **_valuation_response_payload("valuation_unreliable")
    )

    assert reliable.method == "owner_earnings_dcf"
    assert unreliable.method is None


@pytest.mark.parametrize("field", ["currency", "current_price", "price_as_of"])
def test_valuation_response_requires_quote_fields(field):
    payload = _valuation_response_payload()
    payload.pop(field)

    with pytest.raises(ValidationError):
        ValuationResponse(**payload)


@pytest.mark.parametrize("current_price", [0, -1, float("inf"), float("nan")])
def test_valuation_response_rejects_invalid_current_price(current_price):
    payload = _valuation_response_payload()
    payload["current_price"] = current_price

    with pytest.raises(ValidationError):
        ValuationResponse(**payload)


def test_owner_earnings_details_parse_typed_annual_history():
    payload = _owner_earnings_details_payload()
    payload["annual_history"] = [_owner_earnings_history_payload()]

    details = OwnerEarningsValuationDetails(**payload)

    assert isinstance(details.annual_history[0], OwnerEarningsHistoryEntry)
    assert details.annual_history[0].owner_earnings == 750.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operating_cash_flow", float("inf")),
        ("maintenance_capex", float("nan")),
        ("stock_based_compensation", float("inf")),
        ("interest_paid_outside_operating", float("inf")),
        ("owner_earnings", float("inf")),
    ],
)
def test_owner_earnings_history_rejects_non_finite_values(field, value):
    with pytest.raises(ValidationError):
        OwnerEarningsHistoryEntry(
            **_owner_earnings_history_payload(**{field: value})
        )


@pytest.mark.parametrize(
    "field",
    [
        "maintenance_capex",
        "stock_based_compensation",
        "interest_paid_outside_operating",
    ],
)
def test_owner_earnings_history_rejects_negative_deductions(field):
    with pytest.raises(ValidationError):
        OwnerEarningsHistoryEntry(
            **_owner_earnings_history_payload(**{field: -0.01})
        )
