from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query

from app.core.config import get_settings
from app.schemas import QuoteResponse, TradeScoreResponse
from app.services import tradingview_provider as provider
from app.services.tradingview_provider import TradingViewProviderError

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/{symbol}/quote", response_model=QuoteResponse)
def quote(
    symbol: str = Path(..., min_length=1, max_length=64),
    exchange: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict[str, Any]:
    return _provider_response(provider.get_quote, exchange or get_settings().default_exchange, symbol)


@router.get("/{symbol}/fundamentals")
def fundamentals(symbol: str = Path(..., min_length=1, max_length=64)) -> None:
    raise HTTPException(
        status_code=501,
        detail=(
            "Fundamentals are not supported by the TradingView MCP provider. "
            "Use /api/v1/markets/{exchange}/{symbol}/analysis or /score for trading signals."
        ),
    )


@router.get("/{symbol}/technicals")
def technicals(
    symbol: str = Path(..., min_length=1, max_length=64),
    exchange: str | None = Query(default=None, min_length=1, max_length=32),
    timeframe: str | None = Query(default=None, min_length=1, max_length=16),
) -> dict[str, Any]:
    return _provider_response(
        provider.get_analysis,
        exchange or get_settings().default_exchange,
        symbol,
        timeframe or get_settings().default_timeframe,
    )


@router.post("/{symbol}/valuation", response_model=TradeScoreResponse)
def valuation(
    symbol: str = Path(..., min_length=1, max_length=64),
    exchange: str | None = Query(default=None, min_length=1, max_length=32),
    timeframe: str | None = Query(default=None, min_length=1, max_length=16),
) -> dict[str, Any]:
    return _provider_response(
        provider.get_trade_score,
        exchange or get_settings().default_exchange,
        symbol,
        timeframe or get_settings().default_timeframe,
    )


def _provider_response(func, *args):
    try:
        return func(*args)
    except TradingViewProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
