import math
import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.schemas import BankValuationDetails, ValuationResponse
from app.services import sec_companyfacts
from app.services import valuation_fundamentals
from app.services import valuation_service


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_VALUATION_TESTS") != "1",
        reason=(
            "Set RUN_LIVE_VALUATION_TESTS=1 to call live valuation providers."
        ),
    ),
]


@pytest.fixture(autouse=True)
def isolate_valuation_provider_state():
    get_settings.cache_clear()
    valuation_service._clear_valuation_caches()
    valuation_fundamentals._clear_cache()
    sec_companyfacts._clear_cache()
    yield
    valuation_service._clear_valuation_caches()
    valuation_fundamentals._clear_cache()
    sec_companyfacts._clear_cache()
    get_settings.cache_clear()


def _assert_live_ordinary_company_valuation(
    client: TestClient,
    path: str,
    *,
    currency: str,
    expected_primary_sources: frozenset[str],
) -> None:
    response = client.get(path)

    assert response.status_code == 200, response.text
    valuation = ValuationResponse.model_validate(response.json())
    assert valuation.currency == currency
    primary_source = valuation.data_quality.primary_source
    assert primary_source in expected_primary_sources

    intrinsic_value = valuation.intrinsic_value
    assert intrinsic_value is not None
    scenario_values = (
        intrinsic_value.bear,
        intrinsic_value.base,
        intrinsic_value.bull,
    )
    assert all(math.isfinite(value) and value > 0 for value in scenario_values)
    assert scenario_values[0] <= scenario_values[1] <= scenario_values[2]

    required_financial_source_fields = {
        "operating_cash_flow",
        "capital_expenditure",
        "stock_based_compensation",
        "interest_paid_outside_operating",
        "diluted_shares",
    }
    assert required_financial_source_fields <= valuation.sources.keys()
    allowed_financial_sources = {
        "sec_companyfacts": {"sec_companyfacts", "yfinance"},
        "yfinance_fallback": {"yfinance"},
        "yfinance_sgx": {"yfinance"},
    }[primary_source]
    assert {
        valuation.sources[field]
        for field in required_financial_source_fields
    } <= allowed_financial_sources
    assert valuation.sources.get("current_price") == "existing_quote_provider"


@pytest.mark.parametrize(
    ("path", "currency", "expected_primary_sources"),
    [
        (
            "/api/v1/markets/NASDAQ/AAPL/valuation",
            "USD",
            frozenset({"sec_companyfacts", "yfinance_fallback"}),
        ),
        (
            "/api/v1/markets/SGX/S63/valuation",
            "SGD",
            frozenset({"yfinance_sgx"}),
        ),
    ],
    ids=("us-aapl", "sgx-s63"),
)
def test_live_ordinary_company_valuation(
    path: str,
    currency: str,
    expected_primary_sources: frozenset[str],
) -> None:
    with TestClient(app) as client:
        _assert_live_ordinary_company_valuation(
            client,
            path,
            currency=currency,
            expected_primary_sources=expected_primary_sources,
        )


def test_live_sgx_bank_valuation() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/markets/SGX/D05/valuation")

    assert response.status_code == 200, response.text
    valuation = ValuationResponse.model_validate(response.json())
    assert valuation.symbol == "SGX:D05"
    assert valuation.currency == "SGD"
    assert valuation.detected_company_type == "bank"
    assert valuation.method == "bank_residual_income"
    assert valuation.confidence in {"medium", "low"}
    assert valuation.data_quality.primary_source == "yfinance_sgx"
    assert isinstance(valuation.model_details, BankValuationDetails)

    intrinsic_value = valuation.intrinsic_value
    assert intrinsic_value is not None
    scenario_values = (
        intrinsic_value.bear,
        intrinsic_value.base,
        intrinsic_value.bull,
    )
    assert all(math.isfinite(value) and value > 0 for value in scenario_values)
    assert scenario_values[0] <= scenario_values[1] <= scenario_values[2]

    required_bank_source_fields = {
        "common_equity",
        "net_income_common",
        "common_dividends",
        "diluted_shares",
    }
    assert required_bank_source_fields <= valuation.sources.keys()
    assert all(
        valuation.sources[field] == "yfinance"
        for field in required_bank_source_fields
    )
    assert valuation.sources.get("current_price") == "existing_quote_provider"
