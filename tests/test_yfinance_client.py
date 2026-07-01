import pandas as pd

from app.services import yfinance_client


def test_get_stock_snapshot_uses_fast_info_when_full_info_fails(monkeypatch):
    monkeypatch.setattr(yfinance_client.yf, "Ticker", lambda symbol: FakeTicker(symbol))

    snapshot = yfinance_client.get_stock_snapshot("aapl")

    assert snapshot.symbol == "AAPL"
    assert snapshot.quote.current_price == 296.33
    assert snapshot.quote.currency == "USD"
    assert snapshot.quote.market_cap == 4_352_304_006_262
    assert snapshot.quote.shares_outstanding == 14_687_356_000
    assert "Full yFinance quote info is unavailable for AAPL" in snapshot.warnings[0]


def test_get_stock_quote_does_not_call_full_info(monkeypatch):
    monkeypatch.setattr(yfinance_client.yf, "Ticker", lambda symbol: FakeTicker(symbol))

    quote = yfinance_client.get_stock_quote("d05.si")

    assert quote.symbol == "D05.SI"
    assert quote.current_price == 296.33
    assert quote.currency == "USD"
    assert quote.warnings == []


class FakeTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol

    @property
    def info(self):
        raise RuntimeError("Yahoo quoteSummary rejected the request")

    @property
    def fast_info(self):
        return {
            "lastPrice": 296.33,
            "currency": "USD",
            "market_cap": 4_352_304_006_262,
            "shares": 14_687_356_000,
        }

    @property
    def cashflow(self):
        return pd.DataFrame()

    @property
    def balance_sheet(self):
        return pd.DataFrame()

    @property
    def financials(self):
        return pd.DataFrame()

    def history(self, period: str):
        return pd.DataFrame({"Close": [295.43, 296.33]})
