import os

import pytest

from app.services.breakout_analysis import get_breakout_analysis


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_YFINANCE_TESTS") != "1",
    reason="Set RUN_LIVE_YFINANCE_TESTS=1 to call live yFinance breakout data.",
)
@pytest.mark.parametrize(
    ("exchange", "symbol"),
    [("NASDAQ", "AAPL"), ("NASDAQ", "MSFT"), ("SGX", "D05")],
)
def test_live_breakout_analysis_returns_a_structured_result(exchange, symbol):
    result = get_breakout_analysis(exchange, symbol, include_four_hour=False)
    assert result.exchange == exchange
    assert result.symbol == symbol
    assert result.data_status.value in {
        "ready",
        "partial",
        "insufficient_history",
        "stale",
    }
