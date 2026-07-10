from fastapi import FastAPI

from app.api.v1.backtests import router as backtests_router
from app.api.v1.intelligence import router as intelligence_router
from app.api.v1.markets import router as markets_router
from app.api.v1.stocks import router as stocks_router

app = FastAPI(
    title="Trading Intelligence API",
    version="0.1.0",
    description="Market quotes, yfinance-based analysis, TradingView MCP technical analysis and screeners, sentiment, news, and backtests.",
)


@app.get("/", tags=["health"])
def root() -> dict[str, str]:
    return {
        "name": "Trading Intelligence API",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(stocks_router, prefix="/api/v1")
app.include_router(markets_router, prefix="/api/v1")
app.include_router(backtests_router, prefix="/api/v1")
app.include_router(intelligence_router, prefix="/api/v1")
