from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import TransactionCreate
from ..services import portfolio_service

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


@router.get("")
def get_portfolio(db: Session = Depends(get_db)):
    return {"data": portfolio_service.get_portfolio_summary(db)}


@router.get("/holdings")
def get_holdings(db: Session = Depends(get_db)):
    return {"data": portfolio_service.get_holdings(db)}


@router.get("/history")
def get_history(db: Session = Depends(get_db)):
    return {"data": portfolio_service.get_history(db)}


@router.post("/add")
def add_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    try:
        row = portfolio_service.add_transaction(db, payload)
        return {"data": row}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{tx_id}")
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    if not portfolio_service.delete_transaction(db, tx_id):
        raise HTTPException(status_code=404, detail="transaction not found")
    return {"data": {"deleted": True, "id": tx_id}}
