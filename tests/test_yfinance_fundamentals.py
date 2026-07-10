from __future__ import annotations

import math
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import yfinance_fundamentals
from app.services.yfinance_fundamentals import build_valuation_metrics


@pytest.fixture(autouse=True)
def clear_fundamentals_cache():
    clear_cache = getattr(yfinance_fundamentals, "_clear_cache", lambda: None)
    clear_cache()
    yield
    clear_cache()


def test_build_valuation_metrics_prefers_direct_ratios():
    result = build_valuation_metrics(
        100,
        {
            "trailing_pe": 20,
            "forward_pe": 16,
            "diluted_eps_ttm": 5,
            "forward_eps": 6.25,
        },
    )

    assert result == {
        "trailing_pe": 20.0,
        "forward_pe": 16.0,
        "diluted_eps_ttm": 5.0,
        "forward_eps": 6.25,
        "primary_pe": "trailing",
        "pe_calculated": False,
    }


def test_build_valuation_metrics_calculates_missing_ratios():
    result = build_valuation_metrics(
        100,
        {
            "diluted_eps_ttm": 4,
            "forward_eps": 5,
        },
    )

    assert result == {
        "trailing_pe": 25.0,
        "forward_pe": 20.0,
        "diluted_eps_ttm": 4.0,
        "forward_eps": 5.0,
        "primary_pe": "trailing",
        "pe_calculated": True,
    }


@pytest.mark.parametrize("invalid", [0, -1, math.nan, math.inf, -math.inf])
def test_build_valuation_metrics_rejects_non_positive_or_non_finite_values(invalid):
    result = build_valuation_metrics(
        invalid,
        {
            "trailing_pe": invalid,
            "forward_pe": invalid,
            "diluted_eps_ttm": invalid,
            "forward_eps": invalid,
        },
    )

    assert result == {
        "trailing_pe": None,
        "forward_pe": None,
        "diluted_eps_ttm": None,
        "forward_eps": None,
        "primary_pe": "trailing",
        "pe_calculated": False,
    }


def test_get_valuation_metadata_uses_statement_diluted_eps_fallback(monkeypatch):
    class FakeTicker:
        def __init__(self, symbol: str):
            assert symbol == "MSFT"

        def get_info(self):
            return {
                "trailingPE": None,
                "forwardPE": 18,
                "trailingEps": None,
                "forwardEps": 5,
            }

        def get_income_stmt(self, freq: str):
            assert freq == "trailing"
            return pd.DataFrame({"TTM": {"DilutedEPS": 4.0}})

    monkeypatch.setattr(yfinance_fundamentals.yf, "Ticker", FakeTicker)

    result = yfinance_fundamentals.get_valuation_metadata("msft")

    assert result == {
        "trailing_pe": None,
        "forward_pe": 18,
        "diluted_eps_ttm": 4.0,
        "forward_eps": 5,
    }


def test_get_valuation_metadata_keeps_analysis_available_on_provider_failure(monkeypatch):
    class FailingTicker:
        def __init__(self, symbol: str):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(yfinance_fundamentals.yf, "Ticker", FailingTicker)

    assert yfinance_fundamentals.get_valuation_metadata("MSFT") == {}


def test_get_valuation_metadata_caches_until_configured_ttl_expires(monkeypatch):
    calls = []
    now = {"value": 100.0}

    def fake_download(symbol: str):
        calls.append(symbol)
        return {"trailing_pe": len(calls) * 10}

    monkeypatch.setattr(
        yfinance_fundamentals,
        "get_settings",
        lambda: SimpleNamespace(cache_ttl_seconds=60),
    )
    monkeypatch.setattr(yfinance_fundamentals, "monotonic", lambda: now["value"])
    monkeypatch.setattr(
        yfinance_fundamentals,
        "_download_valuation_metadata",
        fake_download,
    )

    first = yfinance_fundamentals.get_valuation_metadata("msft")
    now["value"] = 159.0
    cached = yfinance_fundamentals.get_valuation_metadata("MSFT")
    now["value"] = 161.0
    refreshed = yfinance_fundamentals.get_valuation_metadata("MSFT")

    assert first == {"trailing_pe": 10}
    assert cached == first
    assert refreshed == {"trailing_pe": 20}
    assert calls == ["MSFT", "MSFT"]
