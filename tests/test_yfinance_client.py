import pandas as pd
import pytest

from app.services import yfinance_client


@pytest.fixture(autouse=True)
def clear_yfinance_caches():
    yfinance_client._quote_cache.clear()
    yfinance_client._snapshot_cache.clear()


def test_get_stock_snapshot_uses_fast_info_when_full_info_fails(monkeypatch):
    monkeypatch.setattr(yfinance_client.yf, "Ticker", lambda symbol: FastOnlyTicker(symbol))

    snapshot = yfinance_client.get_stock_snapshot("aapl")

    assert snapshot.symbol == "AAPL"
    assert snapshot.quote.current_price == 296.33
    assert snapshot.quote.currency == "USD"
    assert snapshot.quote.market_cap == 4_352_304_006_262
    assert snapshot.quote.shares_outstanding == 14_687_356_000
    assert "Full yFinance quote info is unavailable for AAPL" in snapshot.warnings[0]


def test_get_stock_quote_returns_fast_fields_when_full_info_fails(monkeypatch):
    monkeypatch.setattr(yfinance_client.yf, "Ticker", lambda symbol: FastOnlyTicker(symbol))

    quote = yfinance_client.get_stock_quote("d05.si")

    assert quote.symbol == "D05.SI"
    assert quote.current_price == 296.33
    assert quote.previous_close == 295.43
    assert quote.price_change == 0.9
    assert quote.price_change_percent == 0.3046
    assert quote.currency == "USD"
    assert quote.volume == 7_500_000
    assert quote.average_volume == 8_200_000
    assert quote.market_cap == 4_352_304_006_262
    assert quote.day_high == 298
    assert quote.day_low == 294
    assert quote.fifty_two_week_high == 320
    assert quote.fifty_two_week_low == 180
    assert quote.trailing_pe is None
    assert "Full yFinance quote info is unavailable for D05.SI" in quote.warnings[0]


def test_get_stock_quote_enriches_company_name_and_ratios_from_full_info(monkeypatch):
    monkeypatch.setattr(yfinance_client.yf, "Ticker", lambda symbol: EnrichedTicker(symbol))

    quote = yfinance_client.get_stock_quote("tsla")

    assert quote.symbol == "TSLA"
    assert quote.short_name == "TSLA Inc."
    assert quote.long_name == "Tesla, Inc."
    assert quote.exchange == "NMS"
    assert quote.current_price == 428.11
    assert quote.previous_close == 423.19
    assert quote.price_change == 4.92
    assert quote.price_change_percent == 1.1626
    assert quote.volume == 44_848_440
    assert quote.market_cap == 1_610_000_000_000
    assert quote.trailing_pe == 383.16
    assert quote.forward_pe == 169.98
    assert quote.price_to_book == 20.5
    assert quote.dividend_yield == 0.0
    assert quote.warnings == []


class FastOnlyTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol

    @property
    def info(self):
        raise RuntimeError("Yahoo quoteSummary rejected the request")

    @property
    def fast_info(self):
        return {
            "lastPrice": 296.33,
            "regularMarketPreviousClose": 295.43,
            "currency": "USD",
            "exchange": "NMS",
            "lastVolume": 7_500_000,
            "threeMonthAverageVolume": 8_200_000,
            "marketCap": 4_352_304_006_262,
            "shares": 14_687_356_000,
            "dayHigh": 298,
            "dayLow": 294,
            "yearHigh": 320,
            "yearLow": 180,
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
        return pd.DataFrame(
            {
                "High": [297, 298],
                "Low": [293, 294],
                "Close": [295.43, 296.33],
                "Volume": [7_000_000, 7_500_000],
            }
        )


class EnrichedTicker(FastOnlyTicker):
    @property
    def info(self):
        return {
            "shortName": "TSLA Inc.",
            "longName": "Tesla, Inc.",
            "exchange": "NMS",
            "financialCurrency": "USD",
            "currentPrice": 428.11,
            "regularMarketPreviousClose": 423.19,
            "regularMarketVolume": 44_848_440,
            "averageVolume": 55_668_630,
            "marketCap": 1_610_000_000_000,
            "sharesOutstanding": 3_755_723_871,
            "dayHigh": 432.35,
            "dayLow": 425.07,
            "fiftyTwoWeekHigh": 498.83,
            "fiftyTwoWeekLow": 288.77,
            "trailingPE": 383.16,
            "forwardPE": 169.98,
            "priceToBook": 20.5,
            "dividendYield": 0.0,
        }
