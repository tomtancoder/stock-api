from fastapi.testclient import TestClient

from app.main import app
from app.schemas import FinancialMetrics, QuoteResponse, StockSnapshot
from app.services import yfinance_client

client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_points_to_docs_and_health():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "Stock Valuation API",
        "docs": "/docs",
        "health": "/health",
    }


def test_valuation_endpoint_uses_mocked_yfinance_snapshot(monkeypatch):
    monkeypatch.setattr(yfinance_client, "get_stock_snapshot", lambda symbol: _snapshot(symbol))

    response = client.post(
        "/api/v1/stocks/ACME/valuation",
        json={
            "discount_rate": 0.10,
            "terminal_growth_rate": 0.02,
            "projection_years": 5,
            "margin_of_safety": 0.25,
            "growth_rate": 0.03,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "ACME"
    assert payload["currency"] == "USD"
    assert payload["intrinsic_value_per_share"] == 130.5
    assert payload["ratios"]["trailing_pe"] == 20


def test_fundamentals_endpoint_returns_data_quality_warnings(monkeypatch):
    snapshot = _snapshot("ACME")
    snapshot.warnings.append("Currency is missing; amounts may not be comparable across markets.")
    monkeypatch.setattr(yfinance_client, "get_stock_snapshot", lambda symbol: snapshot)

    response = client.get("/api/v1/stocks/ACME/fundamentals")

    assert response.status_code == 200
    assert response.json()["warnings"] == [
        "Currency is missing; amounts may not be comparable across markets."
    ]


def _snapshot(symbol: str) -> StockSnapshot:
    return StockSnapshot(
        symbol=symbol.upper(),
        quote=QuoteResponse(
            symbol=symbol.upper(),
            currency="USD",
            current_price=80,
            market_cap=8_000,
            shares_outstanding=100,
            trailing_pe=20,
            forward_pe=18,
            price_to_book=4,
            enterprise_to_ebitda=12,
            dividend_yield=0.01,
        ),
        financials=FinancialMetrics(
            revenue=5_000,
            net_income=700,
            free_cash_flow=1_000,
            total_debt=500,
            cash_and_equivalents=250,
        ),
    )
