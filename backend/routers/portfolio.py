from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import TransactionCreate
from ..services import portfolio_service

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


@router.get("")
def get_portfolio(db: Session = Depends(get_db)):
    return JSONResponse(
        content={"data": portfolio_service.get_portfolio_summary(db)},
        media_type="application/json; charset=utf-8",
    )


@router.get("/holdings")
def get_holdings(db: Session = Depends(get_db)):
    return JSONResponse(
        content={"data": portfolio_service.get_holdings(db)},
        media_type="application/json; charset=utf-8",
    )


@router.get("/history")
def get_history(db: Session = Depends(get_db)):
    return JSONResponse(
        content={"data": portfolio_service.get_history(db)},
        media_type="application/json; charset=utf-8",
    )


@router.post("/add")
def add_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    try:
        row = portfolio_service.add_transaction(db, payload)
        return JSONResponse(
            content={"data": row},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{tx_id}")
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    if not portfolio_service.delete_transaction(db, tx_id):
        raise HTTPException(status_code=404, detail="transaction not found")
    return JSONResponse(
        content={"data": {"deleted": True, "id": tx_id}},
        media_type="application/json; charset=utf-8",
    )
