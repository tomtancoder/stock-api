import pandas as pd
import pytest


def test_technical_analysis_uses_tradingview_mcp_analyze_coin(monkeypatch):
    from app.services import tradingview_provider as provider

    calls = []
    multi_timeframe_calls = []

    monkeypatch.setattr(
        provider,
        "analyze_coin",
        lambda symbol, exchange, timeframe: calls.append((symbol, exchange, timeframe))
        or {
            "symbol": "NASDAQ:TSLA",
            "exchange": "NASDAQ",
            "timeframe": "1D",
            "timestamp": "real-time",
            "price_data": {"current_price": 428.11},
            "market_sentiment": {"overall_rating": 2, "buy_sell_signal": "BUY"},
            "stock_score": 87,
            "grade": "A",
            "trend_state": "bullish",
            "trade_setup": {"risk_reward": 2.4},
            "rsi": {"value": 61.2},
            "macd": {"signal": "Bullish"},
            "ema": {"ema50": 410.2},
            "bollinger_bands": {"position": "upper"},
            "atr": {"value": 12.2},
        },
    )
    monkeypatch.setattr(
        provider,
        "_get_tradingview_reference_data",
        lambda exchange, symbol: {
            "trailing_pe": 38.25,
            "fifty_two_week_high": 555.45,
            "fifty_two_week_low": 349.2,
        },
        raising=False,
    )
    monkeypatch.setattr(
        provider,
        "get_yfinance_analysis",
        lambda *args: pytest.fail("/technical must not use yFinance analysis"),
    )
    monkeypatch.setattr(
        provider,
        "_run_multi_timeframe_analysis",
        lambda *args: multi_timeframe_calls.append(args),
        raising=False,
    )

    response = provider.get_technical_analysis("NASDAQ", "TSLA", "1d")

    assert calls == [("TSLA", "NASDAQ", "1D")]
    assert response["symbol"] == "NASDAQ:TSLA"
    assert response["source"] == "tradingview_mcp"
    assert response["market_sentiment"]["buy_sell_signal"] == "BUY"
    assert response["stock_score"] == 87
    assert response["grade"] == "A"
    assert response["trade_setup"]["risk_reward"] == 2.4
    assert response["rsi"]["value"] == 61.2
    assert response["price_data"]["fifty_two_week_high"] == 555.45
    assert response["price_data"]["fifty_two_week_low"] == 349.2
    assert response["valuation_metrics"] == {
        "trailing_pe": 38.25,
        "primary_pe": "trailing",
    }
    assert response["warnings"] == []
    assert "multi_timeframe" not in response
    assert multi_timeframe_calls == []


def test_technical_analysis_strips_sgx_yahoo_suffix(monkeypatch):
    from app.services import tradingview_provider as provider

    captured = {}

    def fake_analyze_coin(symbol: str, exchange: str, timeframe: str):
        captured["args"] = (symbol, exchange, timeframe)
        return {
            "symbol": "SGX:D05",
            "exchange": "SGX",
            "timeframe": "1D",
            "market_sentiment": {"buy_sell_signal": "NEUTRAL"},
        }

    monkeypatch.setattr(provider, "analyze_coin", fake_analyze_coin)
    monkeypatch.setattr(
        provider,
        "_get_tradingview_reference_data",
        lambda exchange, symbol: {
            "trailing_pe": None,
            "fifty_two_week_high": None,
            "fifty_two_week_low": None,
        },
        raising=False,
    )

    response = provider.get_technical_analysis("SGX", "D05.SI", "1D")

    assert captured["args"] == ("D05", "SGX", "1D")
    assert response["symbol"] == "SGX:D05"
    assert response["timeframe"] == "1D"
    assert response["source"] == "tradingview_mcp"


@pytest.mark.parametrize(
    ("exchange", "symbol", "expected"),
    [
        ("SGX", "D05.SI", "SGX:D05"),
        ("TVC", "XAUUSD", "TVC:GOLD"),
    ],
)
def test_tradingview_symbol_normalizes_provider_aliases(exchange, symbol, expected):
    from app.services import tradingview_provider as provider

    assert provider._tradingview_symbol(exchange, symbol) == expected


def test_tradingview_reference_data_uses_scanner_fields_and_normalized_symbol(monkeypatch):
    from app.services import tradingview_provider as provider

    captured = {}

    def fake_scan(query, cache_key):
        captured["query"] = query.query
        captured["cache_key"] = cache_key
        return 1, pd.DataFrame(
            [
                {
                    "ticker": "NASDAQ:AAPL",
                    "price_earnings_ttm": 38.2531905885,
                    "price_52_week_high": 317.4,
                    "price_52_week_low": 201.5,
                }
            ]
        )

    monkeypatch.setattr(provider, "_scan_with_retry", fake_scan, raising=False)

    response = provider._get_tradingview_reference_data("NASDAQ", "AAPL")

    assert captured["query"]["markets"] == ["america"]
    assert captured["query"]["symbols"]["tickers"] == ["NASDAQ:AAPL"]
    assert captured["query"]["columns"] == [
        "price_earnings_ttm",
        "price_52_week_high",
        "price_52_week_low",
    ]
    assert captured["cache_key"] == (
        "technical_reference_v1",
        "america",
        "NASDAQ:AAPL",
    )
    assert response == {
        "trailing_pe": 38.2531905885,
        "fifty_two_week_high": 317.4,
        "fifty_two_week_low": 201.5,
    }


@pytest.mark.parametrize("invalid_pe", [None, 0, -10, float("nan"), float("inf")])
def test_tradingview_reference_data_normalizes_invalid_pe(monkeypatch, invalid_pe):
    from app.services import tradingview_provider as provider

    monkeypatch.setattr(
        provider,
        "_scan_with_retry",
        lambda query, cache_key: (
            1,
            pd.DataFrame(
                [
                    {
                        "ticker": "NASDAQ:LOSS",
                        "price_earnings_ttm": invalid_pe,
                        "price_52_week_high": float("nan"),
                        "price_52_week_low": None,
                    }
                ]
            ),
        ),
        raising=False,
    )

    response = provider._get_tradingview_reference_data("NASDAQ", "LOSS")

    assert response == {
        "trailing_pe": None,
        "fifty_two_week_high": None,
        "fifty_two_week_low": None,
    }


def test_technical_analysis_preserves_base_result_when_reference_lookup_fails(monkeypatch):
    from app.services import tradingview_provider as provider

    monkeypatch.setattr(
        provider,
        "analyze_coin",
        lambda symbol, exchange, timeframe: {
            "symbol": "NASDAQ:TSLA",
            "exchange": exchange,
            "timeframe": timeframe,
            "price_data": {"current_price": 428.11},
        },
    )
    monkeypatch.setattr(
        provider,
        "_get_tradingview_reference_data",
        lambda exchange, symbol: (_ for _ in ()).throw(RuntimeError("scanner unavailable")),
        raising=False,
    )

    response = provider.get_technical_analysis("NASDAQ", "TSLA", "1D")

    assert response["price_data"] == {
        "current_price": 428.11,
        "fifty_two_week_high": None,
        "fifty_two_week_low": None,
    }
    assert response["valuation_metrics"] == {
        "trailing_pe": None,
        "primary_pe": "trailing",
    }
    assert response["warnings"] == [
        "TradingView reference data is temporarily unavailable."
    ]


def test_technical_analysis_adds_requested_multi_timeframe_result(monkeypatch):
    from app.services import tradingview_provider as provider

    calls = []
    multi_timeframe = {
        "analysis_type": "Multi-Timeframe Alignment",
        "timeframes": {
            "1W": {"bias": "Bullish"},
            "1D": {"bias": "Bullish"},
            "4h": {"bias": "Bullish"},
            "1h": {"bias": "Neutral"},
            "15m": {"bias": "Bullish"},
        },
        "alignment": {"status": "MOSTLY BULLISH", "confidence": "High"},
        "recommendation": {"action": "BUY"},
    }
    monkeypatch.setattr(
        provider,
        "analyze_coin",
        lambda symbol, exchange, timeframe: {
            "symbol": "TVC:GOLD",
            "exchange": exchange,
            "timeframe": timeframe,
            "price_data": {"current_price": 4114.67},
        },
    )
    monkeypatch.setattr(
        provider,
        "_get_tradingview_reference_data",
        lambda exchange, symbol: {
            "trailing_pe": None,
            "fifty_two_week_high": 4200.0,
            "fifty_two_week_low": 2500.0,
        },
        raising=False,
    )
    monkeypatch.setattr(
        provider,
        "_run_multi_timeframe_analysis",
        lambda symbol, exchange: calls.append((symbol, exchange)) or multi_timeframe,
        raising=False,
    )

    response = provider.get_technical_analysis("TVC", "XAUUSD", "1D", True)

    assert calls == [("TVC:GOLD", "TVC")]
    assert response["multi_timeframe"] == multi_timeframe
    assert response["warnings"] == []


def test_technical_analysis_preserves_partial_multi_timeframe_result(monkeypatch):
    from app.services import tradingview_provider as provider

    partial = {
        "timeframes": {
            "1W": {"bias": "Bullish"},
            "1D": {"error": "No data for 1D"},
        },
        "alignment": {"status": "LEAN BULLISH"},
    }
    monkeypatch.setattr(
        provider,
        "analyze_coin",
        lambda symbol, exchange, timeframe: {
            "symbol": "SGX:D05",
            "exchange": exchange,
            "timeframe": timeframe,
            "price_data": {"current_price": 70.02},
        },
    )
    monkeypatch.setattr(
        provider,
        "_get_tradingview_reference_data",
        lambda exchange, symbol: {
            "trailing_pe": 12.1,
            "fifty_two_week_high": 76.8,
            "fifty_two_week_low": 58.4,
        },
        raising=False,
    )
    monkeypatch.setattr(
        provider,
        "_run_multi_timeframe_analysis",
        lambda symbol, exchange: partial,
        raising=False,
    )

    response = provider.get_technical_analysis("SGX", "D05.SI", "1D", True)

    assert response["multi_timeframe"] == partial
    assert response["warnings"] == [
        "TradingView multi-timeframe analysis is incomplete."
    ]


def test_technical_analysis_preserves_base_result_when_multi_timeframe_raises(monkeypatch):
    from app.services import tradingview_provider as provider

    monkeypatch.setattr(
        provider,
        "analyze_coin",
        lambda symbol, exchange, timeframe: {
            "symbol": "NASDAQ:TSLA",
            "exchange": exchange,
            "timeframe": timeframe,
            "price_data": {"current_price": 428.11},
        },
    )
    monkeypatch.setattr(
        provider,
        "_get_tradingview_reference_data",
        lambda exchange, symbol: {
            "trailing_pe": 65.2,
            "fifty_two_week_high": 555.45,
            "fifty_two_week_low": 349.2,
        },
        raising=False,
    )
    monkeypatch.setattr(
        provider,
        "_run_multi_timeframe_analysis",
        lambda symbol, exchange: (_ for _ in ()).throw(RuntimeError("upstream timeout")),
        raising=False,
    )

    response = provider.get_technical_analysis("NASDAQ", "TSLA", "1D", True)

    assert response["multi_timeframe"] == {
        "error": {
            "code": "UPSTREAM_ERROR",
            "message": "Multi-timeframe analysis failed: upstream timeout",
            "retryable": True,
        }
    }
    assert response["warnings"] == [
        "TradingView multi-timeframe analysis is incomplete."
    ]


def test_technical_analysis_raises_provider_error_for_error_envelope(monkeypatch):
    from app.services import tradingview_provider as provider

    monkeypatch.setattr(
        provider,
        "analyze_coin",
        lambda exchange, symbol, timeframe: {
            "error": {
                "code": "SYMBOL_NOT_FOUND",
                "message": "No data found for MISSING on NASDAQ.",
                "retryable": False,
            }
        },
    )

    with pytest.raises(provider.TradingViewProviderError) as exc_info:
        provider.get_technical_analysis("NASDAQ", "MISSING", "1D")

    assert exc_info.value.status_code == 404
    assert "No data found" in str(exc_info.value)


def test_retryable_provider_error_preserves_retry_after_hint(monkeypatch):
    from app.services import tradingview_provider as provider

    monkeypatch.setattr(
        provider,
        "get_yfinance_analysis",
        lambda exchange, symbol, timeframe: {
            "error": {
                "code": "UPSTREAM_ERROR",
                "message": "yfinance is temporarily unavailable.",
                "retryable": True,
                "retry_after_s": 60,
            }
        },
    )

    with pytest.raises(provider.TradingViewProviderError) as exc_info:
        provider.get_analysis("NASDAQ", "TSLA", "1D")

    assert exc_info.value.status_code == 503
    assert exc_info.value.retry_after_s == 60


def test_quote_maps_yahoo_price_payload(monkeypatch):
    from app.services import tradingview_provider as provider

    monkeypatch.setattr(
        provider,
        "get_price",
        lambda symbol: {
            "symbol": "TSLA",
            "price": 428.11,
            "previous_close": 423.19,
            "change": 4.92,
            "change_pct": 1.16,
            "currency": "USD",
            "exchange": "NMS",
            "market_state": "REGULAR",
            "52w_high": 555.45,
            "52w_low": 349.2,
            "source": "Yahoo Finance",
            "timestamp": "2026-07-09T00:00:00+00:00",
        },
    )

    response = provider.get_quote("NASDAQ", "TSLA")

    assert response == {
        "symbol": "TSLA",
        "exchange": "NASDAQ",
        "price": 428.11,
        "previous_close": 423.19,
        "change": 4.92,
        "change_percent": 1.16,
        "currency": "USD",
        "market_state": "REGULAR",
        "fifty_two_week_high": 555.45,
        "fifty_two_week_low": 349.2,
        "source": "Yahoo Finance",
        "timestamp": "2026-07-09T00:00:00+00:00",
        "warnings": [],
    }


def test_sgx_quote_uses_yahoo_si_suffix(monkeypatch):
    from app.services import tradingview_provider as provider

    captured = {}

    def fake_get_price(symbol: str):
        captured["symbol"] = symbol
        return {
            "symbol": symbol,
            "price": 70.02,
            "previous_close": 69.1,
            "change": 0.92,
            "change_pct": 1.33,
            "currency": "SGD",
            "exchange": "SES",
            "source": "Yahoo Finance",
        }

    monkeypatch.setattr(provider, "get_price", fake_get_price)

    response = provider.get_quote("SGX", "D05")

    assert captured["symbol"] == "D05.SI"
    assert response["symbol"] == "D05.SI"
    assert response["exchange"] == "SGX"
    assert response["currency"] == "SGD"


def test_sgx_analysis_strips_yahoo_si_suffix(monkeypatch):
    from app.services import tradingview_provider as provider

    def fake_yfinance_analysis(exchange: str, symbol: str, timeframe: str):
        assert exchange == "SGX"
        assert symbol == "D05.SI"
        assert timeframe == "1D"
        return {"symbol": "SGX:D05", "exchange": "sgx", "timeframe": "1d"}

    monkeypatch.setattr(provider, "get_yfinance_analysis", fake_yfinance_analysis)

    response = provider.get_analysis("SGX", "D05.SI", "1D")

    assert response["symbol"] == "SGX:D05"
    assert response["timeframe"] == "1D"


def test_sgx_backtest_tools_use_yahoo_si_suffix(monkeypatch):
    from app.schemas import BacktestRequest, CompareStrategiesRequest, WalkForwardBacktestRequest
    from app.services import tradingview_provider as provider

    captured = {}

    def fake_run_backtest(*args):
        captured["run"] = args
        return {"symbol": args[0], "strategy": args[1]}

    def fake_compare_strategies(*args):
        captured["compare"] = args
        return {"symbol": args[0], "period": args[1]}

    def fake_walk_forward_backtest(*args):
        captured["walk"] = args
        return {"symbol": args[0], "strategy": args[1]}

    monkeypatch.setattr(provider, "_run_backtest", fake_run_backtest)
    monkeypatch.setattr(provider, "_compare_strategies", fake_compare_strategies)
    monkeypatch.setattr(provider, "_walk_forward_backtest", fake_walk_forward_backtest)

    run_response = provider.run_backtest("SGX", "D05", BacktestRequest(strategy="rsi"))
    compare_response = provider.compare_strategies("SGX", "D05", CompareStrategiesRequest())
    walk_response = provider.walk_forward_backtest(
        "SGX", "D05", WalkForwardBacktestRequest(strategy="macd")
    )

    assert captured["run"][0] == "D05.SI"
    assert captured["compare"][0] == "D05.SI"
    assert captured["walk"][0] == "D05.SI"
    assert run_response["symbol"] == "D05.SI"
    assert compare_response["symbol"] == "D05.SI"
    assert walk_response["symbol"] == "D05.SI"


def test_gold_quote_uses_yahoo_futures_symbol(monkeypatch):
    from app.services import tradingview_provider as provider

    captured = {}

    def fake_get_price(symbol: str):
        captured["symbol"] = symbol
        return {
            "symbol": symbol,
            "price": 4120.2,
            "previous_close": 4070.9,
            "change": 49.3,
            "change_pct": 1.21,
            "currency": "USD",
            "exchange": "CMX",
            "source": "Yahoo Finance",
        }

    monkeypatch.setattr(provider, "get_price", fake_get_price)

    response = provider.get_quote("TVC", "XAUUSD")

    assert captured["symbol"] == "GC=F"
    assert response["symbol"] == "GC=F"
    assert response["price"] == 4120.2
    assert response["currency"] == "USD"


def test_gold_backtest_uses_yahoo_futures_symbol(monkeypatch):
    from app.schemas import BacktestRequest
    from app.services import tradingview_provider as provider

    captured = {}

    def fake_run_backtest(*args):
        captured["args"] = args
        return {"symbol": args[0], "strategy": args[1]}

    monkeypatch.setattr(provider, "_run_backtest", fake_run_backtest)

    response = provider.run_backtest("TVC", "XAUUSD", BacktestRequest(strategy="rsi"))

    assert captured["args"][0] == "GC=F"
    assert response["symbol"] == "GC=F"


def test_sgx_market_is_registered_for_tradingview_mcp():
    from app.services import tradingview_provider  # noqa: F401
    from tradingview_mcp.core.utils import validators

    assert "sgx" in validators.STOCK_EXCHANGES
    assert validators.EXCHANGE_SCREENER["sgx"] == "singapore"
    assert validators.get_tv_exchange_prefix("sgx") == "SGX"
