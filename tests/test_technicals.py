import pandas as pd
import pytest

from app.services import technicals


@pytest.fixture(autouse=True)
def clear_technicals_cache():
    technicals._technicals_cache.clear()


def test_get_stock_technicals_calculates_ema_values(monkeypatch):
    monkeypatch.setattr(technicals.yf, "Ticker", lambda symbol: FakeTicker(symbol, rows=220))

    response = technicals.get_stock_technicals("tsla")
    close_prices = pd.Series(range(1, 221), dtype=float)

    assert response.symbol == "TSLA"
    assert response.period == "1y"
    assert response.interval == "1d"
    assert response.latest_close == 220
    assert response.ema.ema_21 == _expected_ema(close_prices, 21)
    assert response.ema.ema_50 == _expected_ema(close_prices, 50)
    assert response.ema.ema_100 == _expected_ema(close_prices, 100)
    assert response.ema.ema_200 == _expected_ema(close_prices, 200)
    assert response.warnings == []


def test_get_stock_technicals_warns_when_history_is_limited(monkeypatch):
    monkeypatch.setattr(technicals.yf, "Ticker", lambda symbol: FakeTicker(symbol, rows=30))

    response = technicals.get_stock_technicals("new")

    assert response.ema.ema_21 is not None
    assert response.ema.ema_50 is not None
    assert response.ema.ema_100 is not None
    assert response.ema.ema_200 is not None
    assert response.warnings == [
        "Only 30 close prices available; EMA 50 uses limited history.",
        "Only 30 close prices available; EMA 100 uses limited history.",
        "Only 30 close prices available; EMA 200 uses limited history.",
    ]


def test_get_stock_technicals_raises_when_history_is_empty(monkeypatch):
    monkeypatch.setattr(technicals.yf, "Ticker", lambda symbol: EmptyTicker())

    with pytest.raises(ValueError, match="No yFinance price history found"):
        technicals.get_stock_technicals("missing")


def _expected_ema(close_prices: pd.Series, window: int) -> float:
    return round(float(close_prices.ewm(span=window, adjust=False).mean().iloc[-1]), 4)


class FakeTicker:
    def __init__(self, symbol: str, rows: int):
        self.symbol = symbol
        self.rows = rows

    def history(self, period: str, interval: str):
        index = pd.date_range("2026-01-01", periods=self.rows, freq="D")
        return pd.DataFrame(
            {"Close": range(1, self.rows + 1)},
            index=index,
        )


class EmptyTicker:
    def history(self, period: str, interval: str):
        return pd.DataFrame()
