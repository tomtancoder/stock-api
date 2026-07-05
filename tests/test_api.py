from fastapi.testclient import TestClient

from app.api.v1 import stocks
from app.main import app
from app.schemas import EmaValues, FinancialMetrics, QuoteResponse, StockSnapshot, TechnicalsResponse
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


def test_quote_endpoint_uses_lightweight_quote_fetch(monkeypatch):
    monkeypatch.setattr(
        yfinance_client,
        "get_stock_quote",
        lambda symbol: QuoteResponse(
            symbol=symbol.upper(),
            currency="SGD",
            current_price=65.43,
            warnings=[],
        ),
    )

    response = client.get("/api/v1/stocks/D05.SI/quote")

    assert response.status_code == 200
    assert response.json()["symbol"] == "D05.SI"
    assert response.json()["warnings"] == []


def test_technicals_endpoint_returns_ema_values(monkeypatch):
    monkeypatch.setattr(
        stocks,
        "get_stock_technicals",
        lambda symbol, period, interval: TechnicalsResponse(
            symbol=symbol.upper(),
            period=period,
            interval=interval,
            as_of="2026-07-02T00:00:00",
            latest_close=410.72,
            ema=EmaValues(
                ema_21=404.1296,
                ema_50=403.9709,
                ema_100=404.1072,
                ema_200=398.3258,
            ),
            warnings=[],
        ),
    )

    response = client.get("/api/v1/stocks/TSLA/technicals")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "TSLA"
    assert payload["period"] == "1y"
    assert payload["interval"] == "1d"
    assert payload["ema"]["ema_21"] == 404.1296
    assert payload["ema"]["ema_200"] == 398.3258


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


def test_valuation_openapi_example_is_valid_for_execute(monkeypatch):
    monkeypatch.setattr(yfinance_client, "get_stock_snapshot", lambda symbol: _snapshot(symbol))
    example = app.openapi()["components"]["schemas"]["ValuationRequest"]["examples"][0]

    response = client.post("/api/v1/stocks/ACME/valuation", json=example)

    assert response.status_code == 200
    assert response.json()["assumptions"] == example


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
