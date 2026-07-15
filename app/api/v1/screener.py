from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    BreakoutRating,
    BreakoutScreenerResponse,
    BreakoutSetupState,
)
from app.services import screener as screener_service
from app.services.market_data import MarketDataError


router = APIRouter(prefix="/screener", tags=["screener"])


@router.get("/breakouts", response_model=BreakoutScreenerResponse)
def breakouts(
    market: Literal["all", "us", "sg"] = Query(default="all"),
    minimum_score: int = Query(default=0, ge=0, le=18),
    rating: BreakoutRating | None = Query(default=None),
    setup_state: BreakoutSetupState | None = Query(default=None),
    maximum_extension_atr: float | None = Query(default=None, ge=0),
    include_four_hour: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=500),
) -> BreakoutScreenerResponse:
    try:
        return screener_service.run_breakout_screener(
            market=market,
            minimum_score=minimum_score,
            rating=rating,
            setup_state=setup_state,
            maximum_extension_atr=maximum_extension_atr,
            include_four_hour=include_four_hour,
            limit=limit,
        )
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
