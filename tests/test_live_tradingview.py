import os

import pytest

from app.services import tradingview_provider


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TRADINGVIEW_TESTS") != "1",
    reason="Set RUN_LIVE_TRADINGVIEW_TESTS=1 to call live TradingView MCP data.",
)
def test_live_tradingview_provider_can_fetch_quote():
    quote = tradingview_provider.get_quote("NASDAQ", "AAPL")

    assert quote["symbol"] == "AAPL"
    assert quote["exchange"] == "NASDAQ"
    assert quote["price"] is not None

