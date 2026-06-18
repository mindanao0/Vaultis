from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..schemas import RebalanceRequest
from ..services import rebalance_service

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


@router.post("/rebalance")
def rebalance_portfolio(payload: RebalanceRequest):
    if payload.risk_profile not in rebalance_service.TARGET_WEIGHTS:
        raise HTTPException(status_code=400, detail="risk_profile ไม่ถูกต้อง")
    try:
        holdings = [{"symbol": h.symbol, "shares": h.shares} for h in payload.holdings]
        result = rebalance_service.compute_rebalance(
            holdings=holdings,
            risk_profile=payload.risk_profile,
            available_budget_thb=payload.available_budget_thb,
        )
        return JSONResponse(
            content={"data": result},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
