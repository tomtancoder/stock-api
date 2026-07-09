import pytest


def test_trade_score_uses_stock_score_when_available(monkeypatch):
    from app.services import tradingview_provider as provider

    monkeypatch.setattr(
        provider,
        "analyze_coin",
        lambda symbol, exchange, timeframe: {
            "symbol": "NASDAQ:TSLA",
            "exchange": "nasdaq",
            "timeframe": "1D",
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

    response = provider.get_trade_score("NASDAQ", "TSLA", "1D")

    assert response["symbol"] == "NASDAQ:TSLA"
    assert response["score"] == 87.0
    assert response["score_source"] == "stock_score"
    assert response["signal"] == "BUY"
    assert response["grade"] == "A"
    assert response["risk_reward"] == 2.4
    assert response["key_indicators"]["rsi"]["value"] == 61.2


def test_trade_score_maps_technical_rating_when_stock_score_is_missing(monkeypatch):
    from app.services import tradingview_provider as provider

    monkeypatch.setattr(
        provider,
        "analyze_coin",
        lambda symbol, exchange, timeframe: {
            "symbol": "BINANCE:BTCUSDT",
            "exchange": "binance",
            "timeframe": "4h",
            "price_data": {"current_price": 112_000},
            "market_sentiment": {"overall_rating": 2, "buy_sell_signal": "BUY"},
            "rsi": {"value": 58.5},
        },
    )

    response = provider.get_trade_score("BINANCE", "BTCUSDT", "4h")

    assert response["score"] == 83.33
    assert response["score_source"] == "technical_rating"
    assert response["signal"] == "BUY"
    assert response["warnings"] == []


def test_trade_score_raises_provider_error_for_error_envelope(monkeypatch):
    from app.services import tradingview_provider as provider

    monkeypatch.setattr(
        provider,
        "analyze_coin",
        lambda symbol, exchange, timeframe: {
            "error": {
                "code": "SYMBOL_NOT_FOUND",
                "message": "No data found for MISSING on NASDAQ.",
                "retryable": False,
            }
        },
    )

    with pytest.raises(provider.TradingViewProviderError) as exc_info:
        provider.get_trade_score("NASDAQ", "MISSING", "1D")

    assert exc_info.value.status_code == 404
    assert "No data found" in str(exc_info.value)


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

    def fake_analyze_coin(symbol: str, exchange: str, timeframe: str):
        assert symbol == "D05"
        assert exchange == "sgx"
        assert timeframe == "1d"
        return {"symbol": "SGX:D05", "exchange": "sgx", "timeframe": "1d"}

    monkeypatch.setattr(provider, "analyze_coin", fake_analyze_coin)

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
