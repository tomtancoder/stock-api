import os

import pytest
import yfinance as yf
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import FinancialMetrics, QuoteResponse, StockSnapshot
from app.services import yfinance_client
from app.ticker_universe import (
    SAMPLE_100_TICKERS,
    SAMPLE_600_TICKERS,
    SINGAPORE_TICKERS,
    US_TICKERS,
)

client = TestClient(app)


def test_sample_universe_has_600_us_and_singapore_tickers():
    assert len(US_TICKERS) == 300
    assert len(SINGAPORE_TICKERS) == 300
    assert len(SAMPLE_100_TICKERS) == 100
    assert len(SAMPLE_600_TICKERS) == 600
    assert len(set(SAMPLE_600_TICKERS)) == 600
    assert SAMPLE_100_TICKERS == [*US_TICKERS[:50], *SINGAPORE_TICKERS[:50]]
    assert all("." not in symbol for symbol in US_TICKERS)
    assert all(symbol.endswith(".SI") for symbol in SINGAPORE_TICKERS)


@pytest.mark.parametrize("symbol", SAMPLE_600_TICKERS)
def test_quote_endpoint_accepts_sample_ticker_universe(monkeypatch, symbol):
    monkeypatch.setattr(yfinance_client, "get_stock_snapshot", lambda value: _snapshot(value))

    response = client.get(f"/api/v1/stocks/{symbol}/quote")

    assert response.status_code == 200
    assert response.json()["symbol"] == symbol.upper()


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_YFINANCE_TESTS") != "1",
    reason="Set RUN_LIVE_YFINANCE_TESTS=1 to call live yFinance for 600 tickers.",
)
def test_live_yfinance_can_fetch_sample_ticker_universe_prices():
    data = yf.download(
        tickers=" ".join(SAMPLE_600_TICKERS),
        period="5d",
        interval="1d",
        group_by="ticker",
        progress=False,
        threads=True,
        auto_adjust=False,
    )
    missing = []

    for symbol in SAMPLE_600_TICKERS:
        try:
            close_prices = data[symbol]["Close"].dropna()
        except Exception:
            missing.append(symbol)
            continue

        if close_prices.empty:
            missing.append(symbol)

    assert missing == []


def _snapshot(symbol: str) -> StockSnapshot:
    return StockSnapshot(
        symbol=symbol.upper(),
        quote=QuoteResponse(
            symbol=symbol.upper(),
            currency="SGD" if symbol.upper().endswith(".SI") else "USD",
            current_price=100,
            market_cap=10_000,
            shares_outstanding=100,
        ),
        financials=FinancialMetrics(free_cash_flow=1_000),
    )
