from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .database import Base, engine
from .routers import ai, alerts, analysis, etf, etf_analysis, portfolio, screener, sentiment, websocket as prices_ws
from .screener.scheduler_job import run_daily_screener

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
app.include_router(screener.router)
app.include_router(prices_ws.router)

scheduler = AsyncIOScheduler(timezone="Asia/Bangkok")


@app.on_event("startup")
async def start_scheduler():
    scheduler.add_job(run_daily_screener, "cron", hour=7, minute=0)
    scheduler.start()


@app.on_event("shutdown")
async def stop_scheduler():
    scheduler.shutdown()


@app.get("/health")
def health():
    return {"status": "ok", "service": "Vaultis Backend"}
