from __future__ import annotations

from collections.abc import Mapping
from datetime import date

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
        dividends: pd.Series | None = None,
    ) -> None:
        self.cashflow = dict(cashflow or {})
        self.income = dict(income or {})
        self.balance = dict(balance or {})
        self.info = dict(info or {"financialCurrency": "SGD"})
        self.fast_info = dict(fast_info or {"currency": "SGD"})
        self.shares = shares if shares is not None else pd.Series(dtype=float)
        self._dividends = (
            dividends if dividends is not None else pd.Series(dtype=float)
        )
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

    @property
    def dividends(self) -> pd.Series:
        self.calls.append(("dividends", None))
        return self._dividends


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
    assert result.sources["current_diluted_shares"] == "yfinance"
    assert {
        period.sources["diluted_shares"].unit
        for period in result.periods
        if "diluted_shares" in period.sources
    } == {"shares"}
    assert {
        period.sources["revenue"].unit
        for period in result.periods
        if "revenue" in period.sources
    } == {"SGD"}
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


def test_reit_dividends_group_by_fiscal_year_and_derive_compatible_nav(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fiscal_periods = ("2026-06-30", "2025-06-30", "2024-06-30")
    ticker = FakeTicker(
        balance={
            "yearly": statement(
                {
                    "Unitholder Equity": [1_100.0, 1_050.0, 1_000.0],
                    "Total Assets": [3_000.0, 2_800.0, 2_600.0],
                    "Total Debt": [1_050.0, 980.0, 910.0],
                },
                *fiscal_periods,
                currency="SGD",
            )
        },
        info={
            "financialCurrency": "SGD",
            "quoteType": "REIT",
            "industry": "REIT - Retail",
            "fiscalYearEnd": "06-30",
            "aggregateLeverage": 0.42,
        },
        shares=pd.Series(
            [1_000.0, 1_050.0, 1_100.0],
            index=["2024-06-30", "2025-06-30", "2026-06-30"],
        ),
        dividends=pd.Series(
            [
                0.01,
                0.01,
                0.01,
                0.01,
                0.0125,
                0.0125,
                0.0125,
                0.0125,
                0.015,
                0.015,
                0.015,
                0.015,
            ],
            index=pd.to_datetime(
                [
                    "2023-09-15",
                    "2023-12-15",
                    "2024-03-15",
                    "2024-06-15",
                    "2024-09-15",
                    "2024-12-15",
                    "2025-03-15",
                    "2025-06-15",
                    "2025-09-15",
                    "2025-12-15",
                    "2026-03-15",
                    "2026-06-15",
                ],
                utc=True,
            ),
        ),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")

    annuals = {period.period_end: period for period in result.periods if not period.is_ttm}
    assert annuals[pd.Timestamp("2024-06-30").date()].distribution_per_unit == pytest.approx(0.04)
    assert annuals[pd.Timestamp("2025-06-30").date()].distribution_per_unit == pytest.approx(0.05)
    assert annuals[pd.Timestamp("2026-06-30").date()].distribution_per_unit == pytest.approx(0.06)
    assert annuals[pd.Timestamp("2026-06-30").date()].nav_per_unit == pytest.approx(1.0)
    assert annuals[pd.Timestamp("2026-06-30").date()].diluted_shares == 1_100.0
    trailing = [period for period in result.periods if period.is_ttm]
    assert len(trailing) == 1
    assert trailing[0].distribution_per_unit == pytest.approx(0.06)
    assert trailing[0].currency == "SGD"
    assert trailing[0].sources["distribution_per_unit"].concept == "Ticker.dividends"
    assert trailing[0].sources["distribution_per_unit"].unit == "SGD/unit"
    latest = annuals[pd.Timestamp("2026-06-30").date()]
    assert latest.sources["common_equity"].concept == "Unitholder Equity"
    assert latest.sources["common_equity"].unit == "SGD"
    assert latest.sources["diluted_shares"].concept == "get_shares_full"
    assert latest.sources["diluted_shares"].unit == "units"
    assert latest.sources["nav_per_unit"].concept == "derived_nav_per_unit"
    assert latest.sources["nav_per_unit"].unit == "SGD/unit"
    assert result.reit_metrics == {"aggregate_leverage": 0.42}
    assert result.sources["aggregate_leverage"] == "yfinance_info.aggregateLeverage"
    assert not any("derived aggregate leverage" in warning.lower() for warning in result.warnings)
    assert ("dividends", None) in ticker.calls


def test_reit_calendar_grouping_warns_and_derived_leverage_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        balance={
            "yearly": statement(
                {
                    "Unitholder Equity": 1_000.0,
                    "Total Assets": 2_000.0,
                    "Total Debt": 700.0,
                },
                "2025-12-31",
            )
        },
        info={
            "financialCurrency": "SGD",
            "industry": "Real Estate Investment Trust",
        },
        shares=pd.Series([1_000.0], index=["2025-12-31"]),
        dividends=pd.Series(
            [0.02, 0.02, 0.02, 0.02],
            index=pd.to_datetime(
                ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
            ),
        ),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")

    annual = next(period for period in result.periods if not period.is_ttm)
    assert annual.distribution_per_unit == pytest.approx(0.08)
    assert result.reit_metrics == {"aggregate_leverage": pytest.approx(0.35)}
    assert result.sources["aggregate_leverage"] == "derived_aggregate_leverage"
    assert any("calendar year" in warning.lower() for warning in result.warnings)
    assert any("derived aggregate leverage" in warning.lower() for warning in result.warnings)


def test_reit_issuer_dpu_and_nav_are_not_overwritten_by_derived_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        cashflow={
            "yearly": statement({"Distribution Per Unit": 0.09}, "2025-12-31")
        },
        balance={
            "yearly": statement(
                {"Unitholder Equity": 1_000.0, "NAV Per Unit": 1.25},
                "2025-12-31",
            )
        },
        info={
            "financialCurrency": "SGD",
            "quoteType": "PROPERTYTRUST",
            "fiscalYearEnd": "12-31",
        },
        shares=pd.Series([1_000.0], index=["2025-12-31"]),
        dividends=pd.Series(
            [0.01, 0.01, 0.01, 0.01],
            index=pd.to_datetime(
                ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
            ),
        ),
    )
    install_ticker(monkeypatch, ticker)

    annual = next(
        period
        for period in fetch_yfinance_fundamentals("SGX", "M44U").periods
        if not period.is_ttm
    )

    assert annual.distribution_per_unit == 0.09
    assert annual.sources["distribution_per_unit"].concept == "Distribution Per Unit"
    assert annual.nav_per_unit == 1.25
    assert annual.sources["nav_per_unit"].concept == "NAV Per Unit"


def test_reit_dividend_keeps_provider_local_date_at_sgt_year_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        info={
            "financialCurrency": "SGD",
            "quoteType": "REIT",
        },
        dividends=pd.Series(
            [0.02],
            index=[pd.Timestamp("2025-01-01 00:30", tz="Asia/Singapore")],
        ),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")

    annuals = [period for period in result.periods if not period.is_ttm]
    assert [period.period_end.isoformat() for period in annuals] == [
        "2025-12-31"
    ]
    assert annuals[0].distribution_per_unit == 0.02


def test_reit_dividends_deduplicate_only_exact_date_amount_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    same_date = pd.Timestamp("2025-03-31", tz="Asia/Singapore")
    ticker = FakeTicker(
        info={"financialCurrency": "SGD", "quoteType": "REIT"},
        dividends=pd.Series(
            [0.01, 0.01, 0.02],
            index=[same_date, same_date, same_date],
        ),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")

    annual = next(period for period in result.periods if not period.is_ttm)
    trailing = next(period for period in result.periods if period.is_ttm)
    assert annual.distribution_per_unit == pytest.approx(0.03)
    assert trailing.distribution_per_unit == pytest.approx(0.03)


def test_reit_issuer_dpu_suppresses_same_fiscal_bucket_derived_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        cashflow={
            "yearly": statement(
                {"Distribution Per Unit": 0.09},
                "2025-12-28",
            )
        },
        info={
            "financialCurrency": "SGD",
            "quoteType": "REIT",
            "fiscalYearEnd": "12-31",
        },
        dividends=pd.Series(
            [0.01, 0.01, 0.01, 0.01],
            index=pd.to_datetime(
                ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-20"]
            ),
        ),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")

    annuals = [
        period
        for period in result.periods
        if not period.is_ttm and period.distribution_per_unit is not None
    ]
    assert len(annuals) == 1
    assert annuals[0].period_end.isoformat() == "2025-12-28"
    assert annuals[0].distribution_per_unit == 0.09
    assert (
        annuals[0].sources["distribution_per_unit"].concept
        == "Distribution Per Unit"
    )


@pytest.mark.parametrize("equity", [0.0, -100.0])
def test_reit_missing_compatible_positive_nav_stays_none(
    monkeypatch: pytest.MonkeyPatch, equity: float
) -> None:
    ticker = FakeTicker(
        balance={
            "yearly": statement({"Unitholder Equity": equity}, "2025-12-31")
        },
        info={"financialCurrency": "SGD", "quoteType": "REIT"},
        shares=pd.Series([1_000.0], index=["2025-12-31"]),
    )
    install_ticker(monkeypatch, ticker)

    annual = next(
        period
        for period in fetch_yfinance_fundamentals("SGX", "M44U").periods
        if not period.is_ttm
    )

    assert annual.nav_per_unit is None
    assert "nav_per_unit" not in annual.sources


def test_reit_replaces_nonpositive_statement_units_with_positive_unit_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        income={
            "yearly": statement({"Diluted Average Shares": 0.0}, "2025-12-31")
        },
        balance={
            "yearly": statement({"Unitholder Equity": 1_000.0}, "2025-12-31")
        },
        info={"financialCurrency": "SGD", "quoteType": "REIT"},
        shares=pd.Series([1_000.0], index=["2025-12-31"]),
    )
    install_ticker(monkeypatch, ticker)

    annual = next(
        period
        for period in fetch_yfinance_fundamentals("SGX", "M44U").periods
        if not period.is_ttm
    )

    assert annual.diluted_shares == 1_000.0
    assert annual.sources["diluted_shares"].concept == "get_shares_full"
    assert annual.nav_per_unit == pytest.approx(1.0)


def test_reit_dividend_only_periods_receive_compatible_unit_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        info={
            "financialCurrency": "SGD",
            "quoteType": "REIT",
            "fiscalYearEnd": "12-31",
        },
        shares=pd.Series(
            [990.0, 1_000.0],
            index=["2025-12-20", "2025-12-31"],
        ),
        dividends=pd.Series([0.02], index=pd.to_datetime(["2025-12-20"])),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")

    annual = next(period for period in result.periods if not period.is_ttm)
    trailing = next(period for period in result.periods if period.is_ttm)
    assert annual.diluted_shares == 1_000.0
    assert annual.sources["diluted_shares"].period_end.isoformat() == (
        "2025-12-31"
    )
    assert trailing.diluted_shares == 990.0
    assert trailing.sources["diluted_shares"].period_end.isoformat() == (
        "2025-12-20"
    )


@pytest.mark.parametrize(
    ("observation_date", "expected_units"),
    [
        ("2025-11-30", 1_000.0),
        ("2025-11-29", None),
    ],
)
def test_reit_unit_history_uses_a_31_day_maximum_age_boundary(
    monkeypatch: pytest.MonkeyPatch,
    observation_date: str,
    expected_units: float | None,
) -> None:
    ticker = FakeTicker(
        balance={
            "yearly": statement({"Unitholder Equity": 1_000.0}, "2025-12-31")
        },
        info={"financialCurrency": "SGD", "quoteType": "REIT"},
        shares=pd.Series([1_000.0], index=[observation_date]),
    )
    install_ticker(monkeypatch, ticker)

    annual = next(
        period
        for period in fetch_yfinance_fundamentals("SGX", "M44U").periods
        if not period.is_ttm
    )

    assert annual.diluted_shares == expected_units
    if expected_units is not None:
        assert annual.sources["diluted_shares"].period_end.isoformat() == (
            observation_date
        )
        assert annual.nav_per_unit is None
    else:
        assert "diluted_shares" not in annual.sources
        assert annual.nav_per_unit is None


def test_reit_stale_unit_history_cannot_populate_current_units_or_nav(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        balance={
            "yearly": statement({"Unitholder Equity": 1_000.0}, "2025-12-31")
        },
        info={"financialCurrency": "SGD", "quoteType": "REIT"},
        shares=pd.Series([1_000.0], index=["2020-12-31"]),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")
    annual = next(period for period in result.periods if not period.is_ttm)

    assert annual.diluted_shares is None
    assert "diluted_shares" not in annual.sources
    assert annual.nav_per_unit is None
    assert "nav_per_unit" not in annual.sources
    assert result.current_diluted_shares is None
    assert "current_diluted_shares" in result.missing_fields


def test_reit_nav_requires_unit_provenance_at_the_exact_financial_period_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        balance={
            "yearly": statement({"Unitholder Equity": 1_000.0}, "2025-12-31")
        },
        info={"financialCurrency": "SGD", "quoteType": "REIT"},
        shares=pd.Series([1_000.0], index=["2025-12-30"]),
    )
    install_ticker(monkeypatch, ticker)

    annual = next(
        period
        for period in fetch_yfinance_fundamentals("SGX", "M44U").periods
        if not period.is_ttm
    )

    assert annual.diluted_shares == 1_000.0
    assert annual.sources["diluted_shares"].period_end.isoformat() == (
        "2025-12-30"
    )
    assert annual.nav_per_unit is None
    assert "nav_per_unit" not in annual.sources


def test_non_reit_does_not_read_dividend_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        income={"yearly": statement({"Revenue": 1.0}, "2025-12-31")},
        info={"financialCurrency": "SGD", "quoteType": "EQUITY"},
    )
    install_ticker(monkeypatch, ticker)

    fetch_yfinance_fundamentals("SGX", "S63")

    assert ("dividends", None) not in ticker.calls


def test_complete_reit_has_no_owner_earnings_missing_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        cashflow={
            "yearly": statement({"Distribution Per Unit": 0.08}, "2025-12-31")
        },
        balance={
            "yearly": statement({"NAV Per Unit": 1.20}, "2025-12-31")
        },
        info={"financialCurrency": "SGD", "quoteType": "REIT"},
        shares=pd.Series([1_000.0], index=["2025-12-31"]),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")

    assert result.missing_fields == []


def test_reit_missing_nav_reports_only_the_reit_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        cashflow={
            "yearly": statement({"Distribution Per Unit": 0.08}, "2025-12-31")
        },
        info={"financialCurrency": "SGD", "quoteType": "REIT"},
        shares=pd.Series([1_000.0], index=["2025-12-31"]),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")

    assert result.missing_fields == ["nav_per_unit"]


def test_reit_trailing_dpu_excludes_the_exact_twelve_month_lower_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = FakeTicker(
        info={"financialCurrency": "SGD", "quoteType": "REIT"},
        dividends=pd.Series(
            [0.50, 0.02, 0.03],
            index=pd.to_datetime(
                ["2024-12-31", "2025-01-01", "2025-12-31"]
            ),
        ),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")

    trailing = next(period for period in result.periods if period.is_ttm)
    assert trailing.distribution_per_unit == pytest.approx(0.05)


def test_reit_partial_current_fiscal_bucket_stays_trailing_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        yfinance_statements,
        "_current_date",
        lambda: date(2026, 7, 10),
        raising=False,
    )
    ticker = FakeTicker(
        info={
            "financialCurrency": "SGD",
            "quoteType": "REIT",
            "fiscalYearEnd": "12-31",
        },
        dividends=pd.Series(
            [0.02, 0.03],
            index=pd.to_datetime(["2025-03-31", "2026-03-31"]),
        ),
    )
    install_ticker(monkeypatch, ticker)

    result = fetch_yfinance_fundamentals("SGX", "M44U")

    annuals = [
        period
        for period in result.periods
        if not period.is_ttm and period.distribution_per_unit is not None
    ]
    assert [period.period_end.isoformat() for period in annuals] == [
        "2025-12-31"
    ]
    assert annuals[0].distribution_per_unit == 0.02
    trailing = next(period for period in result.periods if period.is_ttm)
    assert trailing.period_end.isoformat() == "2026-03-31"
    assert trailing.distribution_per_unit == 0.03
    assert max(period.period_end for period in result.periods) <= date(
        2026, 7, 10
    )
