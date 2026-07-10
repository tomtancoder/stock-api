from __future__ import annotations

from collections.abc import Mapping

import pandas as pd
import pytest

from app.services import yfinance_statements
from app.services.yfinance_statements import (
    YFinanceStatementsError,
    fetch_yfinance_fundamentals,
)


def statement(
    rows: Mapping[str, list[object] | object],
    *periods: object,
    currency: str | None = None,
) -> pd.DataFrame:
    normalized_rows = {
        row: value if isinstance(value, list) else [value]
        for row, value in rows.items()
    }
    frame = pd.DataFrame(normalized_rows, index=list(periods)).T
    if currency is not None:
        frame.attrs["currency"] = currency
    return frame


class FakeTicker:
    def __init__(
        self,
        *,
        cashflow: Mapping[str, pd.DataFrame] | None = None,
        income: Mapping[str, pd.DataFrame] | None = None,
        balance: Mapping[str, pd.DataFrame] | None = None,
        info: Mapping[str, object] | None = None,
        fast_info: Mapping[str, object] | None = None,
        shares: pd.Series | None = None,
    ) -> None:
        self.cashflow = dict(cashflow or {})
        self.income = dict(income or {})
        self.balance = dict(balance or {})
        self.info = dict(info or {"financialCurrency": "SGD"})
        self.fast_info = dict(fast_info or {"currency": "SGD"})
        self.shares = shares if shares is not None else pd.Series(dtype=float)
        self.calls: list[tuple[str, str | None]] = []

    def get_cashflow(self, freq: str) -> pd.DataFrame:
        self.calls.append(("cashflow", freq))
        return self.cashflow.get(freq, pd.DataFrame())

    def get_income_stmt(self, freq: str) -> pd.DataFrame:
        self.calls.append(("income", freq))
        return self.income.get(freq, pd.DataFrame())

    def get_balance_sheet(self, freq: str) -> pd.DataFrame:
        self.calls.append(("balance", freq))
        return self.balance.get(freq, pd.DataFrame())

    def get_shares_full(self) -> pd.Series:
        self.calls.append(("shares", None))
        return self.shares

    def get_info(self) -> dict[str, object]:
        self.calls.append(("info", None))
        return self.info


def install_ticker(monkeypatch: pytest.MonkeyPatch, ticker: FakeTicker) -> list[str]:
    symbols: list[str] = []

    def factory(symbol: str) -> FakeTicker:
        symbols.append(symbol)
        return ticker

    monkeypatch.setattr(yfinance_statements.yf, "Ticker", factory)
    return symbols


@pytest.mark.parametrize(
    ("statement_kind", "row_alias", "field"),
    [
        ("cashflow", "Operating Cash Flow", "operating_cash_flow"),
        (
            "cashflow",
            "Total Cash From Operating Activities",
            "operating_cash_flow",
        ),
        ("cashflow", "Capital Expenditure", "capital_expenditure"),
        ("cashflow", "Capital Expenditures", "capital_expenditure"),
        ("cashflow", "Stock Based Compensation", "stock_based_compensation"),
        ("cashflow", "Share Based Compensation", "stock_based_compensation"),
        ("cashflow", "Cash Dividends Paid", "common_dividends"),
        ("cashflow", "Common Stock Dividend Paid", "common_dividends"),
        ("income", "Total Revenue", "revenue"),
        ("income", "Revenue", "revenue"),
        ("income", "Net Income Common Stockholders", "net_income_common"),
        ("income", "Net Income", "net_income_common"),
        ("income", "Diluted Average Shares", "diluted_shares"),
        (
            "income",
            "Weighted Average Number Of Diluted Shares Outstanding",
            "diluted_shares",
        ),
        ("balance", "Stockholders Equity", "common_equity"),
        ("balance", "Common Stock Equity", "common_equity"),
        ("balance", "Total Stockholder Equity", "common_equity"),
        ("balance", "Cash And Cash Equivalents", "cash_and_equivalents"),
        (
            "balance",
            "Cash Cash Equivalents And Short Term Investments",
            "cash_and_equivalents",
        ),
        ("balance", "Total Assets", "total_assets"),
        ("balance", "Total Debt", "total_debt"),
        (
            "balance",
            "Long Term Debt And Capital Lease Obligation",
            "total_debt",
        ),
    ],
)
def test_normalizes_reviewed_statement_aliases(
    monkeypatch: pytest.MonkeyPatch,
    statement_kind: str,
    row_alias: str,
    field: str,
) -> None:
    frames = {statement_kind: {"yearly": statement({row_alias: 123.0}, "2025-12-31")}}
    ticker = FakeTicker(**frames)
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "D05")

    period = result.periods[0]
    assert getattr(period, field) == 123.0
    assert period.sources[field].provider == "yfinance"
    assert period.sources[field].concept == row_alias
    assert period.sources[field].period_end == period.period_end


@pytest.mark.parametrize("row_alias", ["Interest Paid Supplemental", "Interest Paid"])
def test_financing_interest_metadata_preserves_actual_interest(
    monkeypatch: pytest.MonkeyPatch, row_alias: str
) -> None:
    ticker = FakeTicker(
        cashflow={"yearly": statement({row_alias: -17.0}, "2025-12-31")},
        info={
            "financialCurrency": "SGD",
            "interestPaidClassification": "financing",
        },
    )
    install_ticker(monkeypatch, ticker)

    period = fetch_yfinance_fundamentals("SGX", "D05").periods[0]

    assert period.interest_paid_outside_operating == -17.0
    assert (
        period.sources["interest_paid_outside_operating"].concept == row_alias
    )


def test_interest_stays_missing_when_classification_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        cashflow={
            "yearly": statement(
                {"Interest Paid Supplemental": -17.0}, "2025-12-31"
            )
        }
    )
    install_ticker(monkeypatch, ticker)

    period = fetch_yfinance_fundamentals("SGX", "D05").periods[0]

    assert period.interest_paid_outside_operating is None
    assert "interest_paid_outside_operating" not in period.sources


def test_operating_interest_metadata_records_resolved_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        cashflow={
            "yearly": statement(
                {
                    "Operating Cash Flow": 100.0,
                    "Interest Paid": -17.0,
                },
                "2025-12-31",
            )
        },
        info={
            "financialCurrency": "SGD",
            "interestPaidClassification": "operating",
        },
    )
    install_ticker(monkeypatch, ticker)

    period = fetch_yfinance_fundamentals("SGX", "D05").periods[0]

    assert period.interest_paid_outside_operating == 0.0
    assert (
        period.sources["interest_paid_outside_operating"].concept
        == "included_in_operating_cash_flow"
    )


def test_interest_classification_is_specific_to_statement_frequency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annual = statement({"Interest Paid": -11.0}, "2025-12-31")
    annual.attrs["interestPaidClassification"] = "operating"
    trailing = statement({"Interest Paid": -13.0}, "2026-06-30")
    trailing.attrs["interestPaidClassification"] = "financing"
    ticker = FakeTicker(
        cashflow={"yearly": annual, "trailing": trailing},
        info={
            "financialCurrency": "SGD",
            "interestPaidClassification": "financing",
        },
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "D05")

    annual_period, trailing_period = result.periods
    assert annual_period.interest_paid_outside_operating == 0.0
    assert (
        annual_period.sources["interest_paid_outside_operating"].concept
        == "included_in_operating_cash_flow"
    )
    assert trailing_period.interest_paid_outside_operating == -13.0
    assert (
        trailing_period.sources["interest_paid_outside_operating"].concept
        == "Interest Paid"
    )


def test_sgx_provider_normalizes_symbols_currency_periods_and_calls_all_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annual_periods = ("2025-12-31", "2024-12-31")
    trailing_period = ("2026-06-30",)
    ticker = FakeTicker(
        cashflow={
            "yearly": statement(
                {
                    "Operating Cash Flow": [120.0, 100.0],
                    "Capital Expenditure": [-25.0, -20.0],
                    "Stock Based Compensation": [8.0, 7.0],
                },
                *annual_periods,
            ),
            "trailing": statement(
                {
                    "Operating Cash Flow": 130.0,
                    "Capital Expenditure": -30.0,
                    "Stock Based Compensation": 9.0,
                },
                *trailing_period,
            ),
        },
        income={
            "yearly": statement(
                {
                    "Total Revenue": [500.0, 450.0],
                    "Net Income": [50.0, 45.0],
                    "Diluted Average Shares": [40.0, 39.0],
                },
                *annual_periods,
            ),
            "trailing": statement(
                {
                    "Total Revenue": 530.0,
                    "Net Income": 52.0,
                    "Diluted Average Shares": 41.0,
                },
                *trailing_period,
            ),
        },
        balance={
            "yearly": statement(
                {
                    "Common Stock Equity": [300.0, 280.0],
                    "Cash And Cash Equivalents": [80.0, 70.0],
                    "Total Assets": [900.0, 850.0],
                    "Total Debt": [100.0, 110.0],
                },
                *annual_periods,
            ),
            "trailing": statement(
                {
                    "Common Stock Equity": 310.0,
                    "Cash And Cash Equivalents": 85.0,
                    "Total Assets": 920.0,
                    "Total Debt": 95.0,
                },
                *trailing_period,
            ),
        },
        info={
            "financialCurrency": "SGD",
            "currency": "USD",
            "interestPaidClassification": "operating",
            "quoteType": "EQUITY",
            "sector": "Financial Services",
            "industry": "Banks - Regional",
        },
        fast_info={"currency": "USD"},
        shares=pd.Series([39.0, 42.0], index=["2025-01-01", "2026-01-01"]),
    )
    symbols = install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "D05")

    assert symbols == ["D05.SI"]
    assert result.symbol == "SGX:D05"
    assert result.exchange == "SGX"
    assert result.currency == "SGD"
    assert result.primary_source == "yfinance_sgx"
    assert result.provider_security_type == "EQUITY"
    assert result.current_diluted_shares == 42.0
    assert [(period.period_end.isoformat(), period.is_ttm) for period in result.periods] == [
        ("2024-12-31", False),
        ("2025-12-31", False),
        ("2026-06-30", True),
    ]
    assert result.periods[0].common_dividends is None
    assert "common_dividends" not in result.periods[0].sources
    assert {
        (kind, freq)
        for kind, freq in ticker.calls
        if kind in {"cashflow", "income", "balance"}
    } == {
        (kind, freq)
        for kind in ("cashflow", "income", "balance")
        for freq in ("yearly", "quarterly", "trailing")
    }
    assert ("shares", None) in ticker.calls
    assert ("info", None) in ticker.calls


def test_four_quarters_create_ttm_when_direct_trailing_statements_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quarters = ("2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31")
    ticker = FakeTicker(
        cashflow={
            "quarterly": statement(
                {
                    "Operating Cash Flow": [40.0, 30.0, 20.0, 10.0],
                    "Capital Expenditure": [-4.0, -3.0, -2.0, -1.0],
                    "Stock Based Compensation": [4.0, 3.0, 2.0, 1.0],
                },
                *quarters,
            )
        },
        income={
            "quarterly": statement(
                {"Revenue": [400.0, 300.0, 200.0, 100.0]}, *quarters
            )
        },
        balance={
            "quarterly": statement(
                {"Total Assets": [900.0, 850.0, 800.0, 750.0]}, *quarters
            )
        },
        info={
            "financialCurrency": "SGD",
            "interestPaidClassification": "operating",
        },
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "D05")

    assert len(result.periods) == 1
    ttm = result.periods[0]
    assert ttm.period_end.isoformat() == "2025-09-30"
    assert ttm.is_ttm is True
    assert ttm.operating_cash_flow == 100.0
    assert ttm.capital_expenditure == -10.0
    assert ttm.stock_based_compensation == 10.0
    assert ttm.revenue == 1_000.0
    assert ttm.total_assets == 900.0


def test_quarterly_ttm_diluted_shares_uses_latest_value_without_summing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quarters = ("2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31")
    ticker = FakeTicker(
        income={
            "quarterly": statement(
                {"Diluted Average Shares": [100.0, 100.0, 100.0, 100.0]},
                *quarters,
            )
        }
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "D05")

    assert len(result.periods) == 1
    ttm = result.periods[0]
    assert ttm.is_ttm is True
    assert ttm.diluted_shares == 100.0
    assert ttm.sources["diluted_shares"].period_end == ttm.period_end


def test_newer_quarters_replace_stale_direct_ttm_with_coherent_period_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quarters = ("2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31")
    ticker = FakeTicker(
        cashflow={
            "quarterly": statement(
                {"Operating Cash Flow": [40.0, 30.0, 20.0, 10.0]},
                *quarters,
            )
        },
        income={
            "trailing": statement({"Revenue": 600.0}, "2025-06-30"),
            "quarterly": statement(
                {"Revenue": [400.0, 300.0, 200.0, 100.0]},
                *quarters,
            ),
        },
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "D05")

    assert len(result.periods) == 1
    ttm = result.periods[0]
    assert ttm.period_end.isoformat() == "2025-09-30"
    assert ttm.operating_cash_flow == 100.0
    assert ttm.revenue == 1_000.0
    assert ttm.sources["operating_cash_flow"].period_end == ttm.period_end
    assert ttm.sources["revenue"].period_end == ttm.period_end


def test_non_finite_values_remain_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    ticker = FakeTicker(
        cashflow={
            "yearly": statement(
                {
                    "Operating Cash Flow": float("nan"),
                    "Capital Expenditure": float("inf"),
                },
                "2025-12-31",
            )
        }
    )
    install_ticker(monkeypatch, ticker)

    period = fetch_yfinance_fundamentals("SGX", "D05").periods[0]

    assert period.operating_cash_flow is None
    assert period.capital_expenditure is None


def test_provider_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingTicker(FakeTicker):
        def get_cashflow(self, freq: str) -> pd.DataFrame:
            if freq == "yearly":
                raise RuntimeError("Yahoo unavailable")
            return super().get_cashflow(freq)

    install_ticker(monkeypatch, FailingTicker())

    with pytest.raises(YFinanceStatementsError, match="D05.SI") as exc_info:
        fetch_yfinance_fundamentals("SGX", "D05")

    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_mismatched_statement_currency_warns_and_drops_affected_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        cashflow={
            "yearly": statement(
                {
                    "Operating Cash Flow": 100.0,
                    "Capital Expenditure": -20.0,
                    "Stock Based Compensation": 5.0,
                },
                "2025-12-31",
                currency="USD",
            )
        },
        income={
            "yearly": statement(
                {"Total Revenue": 500.0}, "2025-12-31", currency="SGD"
            )
        },
        info={"financialCurrency": "SGD"},
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "D05")

    period = result.periods[0]
    assert period.revenue == 500.0
    assert period.operating_cash_flow is None
    assert period.capital_expenditure is None
    assert period.stock_based_compensation is None
    assert "operating_cash_flow" in result.missing_fields
    assert "capital_expenditure" in result.missing_fields
    assert any("USD" in warning and "SGD" in warning for warning in result.warnings)


def test_duplicate_normalized_periods_select_latest_amendment_without_summing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    older = pd.Timestamp("2025-12-31 08:00:00")
    amended = pd.Timestamp("2025-12-31 17:00:00")
    ticker = FakeTicker(
        cashflow={
            "yearly": statement(
                {"Operating Cash Flow": [100.0, 150.0]}, older, amended
            )
        }
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "D05")

    assert len(result.periods) == 1
    assert result.periods[0].operating_cash_flow == 150.0


def test_non_sgx_uses_fallback_source_and_shared_public_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        income={"yearly": statement({"Revenue": 1.0}, "2025-12-31")},
        info={"financialCurrency": "USD"},
        fast_info={"currency": "USD"},
    )
    symbols = install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("nasdaq", "msft")

    assert symbols == ["MSFT"]
    assert result.symbol == "NASDAQ:MSFT"
    assert result.exchange == "NASDAQ"
    assert result.primary_source == "yfinance_fallback"
