from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def stub_valuation_metadata(monkeypatch):
    from app.services import yfinance_analysis

    monkeypatch.setattr(
        yfinance_analysis,
        "get_valuation_metadata",
        lambda symbol: {},
        raising=False,
    )


def _history(rows: int = 260, freq: str = "D") -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=rows, freq=freq, tz="UTC")
    closes = [100 + i * 0.5 for i in range(rows)]
    return pd.DataFrame(
        {
            "Open": [close - 0.4 for close in closes],
            "High": [close + 1.0 for close in closes],
            "Low": [close - 1.0 for close in closes],
            "Close": closes,
            "Volume": [1_000_000 + i * 1_000 for i in range(rows)],
        },
        index=index,
    )


def test_yfinance_analysis_computes_daily_indicators(monkeypatch):
    from app.services import yfinance_analysis

    captured = {}

    def fake_download_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        captured.update({"symbol": symbol, "period": period, "interval": interval})
        return _history()

    monkeypatch.setattr(yfinance_analysis, "_download_history", fake_download_history)
    monkeypatch.setattr(
        yfinance_analysis,
        "_download_fast_info",
        lambda symbol: {
            "marketCap": 2_820_000_000_000,
            "yearHigh": 555.45,
            "yearLow": 349.2,
        },
        raising=False,
    )

    response = yfinance_analysis.get_analysis("NASDAQ", "MSFT", "1D")

    assert captured == {"symbol": "MSFT", "period": "1y", "interval": "1d"}
    assert response["symbol"] == "NASDAQ:MSFT"
    assert response["exchange"] == "NASDAQ"
    assert response["timeframe"] == "1D"
    assert response["source"] == "yfinance"
    assert response["price_data"]["current_price"] == 229.5
    assert response["price_data"]["market_cap"] == 2_820_000_000_000
    assert response["price_data"]["fifty_two_week_high"] == 555.45
    assert response["price_data"]["fifty_two_week_low"] == 349.2
    assert response["valuation_metrics"] == {
        "trailing_pe": None,
        "forward_pe": None,
        "diluted_eps_ttm": None,
        "forward_eps": None,
        "primary_pe": "trailing",
        "pe_calculated": False,
    }
    assert response["rsi"]["value"] is not None
    assert response["macd"]["macd"] is not None
    assert response["sma"]["sma20"] is not None
    assert response["ema"]["ema50"] is not None
    assert response["bollinger_bands"]["upper"] is not None
    assert response["atr"]["value"] is not None
    assert response["market_sentiment"]["buy_sell_signal"] in {"BUY", "SELL", "NEUTRAL"}


def test_yfinance_analysis_maps_sgx_symbol_and_resamples_four_hour(monkeypatch):
    from app.services import yfinance_analysis

    captured = {}

    def fake_download_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        captured.update({"symbol": symbol, "period": period, "interval": interval})
        return _history(rows=120, freq="h")

    monkeypatch.setattr(yfinance_analysis, "_download_history", fake_download_history)
    monkeypatch.setattr(yfinance_analysis, "_download_fast_info", lambda symbol: {}, raising=False)

    response = yfinance_analysis.get_analysis("SGX", "D05.SI", "4H")

    assert captured == {"symbol": "D05.SI", "period": "730d", "interval": "1h"}
    assert response["symbol"] == "SGX:D05"
    assert response["timeframe"] == "4h"
    assert response["price_data"]["current_price"] is not None


def test_yfinance_analysis_derives_market_cap_when_fast_info_omits_it(monkeypatch):
    from app.services import yfinance_analysis

    monkeypatch.setattr(
        yfinance_analysis,
        "_download_history",
        lambda symbol, period, interval: _history(rows=40),
    )
    monkeypatch.setattr(
        yfinance_analysis,
        "_download_fast_info",
        lambda symbol: {
            "shares": 10_000_000,
            "lastPrice": 229.5,
            "yearHigh": 240,
            "yearLow": 90,
        },
        raising=False,
    )

    response = yfinance_analysis.get_analysis("NASDAQ", "MSFT", "1D")

    assert response["price_data"]["market_cap"] == 2_295_000_000
    assert response["price_data"]["fifty_two_week_high"] == 240
    assert response["price_data"]["fifty_two_week_low"] == 90


def test_yfinance_analysis_includes_direct_valuation_metrics(monkeypatch):
    from app.services import yfinance_analysis

    monkeypatch.setattr(
        yfinance_analysis,
        "_download_history",
        lambda symbol, period, interval: _history(rows=40),
    )
    monkeypatch.setattr(
        yfinance_analysis,
        "_download_fast_info",
        lambda symbol: {},
        raising=False,
    )
    monkeypatch.setattr(
        yfinance_analysis,
        "get_valuation_metadata",
        lambda symbol: {
            "trailing_pe": 22.5,
            "forward_pe": 18,
            "diluted_eps_ttm": 10.2,
            "forward_eps": 12.75,
        },
        raising=False,
    )

    response = yfinance_analysis.get_analysis("NASDAQ", "MSFT", "1D")

    assert response["valuation_metrics"] == {
        "trailing_pe": 22.5,
        "forward_pe": 18.0,
        "diluted_eps_ttm": 10.2,
        "forward_eps": 12.75,
        "primary_pe": "trailing",
        "pe_calculated": False,
    }


def test_yfinance_analysis_returns_symbol_not_found_for_empty_history(monkeypatch):
    from app.services import yfinance_analysis

    monkeypatch.setattr(
        yfinance_analysis,
        "_download_history",
        lambda symbol, period, interval: pd.DataFrame(),
    )
    monkeypatch.setattr(yfinance_analysis, "_download_fast_info", lambda symbol: {}, raising=False)

    response = yfinance_analysis.get_analysis("NASDAQ", "MISSING", "1D")

    assert response == {
        "error": {
            "code": "SYMBOL_NOT_FOUND",
            "message": "No yfinance history found for MISSING.",
            "retryable": False,
        }
    }
