import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PriceClassification:
    status: str
    margin_of_safety_price: float
    price_to_base_value: float
    upside_downside_percent: float


def _positive_finite(value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("valuation values must be finite and positive")
    return number


def validate_scenarios(bear: float, base: float, bull: float) -> None:
    low = _positive_finite(bear)
    middle = _positive_finite(base)
    high = _positive_finite(bull)
    if not low <= middle <= high:
        raise ValueError("valuation scenarios must satisfy bear <= base <= bull")


def classify_price(
    current_price: float,
    *,
    bear: float,
    base: float,
    bull: float,
    margin_of_safety: float = 0.25,
) -> PriceClassification:
    price = _positive_finite(current_price)
    validate_scenarios(bear, base, bull)
    if not 0 <= margin_of_safety < 1:
        raise ValueError("margin_of_safety must be between 0 and 1")
    margin_price = base * (1 - margin_of_safety)
    if price > bull:
        status = "very_expensive"
    elif price > base * 1.10:
        status = "expensive"
    elif price <= margin_price:
        status = "cheap"
    else:
        status = "fair"
    return PriceClassification(
        status=status,
        margin_of_safety_price=round(margin_price, 4),
        price_to_base_value=round(price / base, 4),
        upside_downside_percent=round((base - price) / price * 100, 2),
    )
