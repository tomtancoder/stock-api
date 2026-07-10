from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_points_to_trading_intelligence_docs():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "Trading Intelligence API",
        "docs": "/docs",
        "health": "/health",
    }


def test_market_quote_endpoint_uses_tradingview_provider(monkeypatch):
    from app.api.v1 import markets

    def fake_quote(exchange: str, symbol: str):
        assert exchange == "NASDAQ"
        assert symbol == "TSLA"
        return {
            "symbol": "TSLA",
            "exchange": "NASDAQ",
            "price": 428.11,
            "previous_close": 423.19,
            "change": 4.92,
            "change_percent": 1.1626,
            "currency": "USD",
            "market_state": "REGULAR",
            "fifty_two_week_high": 555.45,
            "fifty_two_week_low": 349.2,
            "source": "Yahoo Finance",
            "timestamp": "2026-07-09T00:00:00+00:00",
            "warnings": [],
        }

    monkeypatch.setattr(markets.provider, "get_quote", fake_quote)

    response = client.get("/api/v1/markets/NASDAQ/TSLA/quote")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "TSLA"
    assert payload["exchange"] == "NASDAQ"
    assert payload["price"] == 428.11
    assert payload["change_percent"] == 1.1626
    assert payload["fifty_two_week_high"] == 555.45
    assert payload["fifty_two_week_low"] == 349.2


def test_market_analysis_endpoint_returns_tradingview_analysis(monkeypatch):
    from app.api.v1 import markets

    def fake_analysis(exchange: str, symbol: str, timeframe: str):
        assert (exchange, symbol, timeframe) == ("NASDAQ", "TSLA", "1D")
        return {
            "symbol": "NASDAQ:TSLA",
            "exchange": "NASDAQ",
            "timeframe": "1D",
            "price_data": {"current_price": 428.11, "volume": 44_000_000},
            "rsi": {"value": 61.2, "signal": "Bullish"},
            "market_sentiment": {"overall_rating": 2, "buy_sell_signal": "BUY"},
        }

    monkeypatch.setattr(markets.provider, "get_analysis", fake_analysis)

    response = client.get("/api/v1/markets/NASDAQ/TSLA/analysis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "NASDAQ:TSLA"
    assert payload["rsi"]["signal"] == "Bullish"
    assert payload["market_sentiment"]["buy_sell_signal"] == "BUY"


def test_market_analysis_retryable_error_returns_retry_after_header(monkeypatch):
    from app.api.v1 import markets
    from app.services.tradingview_provider import TradingViewProviderError

    monkeypatch.setattr(
        markets.provider,
        "get_analysis",
        lambda exchange, symbol, timeframe: (_ for _ in ()).throw(
            TradingViewProviderError(
                "TradingView scanner is temporarily unavailable.",
                status_code=503,
                retry_after_s=60,
            )
        ),
    )

    response = client.get("/api/v1/markets/NASDAQ/TSLA/analysis")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert response.json()["detail"] == "TradingView scanner is temporarily unavailable."


def test_market_technical_endpoint_returns_tradingview_mcp_analysis(monkeypatch):
    from app.api.v1 import markets

    def fake_technical(
        exchange: str,
        symbol: str,
        timeframe: str,
        include_multi_timeframe: bool,
    ):
        assert (exchange, symbol, timeframe, include_multi_timeframe) == (
            "NASDAQ",
            "TSLA",
            "1D",
            False,
        )
        return {
            "symbol": "NASDAQ:TSLA",
            "exchange": "NASDAQ",
            "timeframe": "1D",
            "source": "tradingview_mcp",
            "timestamp": "real-time",
            "price_data": {
                "current_price": 428.11,
                "fifty_two_week_high": 555.45,
                "fifty_two_week_low": 349.2,
            },
            "valuation_metrics": {"trailing_pe": 65.2, "primary_pe": "trailing"},
            "warnings": [],
            "market_sentiment": {"overall_rating": 2, "buy_sell_signal": "BUY"},
            "stock_score": 87,
            "grade": "A",
            "trend_state": "bullish",
            "trade_setup": {"risk_reward": 2.4},
            "rsi": {"value": 61.2},
        }

    monkeypatch.setattr(markets.provider, "get_technical_analysis", fake_technical)

    response = client.get("/api/v1/markets/NASDAQ/TSLA/technical")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "tradingview_mcp"
    assert payload["stock_score"] == 87
    assert payload["market_sentiment"]["buy_sell_signal"] == "BUY"
    assert payload["trade_setup"]["risk_reward"] == 2.4
    assert payload["price_data"]["fifty_two_week_high"] == 555.45
    assert payload["valuation_metrics"]["trailing_pe"] == 65.2
    assert payload["warnings"] == []
    assert "multi_timeframe" not in payload


def test_market_technical_endpoint_can_request_multi_timeframe(monkeypatch):
    from app.api.v1 import markets

    def fake_technical(exchange, symbol, timeframe, include_multi_timeframe):
        assert (exchange, symbol, timeframe, include_multi_timeframe) == (
            "NASDAQ",
            "TSLA",
            "4h",
            True,
        )
        return {
            "symbol": "NASDAQ:TSLA",
            "timeframe": "4h",
            "source": "tradingview_mcp",
            "price_data": {
                "current_price": 428.11,
                "fifty_two_week_high": 555.45,
                "fifty_two_week_low": 349.2,
            },
            "valuation_metrics": {"trailing_pe": 65.2, "primary_pe": "trailing"},
            "warnings": [],
            "multi_timeframe": {
                "alignment": {"status": "MOSTLY BULLISH", "confidence": "High"}
            },
        }

    monkeypatch.setattr(markets.provider, "get_technical_analysis", fake_technical)

    response = client.get(
        "/api/v1/markets/NASDAQ/TSLA/technical"
        "?timeframe=4h&include_multi_timeframe=true"
    )

    assert response.status_code == 200
    assert response.json()["multi_timeframe"]["alignment"]["confidence"] == "High"


def test_market_score_endpoint_is_removed():
    response = client.get("/api/v1/markets/NASDAQ/TSLA/score")

    assert response.status_code == 404


def test_market_sgx_endpoints_accept_singapore_symbols(monkeypatch):
    from app.api.v1 import markets

    calls = []

    monkeypatch.setattr(
        markets.provider,
        "get_quote",
        lambda exchange, symbol: calls.append(("quote", exchange, symbol))
        or {
            "symbol": "D05.SI",
            "exchange": "SGX",
            "price": 70.02,
            "currency": "SGD",
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        markets.provider,
        "get_analysis",
        lambda exchange, symbol, timeframe: calls.append(("analysis", exchange, symbol, timeframe))
        or {
            "symbol": "SGX:D05",
            "exchange": "sgx",
            "timeframe": "1D",
            "price_data": {"current_price": 70.02},
        },
    )

    quote = client.get("/api/v1/markets/SGX/D05/quote").json()
    analysis = client.get("/api/v1/markets/SGX/D05.SI/analysis?timeframe=1D").json()

    assert quote["symbol"] == "D05.SI"
    assert quote["currency"] == "SGD"
    assert analysis["symbol"] == "SGX:D05"
    assert calls == [
        ("quote", "SGX", "D05"),
        ("analysis", "SGX", "D05.SI", "1D"),
    ]


def test_market_screener_endpoints_delegate_to_provider(monkeypatch):
    from app.api.v1 import markets

    calls = []

    monkeypatch.setattr(
        markets.provider,
        "get_gainers",
        lambda exchange, timeframe, limit: calls.append(("gainers", exchange, timeframe, limit))
        or [{"symbol": "NASDAQ:TSLA"}],
    )
    monkeypatch.setattr(
        markets.provider,
        "get_losers",
        lambda exchange, timeframe, limit: calls.append(("losers", exchange, timeframe, limit))
        or [{"symbol": "NASDAQ:AAPL"}],
    )
    monkeypatch.setattr(
        markets.provider,
        "get_bollinger_scan",
        lambda exchange, timeframe, bbw_threshold, limit: calls.append(
            ("bollinger", exchange, timeframe, bbw_threshold, limit)
        )
        or [{"symbol": "NASDAQ:NVDA"}],
    )
    monkeypatch.setattr(
        markets.provider,
        "get_rating_filter",
        lambda exchange, timeframe, rating, limit: calls.append(
            ("rating", exchange, timeframe, rating, limit)
        )
        or [{"symbol": "NASDAQ:MSFT"}],
    )

    assert client.get("/api/v1/markets/NASDAQ/gainers?timeframe=1D&limit=3").json() == [
        {"symbol": "NASDAQ:TSLA"}
    ]
    assert client.get("/api/v1/markets/NASDAQ/losers?timeframe=1D&limit=2").json() == [
        {"symbol": "NASDAQ:AAPL"}
    ]
    assert client.get("/api/v1/markets/NASDAQ/bollinger-scan?bbw_threshold=0.05").json() == [
        {"symbol": "NASDAQ:NVDA"}
    ]
    assert client.get("/api/v1/markets/NASDAQ/rating-filter?rating=2").json() == [
        {"symbol": "NASDAQ:MSFT"}
    ]
    assert calls == [
        ("gainers", "NASDAQ", "1D", 3),
        ("losers", "NASDAQ", "1D", 2),
        ("bollinger", "NASDAQ", "1D", 0.05, 50),
        ("rating", "NASDAQ", "1D", 2, 25),
    ]


def test_backtest_endpoints_delegate_to_provider(monkeypatch):
    from app.api.v1 import backtests

    calls = []
    monkeypatch.setattr(
        backtests.provider,
        "run_backtest",
        lambda exchange, symbol, request: calls.append(("run", exchange, symbol, request.strategy))
        or {"symbol": "NASDAQ:TSLA", "strategy": request.strategy},
    )
    monkeypatch.setattr(
        backtests.provider,
        "compare_strategies",
        lambda exchange, symbol, request: calls.append(("compare", exchange, symbol, request.period))
        or {"symbol": "NASDAQ:TSLA", "leaderboard": []},
    )
    monkeypatch.setattr(
        backtests.provider,
        "walk_forward_backtest",
        lambda exchange, symbol, request: calls.append(("walk", exchange, symbol, request.strategy))
        or {"symbol": "NASDAQ:TSLA", "folds": []},
    )

    assert client.post("/api/v1/backtests/NASDAQ/TSLA", json={"strategy": "rsi"}).json()[
        "strategy"
    ] == "rsi"
    assert client.post("/api/v1/backtests/NASDAQ/TSLA/compare", json={"period": "2y"}).json()[
        "leaderboard"
    ] == []
    assert client.post(
        "/api/v1/backtests/NASDAQ/TSLA/walk-forward", json={"strategy": "macd"}
    ).json()["folds"] == []
    assert calls == [
        ("run", "NASDAQ", "TSLA", "rsi"),
        ("compare", "NASDAQ", "TSLA", "2y"),
        ("walk", "NASDAQ", "TSLA", "macd"),
    ]


def test_sentiment_and_news_endpoints_delegate_to_provider(monkeypatch):
    from app.api.v1 import intelligence

    monkeypatch.setattr(
        intelligence.provider,
        "get_sentiment",
        lambda symbol, category, limit: {
            "symbol": symbol,
            "sentiment_label": "Bullish",
            "posts_analyzed": limit,
            "category": category,
        },
    )
    monkeypatch.setattr(
        intelligence.provider,
        "get_news",
        lambda symbol, category, limit: {
            "symbol": symbol,
            "category": category,
            "items": [{"title": "Market update"}],
            "limit": limit,
        },
    )

    sentiment = client.get("/api/v1/sentiment/TSLA?category=stocks&limit=5").json()
    news = client.get("/api/v1/news?symbol=TSLA&category=stocks&limit=3").json()

    assert sentiment["sentiment_label"] == "Bullish"
    assert sentiment["posts_analyzed"] == 5
    assert news["items"][0]["title"] == "Market update"
    assert news["limit"] == 3


def test_stock_routes_remain_compatibility_aliases(monkeypatch):
    from app.api.v1 import stocks

    monkeypatch.setattr(
        stocks.provider,
        "get_quote",
        lambda exchange, symbol: {
            "symbol": symbol,
            "exchange": exchange,
            "price": 428.11,
            "warnings": [],
        },
    )
    technical_calls = []
    monkeypatch.setattr(
        stocks.provider,
        "get_technical_analysis",
        lambda exchange, symbol, timeframe, include_multi_timeframe: technical_calls.append(
            (exchange, symbol, timeframe, include_multi_timeframe)
        )
        or {
            "symbol": f"{exchange}:{symbol}",
            "timeframe": timeframe,
            "source": "tradingview_mcp",
            "market_sentiment": {"buy_sell_signal": "BUY"},
            "price_data": {
                "fifty_two_week_high": 555.45,
                "fifty_two_week_low": 349.2,
            },
            "valuation_metrics": {"trailing_pe": 65.2, "primary_pe": "trailing"},
            "warnings": [],
            **(
                {"multi_timeframe": {"alignment": {"status": "MOSTLY BULLISH"}}}
                if include_multi_timeframe
                else {}
            ),
        },
    )

    quote = client.get("/api/v1/stocks/TSLA/quote?exchange=NYSE").json()
    technicals = client.get(
        "/api/v1/stocks/TSLA/technicals?timeframe=4h&include_multi_timeframe=true"
    ).json()
    valuation = client.post("/api/v1/stocks/TSLA/valuation")
    fundamentals = client.get("/api/v1/stocks/TSLA/fundamentals")

    assert quote["exchange"] == "NYSE"
    assert technicals["timeframe"] == "4h"
    assert technicals["source"] == "tradingview_mcp"
    assert technicals["valuation_metrics"]["trailing_pe"] == 65.2
    assert technicals["multi_timeframe"]["alignment"]["status"] == "MOSTLY BULLISH"
    assert technical_calls == [("NASDAQ", "TSLA", "4h", True)]
    assert valuation.status_code == 501
    assert "Valuation is not supported" in valuation.json()["detail"]
    assert fundamentals.status_code == 501
    assert "Fundamentals are not supported" in fundamentals.json()["detail"]
