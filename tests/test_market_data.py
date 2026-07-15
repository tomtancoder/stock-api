from datetime import datetime, timezone

import pandas as pd
import pytest

from app.services import market_data


def _frame(index=None) -> pd.DataFrame:
    if index is None:
        index = pd.date_range("2026-01-01", periods=3, freq="D")
    return pd.DataFrame(
        {
            "Open": [10, 11, 12],
            "High": [11, 12, 13],
            "Low": [9, 10, 11],
            "Close": [10.5, 11.5, 12.5],
            "Volume": [100, 110, 120],
        },
        index=index,
    )


def test_normalize_symbol_rejects_blank_and_uppercases():
    assert market_data.normalize_symbol(" d05.si ") == "D05.SI"
    with pytest.raises(ValueError):
        market_data.normalize_symbol("  ")


def test_validate_ohlcv_sorts_deduplicates_and_coerces():
    frame = _frame(pd.to_datetime(["2026-01-03", "2026-01-01", "2026-01-01"]))
    frame["Close"] = frame["Close"].astype(object)
    frame.iloc[1, frame.columns.get_loc("Close")] = "10.25"
    result = market_data.validate_ohlcv(frame, "ACME")
    assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert result.index.is_monotonic_increasing
    assert result.index.is_unique
    assert len(result) == 2


def test_validate_ohlcv_requires_price_columns_but_allows_missing_volume_values():
    with pytest.raises(market_data.MarketDataError):
        market_data.validate_ohlcv(_frame().drop(columns="High"), "ACME")
    frame = _frame()
    frame.loc[frame.index[-1], "Volume"] = float("nan")
    assert pd.isna(market_data.validate_ohlcv(frame, "ACME")["Volume"].iloc[-1])


def test_completed_daily_bars_drops_open_us_and_singapore_sessions():
    us_today = pd.Timestamp("2026-07-16")
    us = _frame(pd.date_range("2026-07-14", periods=3, freq="D"))
    before_us_close = datetime(2026, 7, 16, 19, 0, tzinfo=timezone.utc)
    assert us_today not in market_data.completed_daily_bars(
        us, symbol="MSFT", now_utc=before_us_close
    ).index

    sg = _frame(pd.date_range("2026-07-14", periods=3, freq="D"))
    before_sg_close = datetime(2026, 7, 16, 8, 30, tzinfo=timezone.utc)
    assert us_today not in market_data.completed_daily_bars(
        sg, symbol="D05.SI", now_utc=before_sg_close
    ).index


def test_completed_daily_bars_keeps_session_after_grace():
    frame = _frame(pd.date_range("2026-07-14", periods=3, freq="D"))
    after_close = datetime(2026, 7, 16, 21, 0, tzinfo=timezone.utc)
    assert pd.Timestamp("2026-07-16") in market_data.completed_daily_bars(
        frame, symbol="MSFT", now_utc=after_close
    ).index


def test_completed_daily_bars_treats_timezone_aware_daily_index_as_session_label():
    index = pd.date_range("2026-07-14", periods=3, freq="D", tz="UTC")
    frame = _frame(index)
    before_close = datetime(2026, 7, 16, 19, 0, tzinfo=timezone.utc)
    result = market_data.completed_daily_bars(
        frame, symbol="MSFT", now_utc=before_close
    )
    assert len(result) == 2


def test_batch_download_parses_ticker_first_multiindex_and_isolates_missing(monkeypatch):
    fields = ["Open", "High", "Low", "Close", "Volume"]
    index = pd.date_range("2026-01-01", periods=3, freq="D")
    columns = pd.MultiIndex.from_product([["AAA", "BBB"], fields])
    values = []
    for row in range(3):
        values.append([10 + row, 11 + row, 9 + row, 10.5 + row, 100] * 2)
    response = pd.DataFrame(values, index=index, columns=columns)
    monkeypatch.setattr(market_data, "_download", lambda **kwargs: response)
    histories, errors = market_data.download_daily_histories(["AAA", "BBB", "MISS"])
    assert set(histories) == {"AAA", "BBB"}
    assert "MISS" in errors


def test_batch_download_parses_field_first_multiindex(monkeypatch):
    fields = ["Open", "High", "Low", "Close", "Volume"]
    index = pd.date_range("2026-01-01", periods=3, freq="D")
    columns = pd.MultiIndex.from_product([fields, ["AAA", "BBB"]])
    response = pd.DataFrame(
        [[10, 20, 11, 21, 9, 19, 10.5, 20.5, 100, 200]] * 3,
        index=index,
        columns=columns,
    )
    monkeypatch.setattr(market_data, "_download", lambda **kwargs: response)
    histories, errors = market_data.download_daily_histories(["AAA", "BBB"])
    assert errors == {}
    assert histories["AAA"]["Close"].iloc[-1] == 10.5
    assert histories["BBB"]["Close"].iloc[-1] == 20.5


def test_fetch_daily_history_requests_adjusted_data(monkeypatch):
    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        return _frame()

    monkeypatch.setattr(market_data, "_download", fake_download)
    market_data.clear_market_data_cache()
    result = market_data.fetch_daily_history("acme")
    assert not result.empty
    assert calls[0]["auto_adjust"] is True
    assert calls[0]["interval"] == "1d"
