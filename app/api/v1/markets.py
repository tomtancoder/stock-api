from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query

from app.core.config import get_settings
from app.schemas import QuoteResponse, ValuationResponse
from app.services import tradingview_provider as provider
from app.services import valuation_service
from app.services.tradingview_provider import TradingViewProviderError

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("/{exchange}/{symbol}/quote", response_model=QuoteResponse)
def quote(
    exchange: str = Path(..., min_length=1, max_length=32),
    symbol: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    return _provider_response(provider.get_quote, exchange, symbol)


@router.get("/{exchange}/{symbol}/analysis")
def analysis(
    exchange: str = Path(..., min_length=1, max_length=32),
    symbol: str = Path(..., min_length=1, max_length=64),
    timeframe: str | None = Query(default=None, min_length=1, max_length=16),
) -> dict[str, Any]:
    return _provider_response(
        provider.get_analysis,
        exchange,
        symbol,
        timeframe or get_settings().default_timeframe,
    )


@router.get("/{exchange}/{symbol}/technical")
def technical(
    exchange: str = Path(..., min_length=1, max_length=32),
    symbol: str = Path(..., min_length=1, max_length=64),
    timeframe: str | None = Query(default=None, min_length=1, max_length=16),
    include_multi_timeframe: bool = Query(default=False),
) -> dict[str, Any]:
    return _provider_response(
        provider.get_technical_analysis,
        exchange,
        symbol,
        timeframe or get_settings().default_timeframe,
        include_multi_timeframe,
    )


@router.get(
    "/{exchange}/{symbol}/valuation",
    response_model=ValuationResponse,
)
def valuation(
    exchange: str = Path(..., min_length=1, max_length=32),
    symbol: str = Path(..., min_length=1, max_length=64),
) -> ValuationResponse:
    try:
        return valuation_service.get_valuation(exchange, symbol)
    except valuation_service.ValuationServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers=exc.headers,
        ) from exc


@router.get("/{exchange}/gainers")
def gainers(
    exchange: str = Path(..., min_length=1, max_length=32),
    timeframe: str | None = Query(default=None, min_length=1, max_length=16),
    limit: int = Query(default=25, ge=1, le=50),
) -> list[dict[str, Any]]:
    return _provider_response(
        provider.get_gainers,
        exchange,
        timeframe or get_settings().default_timeframe,
        limit,
    )


@router.get("/{exchange}/losers")
def losers(
    exchange: str = Path(..., min_length=1, max_length=32),
    timeframe: str | None = Query(default=None, min_length=1, max_length=16),
    limit: int = Query(default=25, ge=1, le=50),
) -> list[dict[str, Any]]:
    return _provider_response(
        provider.get_losers,
        exchange,
        timeframe or get_settings().default_timeframe,
        limit,
    )


@router.get("/{exchange}/bollinger-scan")
def bollinger_scan(
    exchange: str = Path(..., min_length=1, max_length=32),
    timeframe: str | None = Query(default=None, min_length=1, max_length=16),
    bbw_threshold: float = Query(default=0.04, gt=0),
    limit: int = Query(default=50, ge=1, le=50),
) -> list[dict[str, Any]]:
    return _provider_response(
        provider.get_bollinger_scan,
        exchange,
        timeframe or get_settings().default_timeframe,
        bbw_threshold,
        limit,
    )


@router.get("/{exchange}/rating-filter")
def rating_filter(
    exchange: str = Path(..., min_length=1, max_length=32),
    timeframe: str | None = Query(default=None, min_length=1, max_length=16),
    rating: int = Query(default=2, ge=-3, le=3),
    limit: int = Query(default=25, ge=1, le=50),
) -> list[dict[str, Any]]:
    return _provider_response(
        provider.get_rating_filter,
        exchange,
        timeframe or get_settings().default_timeframe,
        rating,
        limit,
    )


def _provider_response(func, *args):
    try:
        return func(*args)
    except TradingViewProviderError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
            headers=exc.headers,
        ) from exc
