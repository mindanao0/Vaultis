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
    if payload.risk_profile not in rebalance_service.TARGET_WEIGHTS:
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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
