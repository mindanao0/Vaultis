from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..schemas import TransactionCreate
from ..services import portfolio_service

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


@router.get("")
def get_portfolio():
    try:
        return JSONResponse(
            content={"data": portfolio_service.get_portfolio_summary()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/holdings")
def get_holdings():
    try:
        return JSONResponse(
            content={"data": portfolio_service.get_holdings()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/history")
def get_history():
    try:
        return JSONResponse(
            content={"data": portfolio_service.get_history()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/add")
def add_transaction(payload: TransactionCreate):
    try:
        row = portfolio_service.add_transaction(payload)
        return JSONResponse(
            content={"data": row},
            media_type="application/json; charset=utf-8",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{tx_id}")
def delete_transaction(tx_id: str):
    if not portfolio_service.delete_transaction(tx_id):
        raise HTTPException(status_code=404, detail="transaction not found")
    return JSONResponse(
        content={"data": {"deleted": True, "tx_id": tx_id}},
        media_type="application/json; charset=utf-8",
    )
