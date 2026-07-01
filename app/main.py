from fastapi import FastAPI

from app.api.v1.stocks import router as stocks_router

app = FastAPI(
    title="Stock Valuation API",
    version="0.1.0",
    description="Quote, fundamentals, and intrinsic-value estimates powered by yFinance.",
)


@app.get("/", tags=["health"])
def root() -> dict[str, str]:
    return {
        "name": "Stock Valuation API",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(stocks_router, prefix="/api/v1")
