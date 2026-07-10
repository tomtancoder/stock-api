from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import tradingview_provider as provider
from app.services.tradingview_provider import TradingViewProviderError

router = APIRouter(tags=["intelligence"])


@router.get("/sentiment/{symbol}")
def sentiment(
    symbol: str,
    category: str = Query(default="all", min_length=1, max_length=32),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return _provider_response(provider.get_sentiment, symbol, category, limit)


@router.get("/news")
def news(
    symbol: str | None = Query(default=None, min_length=1, max_length=64),
    category: str = Query(default="stocks", min_length=1, max_length=32),
    limit: int = Query(default=10, ge=1, le=100),
) -> dict[str, Any]:
    return _provider_response(provider.get_news, symbol, category, limit)


def _provider_response(func, *args):
    try:
        return func(*args)
    except TradingViewProviderError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
            headers=exc.headers,
        ) from exc
