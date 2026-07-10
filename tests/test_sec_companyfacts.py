from __future__ import annotations

from threading import Event, Thread
from typing import Any

import pytest

from app.core.config import Settings


class FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self.payload


class FakeClient:
    def __init__(
        self,
        responses: dict[str, FakeResponse],
        calls: list[str],
        **kwargs: Any,
    ) -> None:
        self.responses = responses
        self.calls = calls

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str) -> FakeResponse:
        self.calls.append(url)
        return self.responses[url]


def install_http(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, FakeResponse],
) -> list[str]:
    from app.services import sec_companyfacts

    calls: list[str] = []
    monkeypatch.setattr(
        sec_companyfacts.httpx,
        "Client",
        lambda **kwargs: FakeClient(responses, calls, **kwargs),
    )
    return calls


def sec_fact(
    value: float,
    *,
    end: str = "2024-12-31",
    start: str | None = "2024-01-01",
    form: str = "10-K",
    filed: str = "2025-02-01",
    accession: str = "0000000001-25-000001",
    fiscal_year: int = 2024,
    fiscal_period: str = "FY",
    frame: str | None = "CY2024",
) -> dict[str, object]:
    result: dict[str, object] = {
        "end": end,
        "val": value,
        "accn": accession,
        "fy": fiscal_year,
        "fp": fiscal_period,
        "form": form,
        "filed": filed,
    }
    if start is not None:
        result["start"] = start
    if frame is not None:
        result["frame"] = frame
    return result


def company_facts(
    concepts: dict[str, dict[str, list[dict[str, object]]]],
) -> dict[str, object]:
    return {
        "cik": 320193,
        "entityName": "Example Corp",
        "facts": {
            "us-gaap": {
                concept: {"units": units}
                for concept, units in concepts.items()
            }
        },
    }


def submissions() -> dict[str, object]:
    return {
        "cik": "0000320193",
        "name": "Example Corp",
        "entityType": "operating",
        "sic": "3571",
        "sicDescription": "Electronic Computers",
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
        "filings": {"recent": {}},
    }


def install_fetch_payloads(
    monkeypatch: pytest.MonkeyPatch,
    facts: dict[str, object],
    *,
    submission_payload: dict[str, object] | None = None,
) -> list[str]:
    from app.services import sec_companyfacts

    sec_companyfacts._clear_cache()
    monkeypatch.setattr(
        sec_companyfacts,
        "get_settings",
        lambda: Settings(sec_user_agent="stock-api test@example.com"),
    )
    cik = "0000320193"
    return install_http(
        monkeypatch,
        {
            sec_companyfacts.TICKERS_URL: FakeResponse(
                {"0": {"cik_str": 320193, "ticker": "AAPL"}}
            ),
            sec_companyfacts.COMPANY_FACTS_URL.format(cik=cik): FakeResponse(
                facts
            ),
            sec_companyfacts.SUBMISSIONS_URL.format(cik=cik): FakeResponse(
                submission_payload or submissions()
            ),
        },
    )


def test_settings_exposes_sec_user_agent() -> None:
    settings = Settings(sec_user_agent="stock-api test@example.com")

    assert settings.sec_user_agent == "stock-api test@example.com"


def test_resolve_cik_zero_pads_company_ticker_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    sec_companyfacts._clear_cache()
    monkeypatch.setattr(
        sec_companyfacts,
        "get_settings",
        lambda: Settings(sec_user_agent="stock-api test@example.com"),
    )
    calls = install_http(
        monkeypatch,
        {
            sec_companyfacts.TICKERS_URL: FakeResponse(
                {"0": {"cik_str": 320193, "ticker": "AAPL"}}
            )
        },
    )

    assert sec_companyfacts.resolve_cik(" aapl ") == "0000320193"
    assert calls == [sec_companyfacts.TICKERS_URL]


def test_resolve_cik_raises_typed_not_found_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    sec_companyfacts._clear_cache()
    monkeypatch.setattr(
        sec_companyfacts,
        "get_settings",
        lambda: Settings(sec_user_agent="stock-api test@example.com"),
    )
    install_http(
        monkeypatch,
        {
            sec_companyfacts.TICKERS_URL: FakeResponse(
                {"0": {"cik_str": 320193, "ticker": "AAPL"}}
            )
        },
    )

    with pytest.raises(sec_companyfacts.SecCompanyFactsError) as exc_info:
        sec_companyfacts.resolve_cik("MISSING")

    assert exc_info.value.status_code == 404


def test_sec_requests_require_a_configured_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    sec_companyfacts._clear_cache()
    monkeypatch.setattr(
        sec_companyfacts,
        "get_settings",
        lambda: Settings(sec_user_agent=None),
    )
    monkeypatch.setattr(
        sec_companyfacts.httpx,
        "Client",
        lambda **kwargs: pytest.fail("HTTP must not run without a User-Agent"),
    )

    with pytest.raises(
        sec_companyfacts.SecCompanyFactsError,
        match="STOCK_API_SEC_USER_AGENT",
    ) as exc_info:
        sec_companyfacts.resolve_cik("AAPL")

    assert exc_info.value.status_code == 502


def test_sec_client_uses_required_timeout_and_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    sec_companyfacts._clear_cache()
    monkeypatch.setattr(
        sec_companyfacts,
        "get_settings",
        lambda: Settings(sec_user_agent="stock-api test@example.com"),
    )
    options: list[dict[str, object]] = []

    def factory(**kwargs: object) -> FakeClient:
        options.append(dict(kwargs))
        return FakeClient(
            {
                sec_companyfacts.TICKERS_URL: FakeResponse(
                    {"0": {"cik_str": 320193, "ticker": "AAPL"}}
                )
            },
            [],
        )

    monkeypatch.setattr(sec_companyfacts.httpx, "Client", factory)

    assert sec_companyfacts.resolve_cik("AAPL") == "0000320193"
    assert options == [
        {
            "timeout": 20.0,
            "headers": {"User-Agent": "stock-api test@example.com"},
        }
    ]


def test_ticker_cache_does_not_hold_its_lock_during_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    sec_companyfacts._clear_cache()
    monkeypatch.setattr(
        sec_companyfacts,
        "get_settings",
        lambda: Settings(sec_user_agent="stock-api test@example.com"),
    )
    request_started = Event()
    allow_request = Event()

    def request(url: str) -> dict[str, dict[str, object]]:
        assert url == sec_companyfacts.TICKERS_URL
        request_started.set()
        assert allow_request.wait(timeout=1)
        return {"0": {"cik_str": 320193, "ticker": "AAPL"}}

    monkeypatch.setattr(sec_companyfacts, "_request_json", request)
    result: list[str] = []
    worker = Thread(target=lambda: result.append(sec_companyfacts.resolve_cik("AAPL")))
    worker.start()
    assert request_started.wait(timeout=1)

    assert sec_companyfacts._CACHE_LOCK.acquire(blocking=False)
    sec_companyfacts._CACHE_LOCK.release()
    allow_request.set()
    worker.join(timeout=1)

    assert result == ["0000320193"]


def test_ticker_mapping_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import sec_companyfacts

    sec_companyfacts._clear_cache()
    monkeypatch.setattr(
        sec_companyfacts,
        "get_settings",
        lambda: Settings(sec_user_agent="stock-api test@example.com"),
    )
    calls = install_http(
        monkeypatch,
        {
            sec_companyfacts.TICKERS_URL: FakeResponse(
                {"0": {"cik_str": 320193, "ticker": "AAPL"}}
            )
        },
    )

    assert sec_companyfacts.resolve_cik("AAPL") == "0000320193"
    assert sec_companyfacts.resolve_cik("AAPL") == "0000320193"
    assert calls == [sec_companyfacts.TICKERS_URL]


def test_selects_latest_compatible_amendment_without_summing_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    operating_facts = [
        sec_fact(100, filed="2025-02-01", accession="original"),
        sec_fact(100, filed="2025-02-01", accession="original"),
        sec_fact(
            120,
            form="10-K/A",
            filed="2025-03-01",
            accession="compatible-amendment",
        ),
        sec_fact(
            999,
            start="2024-04-01",
            form="10-K/A",
            filed="2025-04-01",
            accession="incompatible-amendment",
        ),
        sec_fact(777, form="8-K", filed="2025-05-01", accession="wrong-form"),
    ]
    facts = company_facts(
        {
            "NetCashProvidedByUsedInOperatingActivities": {
                "USD": operating_facts,
                "shares": [sec_fact(888, filed="2025-06-01")],
            },
            "Revenues": {"USD": [sec_fact(500)]},
        }
    )
    calls = install_fetch_payloads(monkeypatch, facts)

    result = sec_companyfacts.fetch_sec_fundamentals("nasdaq", "aapl")

    assert calls == [
        sec_companyfacts.TICKERS_URL,
        sec_companyfacts.COMPANY_FACTS_URL.format(cik="0000320193"),
        sec_companyfacts.SUBMISSIONS_URL.format(cik="0000320193"),
    ]
    assert result.symbol == "NASDAQ:AAPL"
    assert result.exchange == "NASDAQ"
    assert result.currency == "USD"
    assert result.primary_source == "sec_companyfacts"
    assert len(result.periods) == 1
    period = result.periods[0]
    assert period.operating_cash_flow == 120.0
    source = period.sources["operating_cash_flow"]
    assert source.provider == "sec_companyfacts"
    assert source.concept == "NetCashProvidedByUsedInOperatingActivities"
    assert source.form == "10-K/A"
    assert source.accession == "compatible-amendment"
    assert source.period_end.isoformat() == "2024-12-31"
    assert source.filed_at.isoformat() == "2025-03-01"
    assert source.unit == "USD"


@pytest.mark.parametrize(
    ("field", "concept", "unit", "value", "expected"),
    [
        (
            "operating_cash_flow",
            "NetCashProvidedByUsedInOperatingActivities",
            "USD",
            101,
            101,
        ),
        (
            "operating_cash_flow",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "USD",
            102,
            102,
        ),
        (
            "capital_expenditure",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "USD",
            21,
            21,
        ),
        (
            "capital_expenditure",
            "PaymentsForAdditionsToPropertyPlantAndEquipment",
            "USD",
            22,
            22,
        ),
        ("stock_based_compensation", "ShareBasedCompensation", "USD", 11, 11),
        (
            "stock_based_compensation",
            "AllocatedShareBasedCompensationExpense",
            "USD",
            12,
            12,
        ),
        ("interest_paid_outside_operating", "InterestPaidNet", "USD", 7, 0),
        ("interest_paid_outside_operating", "InterestPaid", "USD", 8, 0),
        (
            "revenue",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "USD",
            501,
            501,
        ),
        ("revenue", "Revenues", "USD", 502, 502),
        (
            "net_income_common",
            "NetIncomeLossAvailableToCommonStockholdersBasic",
            "USD",
            51,
            51,
        ),
        ("net_income_common", "NetIncomeLoss", "USD", 52, 52),
        ("common_equity", "StockholdersEquity", "USD", 301, 301),
        ("common_equity", "CommonStockholdersEquity", "USD", 302, 302),
        (
            "cash_and_equivalents",
            "CashAndCashEquivalentsAtCarryingValue",
            "USD",
            201,
            201,
        ),
        (
            "cash_and_equivalents",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "USD",
            202,
            202,
        ),
        ("total_assets", "Assets", "USD", 901, 901),
        (
            "total_debt",
            "LongTermDebtAndFinanceLeaseObligationsCurrentAndNoncurrent",
            "USD",
            101,
            101,
        ),
        (
            "total_debt",
            "LongTermDebtAndCapitalLeaseObligationsCurrentAndNoncurrent",
            "USD",
            102,
            102,
        ),
        ("total_debt", "LongTermDebt", "USD", 103, 103),
        (
            "diluted_shares",
            "WeightedAverageNumberOfDilutedSharesOutstanding",
            "shares",
            41,
            41,
        ),
        (
            "diluted_shares",
            "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
            "shares",
            42,
            42,
        ),
        ("common_dividends", "PaymentsOfDividendsCommonStock", "USD", 31, 31),
        ("common_dividends", "PaymentsOfDividends", "USD", 32, 32),
    ],
)
def test_normalizes_reviewed_sec_concept_alternatives(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    concept: str,
    unit: str,
    value: float,
    expected: float,
) -> None:
    from app.services import sec_companyfacts

    instant_fields = {
        "common_equity",
        "cash_and_equivalents",
        "total_assets",
        "total_debt",
    }
    concept_fact = sec_fact(
        value,
        start=None if field in instant_fields else "2024-01-01",
    )
    concepts = {concept: {unit: [concept_fact]}}
    if field != "revenue":
        concepts["Revenues"] = {"USD": [sec_fact(500)]}
    install_fetch_payloads(monkeypatch, company_facts(concepts))

    period = sec_companyfacts.fetch_sec_fundamentals("NASDAQ", "AAPL").periods[0]

    assert getattr(period, field) == float(expected)
    assert period.sources[field].concept == concept
    assert period.sources[field].unit == unit


def test_uses_ordered_concept_priority_before_newer_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    facts = company_facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "USD": [sec_fact(500, filed="2025-02-01")]
            },
            "Revenues": {
                "USD": [sec_fact(999, filed="2025-04-01")]
            },
        }
    )
    install_fetch_payloads(monkeypatch, facts)

    period = sec_companyfacts.fetch_sec_fundamentals("NASDAQ", "AAPL").periods[0]

    assert period.revenue == 500.0
    assert (
        period.sources["revenue"].concept
        == "RevenueFromContractWithCustomerExcludingAssessedTax"
    )


def test_prefers_common_equity_over_broad_stockholders_equity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    facts = company_facts(
        {
            "CommonStockholdersEquity": {
                "USD": [sec_fact(700, start=None)]
            },
            "StockholdersEquity": {
                "USD": [sec_fact(1_000, start=None)]
            },
            "Revenues": {"USD": [sec_fact(500)]},
        }
    )
    install_fetch_payloads(monkeypatch, facts)

    period = sec_companyfacts.fetch_sec_fundamentals("NASDAQ", "AAPL").periods[0]

    assert period.common_equity == 700.0
    assert period.sources["common_equity"].concept == "CommonStockholdersEquity"


def test_returns_five_annual_periods_and_latest_four_compatible_quarters_as_ttm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    annual_revenue = [
        sec_fact(
            year * 10,
            start=f"{year}-01-01",
            end=f"{year}-12-31",
            filed=f"{year + 1}-02-01",
            accession=f"annual-{year}",
            fiscal_year=year,
            frame=f"CY{year}",
        )
        for year in range(2020, 2026)
    ]
    quarter_specs = [
        ("2025-10-01", "2025-12-31", "CY2025Q4", "10-K", 10, 10),
        ("2026-01-01", "2026-03-31", "CY2026Q1", "10-Q", 20, 11),
        ("2026-04-01", "2026-06-30", "CY2026Q2", "10-Q", 30, 12),
        ("2026-07-01", "2026-09-30", "CY2026Q3", "10-Q", 40, 13),
    ]
    quarterly_revenue = [
        sec_fact(
            revenue,
            start=start,
            end=end,
            form=form,
            filed=end,
            accession=f"quarter-{frame}",
            fiscal_year=int(end[:4]),
            fiscal_period=frame[-2:],
            frame=frame,
        )
        for start, end, frame, form, revenue, _shares in quarter_specs
    ]
    quarterly_shares = [
        sec_fact(
            shares,
            start=start,
            end=end,
            form=form,
            filed=end,
            accession=f"shares-{frame}",
            fiscal_year=int(end[:4]),
            fiscal_period=frame[-2:],
            frame=frame,
        )
        for start, end, frame, form, _revenue, shares in quarter_specs
    ]
    facts = company_facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "USD": [*annual_revenue, *quarterly_revenue]
            },
            "WeightedAverageNumberOfDilutedSharesOutstanding": {
                "shares": quarterly_shares
            },
        }
    )
    install_fetch_payloads(monkeypatch, facts)

    result = sec_companyfacts.fetch_sec_fundamentals("NASDAQ", "AAPL")

    annuals = [period for period in result.periods if not period.is_ttm]
    trailing = [period for period in result.periods if period.is_ttm]
    assert [period.fiscal_year for period in annuals] == [2021, 2022, 2023, 2024, 2025]
    assert len(trailing) == 1
    ttm = trailing[0]
    assert ttm.period_end.isoformat() == "2026-09-30"
    assert ttm.revenue == 100.0
    assert ttm.diluted_shares == 13.0
    assert (
        ttm.sources["revenue"].concept
        == "RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    assert ttm.sources["revenue"].accession == "quarter-CY2026Q3"
    assert result.current_diluted_shares == 13.0


def test_does_not_build_ttm_from_nonconsecutive_quarter_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    quarter_frames = ("CY2025Q1", "CY2025Q2", "CY2025Q4", "CY2026Q1")
    quarterly = [
        sec_fact(
            10,
            start=f"{frame[2:6]}-01-01",
            end=f"{frame[2:6]}-03-31",
            form="10-Q",
            filed=f"{frame[2:6]}-04-15",
            accession=frame,
            fiscal_year=int(frame[2:6]),
            fiscal_period=frame[-2:],
            frame=frame,
        )
        for frame in quarter_frames
    ]
    facts = company_facts(
        {
            "Revenues": {
                "USD": [sec_fact(500), *quarterly]
            }
        }
    )
    install_fetch_payloads(monkeypatch, facts)

    result = sec_companyfacts.fetch_sec_fundamentals("NASDAQ", "AAPL")

    assert all(not period.is_ttm for period in result.periods)


def test_does_not_union_sparse_quarters_across_unrelated_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    revenue_quarters = [
        sec_fact(
            10,
            start="2025-01-01",
            end="2025-03-31",
            form="10-Q",
            filed="2025-04-30",
            accession="revenue-q1",
            fiscal_year=2025,
            fiscal_period="Q1",
            frame="CY2025Q1",
        ),
        sec_fact(
            20,
            start="2025-04-01",
            end="2025-06-30",
            form="10-Q",
            filed="2025-07-31",
            accession="revenue-q2",
            fiscal_year=2025,
            fiscal_period="Q2",
            frame="CY2025Q2",
        ),
    ]
    operating_q3 = sec_fact(
        30,
        start="2025-07-01",
        end="2025-09-30",
        form="10-Q",
        filed="2025-10-31",
        accession="operating-q3",
        fiscal_year=2025,
        fiscal_period="Q3",
        frame="CY2025Q3",
    )
    equity_q4 = sec_fact(
        700,
        start=None,
        end="2025-12-31",
        form="10-K",
        filed="2026-02-01",
        accession="equity-q4",
        fiscal_year=2025,
        fiscal_period="Q4",
        frame="CY2025Q4I",
    )
    facts = company_facts(
        {
            "Revenues": {
                "USD": [sec_fact(500), *revenue_quarters]
            },
            "NetCashProvidedByUsedInOperatingActivities": {
                "USD": [operating_q3]
            },
            "CommonStockholdersEquity": {"USD": [equity_q4]},
        }
    )
    install_fetch_payloads(monkeypatch, facts)

    result = sec_companyfacts.fetch_sec_fundamentals("NASDAQ", "AAPL")

    assert all(not period.is_ttm for period in result.periods)


def test_preserves_missing_values_and_uses_submission_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    facts = company_facts({"Revenues": {"USD": [sec_fact(500)]}})
    install_fetch_payloads(monkeypatch, facts)

    result = sec_companyfacts.fetch_sec_fundamentals("NASDAQ", "AAPL")

    period = result.periods[0]
    assert period.operating_cash_flow is None
    assert period.capital_expenditure is None
    assert period.diluted_shares is None
    assert "operating_cash_flow" in result.missing_fields
    assert "capital_expenditure" in result.missing_fields
    assert "current_diluted_shares" in result.missing_fields
    assert result.provider_security_type == "operating"
    assert result.industry == "Electronic Computers"
    assert result.issuer_classification == "Electronic Computers (SIC 3571)"
    assert result.sources == {
        "financial_statements": "sec_companyfacts",
        "submissions_metadata": "sec_submissions",
    }


def test_provider_http_failure_is_wrapped_as_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import sec_companyfacts

    sec_companyfacts._clear_cache()
    monkeypatch.setattr(
        sec_companyfacts,
        "get_settings",
        lambda: Settings(sec_user_agent="stock-api test@example.com"),
    )
    install_http(
        monkeypatch,
        {sec_companyfacts.TICKERS_URL: FakeResponse({}, status_code=503)},
    )

    with pytest.raises(sec_companyfacts.SecCompanyFactsError) as exc_info:
        sec_companyfacts.resolve_cik("AAPL")

    assert exc_info.value.status_code == 502
    assert isinstance(exc_info.value.__cause__, RuntimeError)
