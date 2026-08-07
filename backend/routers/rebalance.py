from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from ..schemas import RebalanceRequest
from ..services import rebalance_service

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


@router.post("/rebalance")
def rebalance_portfolio(
    payload: RebalanceRequest,
    include_ai: bool = Query(False, description="เรียก AI อธิบายแผน (มีค่าใช้จ่าย)"),
):
    """แผน rebalance.

    ราคาที่ดึงไม่ได้ = **ไม่มีแผน** ไม่ใช่ error: ตอบ 200 พร้อม ``missing_prices``
    (รายชื่อ ticker ที่ขาดราคา), ``actions: []``, ``needs_rebalance: null`` และ
    ``detail`` ที่อธิบายเหตุผล — ผู้ใช้ต้องเห็นว่าทำไมถึงไม่มีแผน ไม่ใช่เจอ 500 เปล่า ๆ

    ของที่ถืออยู่แต่ไม่มีในสัดส่วนเป้าหมายจะโผล่ใน ``untracked_holdings`` พร้อม
    คำอธิบายใน ``detail`` — เป้าของมันคือ 0% แผนจึงตั้งเป้าขายออก (AUDIT B4.2)

    ``risk_profile`` ต้องตรงกับ ``portfolio.risk_profile`` ใน ``config.json``
    ไม่ตรง = **400** (ไม่ใช่คำนวณให้เงียบ ๆ ด้วยน้ำหนักคนละชุดกับแผน DCA รายเดือน)
    """
    if payload.risk_profile not in rebalance_service.RISK_PROFILE_NAMES:
        raise HTTPException(status_code=400, detail="risk_profile ไม่ถูกต้อง")
    try:
        holdings = [{"symbol": h.symbol, "shares": h.shares} for h in payload.holdings]
        result = rebalance_service.compute_rebalance(
            holdings=holdings,
            risk_profile=payload.risk_profile,
            available_budget_thb=payload.available_budget_thb,
            user_initiated=include_ai,
        )
        return JSONResponse(
            content={"data": result},
            media_type="application/json; charset=utf-8",
        )
    except rebalance_service.RiskProfileMismatch as exc:
        # คำขอของผู้เรียกขัดกับค่าที่ระบบตั้งไว้ = ความผิดของคำขอ ไม่ใช่ 500
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
