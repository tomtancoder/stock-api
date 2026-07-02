from fastapi import APIRouter, HTTPException, Path, Query

from app.schemas import (
    FundamentalsResponse,
    QuoteResponse,
    TechnicalsResponse,
    ValuationRequest,
    ValuationResponse,
)
from app.services import yfinance_client
from app.services.technicals import get_stock_technicals
from app.services.valuation import build_valuation
from app.services.yfinance_client import YFinanceError

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/{symbol}/quote", response_model=QuoteResponse)
def quote(symbol: str = Path(..., min_length=1, max_length=32)) -> QuoteResponse:
    try:
        return yfinance_client.get_stock_quote(symbol)
    except YFinanceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{symbol}/fundamentals", response_model=FundamentalsResponse)
def fundamentals(
    symbol: str = Path(..., min_length=1, max_length=32),
) -> FundamentalsResponse:
    snapshot = _load_snapshot(symbol)
    return FundamentalsResponse(
        symbol=snapshot.symbol,
        currency=snapshot.quote.currency,
        financials=snapshot.financials,
        shares_outstanding=snapshot.quote.shares_outstanding,
        warnings=snapshot.warnings,
    )


@router.get("/{symbol}/technicals", response_model=TechnicalsResponse)
def technicals(
    symbol: str = Path(..., min_length=1, max_length=32),
    period: str = Query(default="1y", min_length=1, max_length=16),
    interval: str = Query(default="1d", min_length=1, max_length=16),
) -> TechnicalsResponse:
    try:
        return get_stock_technicals(symbol=symbol, period=period, interval=interval)
    except YFinanceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{symbol}/valuation", response_model=ValuationResponse)
def valuation(
    request: ValuationRequest | None = None,
    symbol: str = Path(..., min_length=1, max_length=32),
) -> ValuationResponse:
    snapshot = _load_snapshot(symbol)
    assumptions = request or ValuationRequest()
    return build_valuation(snapshot=snapshot, overrides=assumptions)


def _load_snapshot(symbol: str):
    try:
        return yfinance_client.get_stock_snapshot(symbol)
    except YFinanceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
