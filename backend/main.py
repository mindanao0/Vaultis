import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from .routers import (
    ai,
    alerts,
    analysis,
    backtest,
    cashflow,
    debt,
    emergency_fund,
    etf,
    etf_analysis,
    forecast,
    goals,
    networth,
    portfolio,
    rebalance,
    reports,
    screener,
    sentiment,
    transactions,
)
from .routers import websocket as prices_ws
from .screener.scheduler_job import run_daily_screener
from .security import allowed_origins, require_api_key
from .services.report_service import generate_and_save_report as run_monthly_report

logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

scheduler = AsyncIOScheduler(timezone="Asia/Bangkok")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # @app.on_event ถูก deprecate แล้ว — ใช้ lifespan แทน
    scheduler.add_job(run_daily_screener, "cron", hour=7, minute=0)
    scheduler.add_job(run_monthly_report, "cron", day=1, hour=8, minute=0)
    scheduler.start()
    logger.info("scheduler started (Asia/Bangkok)")
    yield
    scheduler.shutdown()


app = FastAPI(
    title="Vaultis API",
    description="ETF Analysis Backend for Vaultis",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# เดิม allow_origins=["*"] → เว็บใดก็ยิง API นี้จากเบราว์เซอร์ผู้ใช้ได้ (AUDIT.md H1)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# --- อ่านอย่างเดียว: เปิดได้ (ไม่เปลี่ยนสถานะ ไม่มีค่าใช้จ่าย) ---
app.include_router(etf.router)
app.include_router(prices_ws.router)

# --- ต้องมี X-API-Key: เปลี่ยนสถานะ, มีค่าใช้จ่าย LLM, หรือเข้าถึงข้อมูลส่วนตัว ---
protected = [
    portfolio.router,
    rebalance.router,
    goals.router,
    reports.router,
    alerts.router,
    ai.router,
    transactions.router,
    networth.router,
    cashflow.router,
    debt.router,
    emergency_fund.router,
    screener.router,
    sentiment.router,
    analysis.router,
    backtest.router,
    forecast.router,
    etf_analysis.router,
]
for router in protected:
    app.include_router(router, dependencies=[Depends(require_api_key)])


@app.get("/health")
def health():
    return {"status": "ok", "service": "Vaultis Backend"}
