import logging
import os
from contextlib import asynccontextmanager

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
from .services.json_safe import json_safe
from .services.report_service import generate_and_save_report as run_monthly_report

DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL_ENV = "VAULTIS_LOG_LEVEL"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str | None = None) -> None:
    """ตั้งค่า logging ของทั้งโปรเซส — ต้องเรียกที่ "ทางเข้า" เท่านั้น ห้ามโรยตามโมดูล.

    ก่อนแก้: ไม่มีที่ไหนในระบบเรียก ``basicConfig``/``dictConfig`` เลย และ
    ``docker-compose.yml`` รัน uvicorn โดยไม่ส่ง ``--log-level`` (ซึ่งต่อให้ส่งก็ไม่ช่วย
    เพราะ uvicorn ตั้งค่าเฉพาะ logger ชื่อ ``uvicorn*``) logger ของแอปจึงตกไปที่ root
    ที่มีแต่ ``lastResort`` ระดับ WARNING ⇒ **ทุก ``logger.info`` ถูกทิ้งเงียบ**
    รวมถึงบรรทัดสรุปของ screener ``"Screener run complete: %d/%d symbols passed,
    %d ตรวจไม่ได้"`` ที่เขียนไว้เพื่อกฎ C1 โดยเฉพาะ และบรรทัด ``scheduler started``
    ผลคือแยกไม่ออกระหว่าง "งาน 07:00 รันแล้วไม่มีสัญญาณ" กับ "งานไม่ได้รันเลย"
    (AUDIT_ROUND2_2026-08-07 — วัดจริงใน container: grep 'Screener run complete' → 0 บรรทัด
    ขณะที่ WARNING/ERROR ผ่านได้ปกติ)

    ระดับ log ปรับได้ที่ ``VAULTIS_LOG_LEVEL`` (ดีฟอลต์ ``INFO``) ค่าที่ไม่รู้จักไม่ทำให้
    โปรเซสตาย แต่ถอยไปใช้ ``INFO`` พร้อมเตือน — log ที่ตั้งค่าผิดต้องไม่ทำให้ backend ล่ม

    ไม่ใช้ ``force=True`` โดยตั้งใจ: ถ้ามีใครตั้ง handler ไว้ก่อนแล้ว (deploy ที่มี
    dictConfig ของตัวเอง หรือ plugin logging ของ pytest) ``basicConfig`` จะไม่ทำอะไร
    ซึ่งถูกต้องแล้ว — ทางเข้าตั้งค่าให้ "เมื่อไม่มีใครตั้ง" ไม่ใช่แย่งของคนอื่น
    """
    raw = level if level is not None else os.getenv(LOG_LEVEL_ENV, "")
    name = (raw or DEFAULT_LOG_LEVEL).strip().upper()
    resolved = int(name) if name.isdigit() else logging.getLevelNamesMapping().get(name)
    unknown = resolved is None
    if unknown:
        resolved = logging.INFO
    logging.basicConfig(level=resolved, format=LOG_FORMAT)
    if unknown:
        logging.getLogger(__name__).warning(
            "%s=%r ไม่ใช่ระดับ log ที่รู้จัก — ใช้ %s แทน", LOG_LEVEL_ENV, raw, DEFAULT_LOG_LEVEL
        )


# เรียกตอน import: uvicorn/gunicorn โหลดโมดูลนี้เป็นทางเข้าเดียวของ backend
configure_logging()

logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

# misfire_grace_time ดีฟอลต์ของ APScheduler คือ 1 วินาที — งานที่ตื่นช้ากว่านั้น
# (เช่น loop ติดงาน blocking คร่อม 07:00:00 พอดี) ถูก "ข้าม" ไปเงียบ ๆ ทั้งรอบ
# ⇒ สแกนของวันนั้นหายไปโดยไม่มีใครรู้ (AUDIT_2026-08-06 ข้อ B6.1)
# 3600 = ยอมให้สายได้ถึง 1 ชม. แล้วค่อยรัน · coalesce = รอบที่ค้างหลายรอบยุบเหลือรอบเดียว
scheduler = AsyncIOScheduler(
    timezone="Asia/Bangkok",
    job_defaults={"misfire_grace_time": 3600, "coalesce": True, "max_instances": 1},
)


def _report_job_problem(event) -> None:
    """งานตามเวลาที่ถูกข้ามหรือพัง ต้องดังออกมา ไม่ใช่หายเงียบ."""
    if event.code == EVENT_JOB_MISSED:
        logger.error(
            "งานตามเวลา %s ถูกข้าม (misfire) รอบ %s — รอบนี้ไม่มีผลลัพธ์",
            event.job_id,
            getattr(event, "scheduled_run_time", "?"),
        )
    else:
        logger.error(
            "งานตามเวลา %s ล้มเหลว: %s",
            event.job_id,
            getattr(event, "exception", None),
            exc_info=getattr(event, "exception", None),
        )


scheduler.add_listener(_report_job_problem, EVENT_JOB_MISSED | EVENT_JOB_ERROR)


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


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 ต้องส่งออกได้เสมอ แม้ค่าที่ผู้ใช้ส่งมาเป็น ``inf``/``NaN``.

    ตัวจัดการมาตรฐานของ FastAPI สะท้อนค่าที่ผิดกลับไปในช่อง ``input`` แล้ว
    ``JSONResponse`` render ด้วย ``allow_nan=False`` ⇒ ``inf`` ทำให้ **ทั้งคำตอบ**
    กลายเป็น 500 "Internal Server Error" ภาษาอังกฤษ ทั้งที่ความจริงคือ "อินพุตผิด"
    (เจอตอนปิด G8: ``{"weights": {"VOO": Infinity}}`` ควรได้ 422 ที่อ่านออก)

    ``json_safe`` แปลงเฉพาะค่าที่ JSON ไม่รองรับให้เป็น ``null`` — รูปร่าง body
    (``detail`` เป็น array ของ object ที่มี ``loc``/``msg``) ยังตรงกับ openapi เหมือนเดิม
    """
    return JSONResponse(
        status_code=422,
        content={"detail": json_safe(jsonable_encoder(exc.errors()))},
        media_type="application/json; charset=utf-8",
    )


# เดิม allow_origins=["*"] → เว็บใดก็ยิง API นี้จากเบราว์เซอร์ผู้ใช้ได้ (AUDIT.md H1)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    # PUT ต้องอยู่ในรายการ ไม่งั้น preflight ของ PUT /api/goals/{id}/contribute
    # ถูกปฏิเสธ 400 ทั้งที่ openapi ประกาศ endpoint นั้นไว้ (AUDIT_2026-08-06 D3.3)
    # ตาข่ายกันหลุด: tests/test_audit_d3.py (ทุกเมธอดที่ route ประกาศต้องอยู่ที่นี่)
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# --- อ่านอย่างเดียว: เปิดได้ (ไม่เปลี่ยนสถานะ ไม่มีค่าใช้จ่าย) ---
app.include_router(etf.router)
app.include_router(prices_ws.router)

# --- ต้องมี X-API-Key: เปลี่ยนสถานะ, มีค่าใช้จ่าย LLM, หรือเข้าถึงข้อมูลส่วนตัว ---
# ทุก router ต้องประกาศ path ไม่ชนกัน: FastAPI จับคู่ route แรกที่ตรงเสมอ ตัวหลังจึง
# เข้าไม่ถึงตลอดกาลโดยไม่มี error ให้เห็น (เคย: analysis.router ทับ backtest.router ที่
# POST /api/backtest) — tests/test_route_uniqueness.py กันไม่ให้กลับมา
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
