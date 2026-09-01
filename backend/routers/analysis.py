from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from analysis.financial_model import ALLOCATION_UNIT_THB
from data.fetcher import PriceDataUnavailableError
from portfolio.targets import NoTargetForSubset, TargetWeightsError

from ..schemas import DcaSimRequest, PortfolioBacktestRequest, validate_dca_budget
from ..services import market_analysis_service as service

router = APIRouter(prefix="/api", tags=["Analysis"])


def _json(data) -> JSONResponse:
    return JSONResponse(content={"data": data}, media_type="application/json; charset=utf-8")


def _looks_like_unknown_symbol(exc: Exception) -> bool:
    """แยก "ไม่มีสัญลักษณ์นี้" (404 จาก Yahoo) ออกจาก "ดึงไม่สำเร็จชั่วคราว".

    yfinance ปัจจุบันใช้ curl_cffi ซึ่งโยน ``HTTPError`` ของตัวเอง (subclass ของ
    ``OSError`` ไม่ใช่ ``requests.HTTPError``) — จับด้วยชนิดข้ามไลบรารีไม่ได้
    จึงอ่าน status code จากอ็อบเจกต์ก่อน แล้วค่อยถอยไปดูข้อความ
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    if status is not None:
        return int(status) == 404
    return "404" in str(exc)


# เดิม path นี้คือ "/backtest" ซึ่งชนกับ routers/backtest.py (กลยุทธ์ RSI+MACD ด้วย vectorbt)
# router ตัวนี้ถูก include ก่อน FastAPI จึงส่งงานมาที่นี่เสมอ → ทั้งไฟล์ backtest.py
# กลายเป็นโค้ดตาย ขณะที่ /docs โฆษณา schema ของตัวที่เข้าไม่ถึง (ยิงตามเอกสารได้ 422)
# Backend Router Map ยกชื่อ /api/backtest ให้ routers/backtest.py ตัวนี้จึงย้ายมาอยู่ใต้ /api/analysis
@router.post("/analysis/backtest")
def run_portfolio_weight_backtest(payload: PortfolioBacktestRequest):
    try:
        return _json(service.run_backtest(payload.weights, payload.initial_capital))
    except PriceDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dca/simulate")
def run_dca_simulation(payload: DcaSimRequest):
    try:
        return _json(service.simulate_dca(payload.weights, payload.monthly_investment))
    except PriceDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/macro")
def get_macro():
    data = service.macro_snapshot()
    if not data:
        raise HTTPException(status_code=503, detail="ดึงข้อมูล macro ไม่สำเร็จ (ตรวจสอบ FRED_API_KEY)")
    return _json(data)


@router.get("/analysis/dcf/{ticker}")
def get_dcf_for_ticker(ticker: str):
    try:
        return _json(service.dcf_for_ticker(ticker))
    except ValueError as exc:
        # เช่น GLDM: สินทรัพย์ที่ไม่มีกำไร ทำ DCF ไม่ได้ — บอกตรง ๆ ไม่เดาตัวเลข
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PriceDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        # เดิมข้อผิดพลาดจาก yfinance หลุดออกไปเป็น 500 + traceback ภาษาอังกฤษ
        # (ticker ที่ไม่มีจริง → HTTP 404 จาก Yahoo) — AUDIT_2026-08-06 D3.1
        if _looks_like_unknown_symbol(exc):
            raise HTTPException(
                status_code=404,
                detail=f"ไม่พบสัญลักษณ์ {ticker} ที่แหล่งข้อมูล (ตรวจตัวสะกดอีกครั้ง)",
            ) from exc
        raise HTTPException(
            status_code=503,
            detail=f"ดึงข้อมูลของ {ticker} ไม่สำเร็จ: {exc}",
        ) from exc


def _no_target_for_subset_detail(exc: NoTargetForSubset) -> str:
    """ข้อความของ "รอบนี้ไม่มีน้ำหนักให้จัดสรร" — ต้องชี้ไปที่ข้อมูล ไม่ใช่ที่ config.json.

    เขียนใหม่จากฟิลด์ ``requested`` / ``missing`` ของ exception (ไม่ใช่ ``str(exc)``)
    เพื่อให้คำตอบของ API เรียกชื่อ ETF ทั้งสองกลุ่มเสมอ แม้ข้อความต้นทางจะถูกแก้ถ้อยคำ
    ในอนาคต — ปลายทางจะได้ไม่ต้อง parse ประโยคไทยเอาเอง (AUDIT_ROUND2_2026-08-07 G1)
    """
    requested = ", ".join(exc.requested) if exc.requested else "(ไม่มี)"
    missing = ", ".join(exc.missing) if exc.missing else "(ไม่ทราบ)"
    return (
        "จัดสรรงบรอบนี้ไม่ได้เพราะข้อมูลราคาไม่ครบ ไม่ใช่เพราะสัดส่วนใน config.json ผิด: "
        f"ETF ที่มีข้อมูลรอบนี้ ({requested}) ถูกตั้งเป้าหมายไว้ 0% ทั้งหมด "
        f"ส่วนตัวที่ถือน้ำหนักอยู่ ({missing}) ดึงราคาไม่สำเร็จ "
        "— ลองใหม่อีกครั้งเมื่อข้อมูลกลับมา ไม่ต้องแก้สัดส่วนเป้าหมาย"
    )


@router.get("/analysis/full")
def get_full_financial_analysis(
    budget_thb: float = Query(
        5000, description=f"งบ DCA เดือนนี้ (บาท) — ต้องอย่างน้อย {ALLOCATION_UNIT_THB} บาท"
    ),
):
    # งบต่ำกว่าหนึ่งก้อน (100 บาท) แจกไม่ลงสักตัว เดิมได้แผนว่างแล้วปลายทางรายงานว่า
    # "ดึงข้อมูลไม่ได้" ทั้งที่ข้อมูลครบ — AUDIT_2026-08-06 D3.10
    #
    # ด่านและประโยคไทยมาจาก ``schemas.validate_dca_budget`` ตัวเดียวกับที่ ``POST /api/ai/advice``
    # ใช้ — อินพุตเดียวกันต้องได้คำตอบเดียวกัน เดิมเงื่อนไขถูกเขียนซ้ำที่นี่อีกชุดแล้วเพี้ยนจริง:
    # ``ge=1`` ปล่อย ``budget_thb=inf`` ผ่าน (``inf >= 1`` และ ``inf < 100`` เป็น False) ไปตาย
    # ตอน serialize เป็นข้อความอังกฤษ 500 ขณะที่ ``/api/ai/advice`` ตอบ 422 ภาษาไทย
    # (AUDIT_ROUND2_2026-08-07)
    #
    # ``detail`` ตรงนี้เป็นสตริงประโยคเดียว (ไม่ใช่ list แบบ 422 ของ Pydantic) เพราะหน้าจอ
    # เอาไปแสดงตรง ๆ และผู้เรียกเดิมอ่านรูปนี้อยู่ — สิ่งที่ต้องตรงกันคือ "รหัสสถานะ + เหตุผล"
    try:
        budget_thb = validate_dca_budget(budget_thb)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        return _json(service.full_analysis(budget_thb))
    except PriceDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except NoTargetForSubset as exc:
        # "ดึงราคาไม่สำเร็จรอบนี้" = ปัญหาชั่วคราวของแหล่งข้อมูล ⇒ 503 เท่ากับ
        # PriceDataUnavailableError (คนละรหัสกับคอนฟิกผิด เพราะผู้ใช้ต้องทำคนละอย่าง:
        # อันนี้แค่รอ อีกอันต้องไปแก้ config.json) เดิมหล่นไปเข้า ``except Exception``
        # แล้วออกเป็น 500 เปล่า ๆ = "เซิร์ฟเวอร์พัง" ทั้งที่ระบบรู้สาเหตุครบ
        raise HTTPException(status_code=503, detail=_no_target_for_subset_detail(exc)) from exc
    except TargetWeightsError as exc:
        # เหลือแต่ ``InvalidTargetWeights`` (และชนิดใหม่ในอนาคต) = คอนฟิกผิดจริง
        # แก้ที่ config.json แล้วหาย → 422 พร้อมข้อความไทยที่ targets.py เขียนไว้
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
