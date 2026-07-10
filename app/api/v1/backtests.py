from typing import Any

from fastapi import APIRouter, HTTPException, Path

from app.schemas import BacktestRequest, CompareStrategiesRequest, WalkForwardBacktestRequest
from app.services import tradingview_provider as provider
from app.services.tradingview_provider import TradingViewProviderError

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("/{exchange}/{symbol}")
def run_backtest(
    request: BacktestRequest,
    exchange: str = Path(..., min_length=1, max_length=32),
    symbol: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    return _provider_response(provider.run_backtest, exchange, symbol, request)


@router.post("/{exchange}/{symbol}/compare")
def compare_strategies(
    request: CompareStrategiesRequest,
    exchange: str = Path(..., min_length=1, max_length=32),
    symbol: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    return _provider_response(provider.compare_strategies, exchange, symbol, request)


@router.post("/{exchange}/{symbol}/walk-forward")
def walk_forward_backtest(
    request: WalkForwardBacktestRequest,
    exchange: str = Path(..., min_length=1, max_length=32),
    symbol: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    return _provider_response(provider.walk_forward_backtest, exchange, symbol, request)


def _provider_response(func, *args):
    try:
        return func(*args)
    except TradingViewProviderError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
            headers=exc.headers,
        ) from exc
