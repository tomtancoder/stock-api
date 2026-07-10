import pytest

from app.services.valuation_math import classify_price, validate_scenarios


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
