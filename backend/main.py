from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from .routers import ai, alerts, analysis, etf, etf_analysis, portfolio, sentiment, websocket as prices_ws

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Vaultis API",
    description="ETF Analysis Backend for Vaultis",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(etf.router)
app.include_router(etf_analysis.router)
app.include_router(portfolio.router)
app.include_router(analysis.router)
app.include_router(alerts.router)
app.include_router(ai.router)
app.include_router(sentiment.router)
app.include_router(prices_ws.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "Vaultis Backend"}
