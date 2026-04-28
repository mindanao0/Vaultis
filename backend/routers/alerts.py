from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import PriceAlertCreate
from ..services import alert_service

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])


@router.get("")
def get_alerts(db: Session = Depends(get_db)):
    return {"data": alert_service.list_alerts(db)}


@router.post("")
def create_alert(payload: PriceAlertCreate, db: Session = Depends(get_db)):
    try:
        row = alert_service.create_alert(db, payload)
        return {"data": row}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{alert_id}")
def delete_alert(alert_id: int, db: Session = Depends(get_db)):
    if not alert_service.delete_alert(db, alert_id):
        raise HTTPException(status_code=404, detail="alert not found")
    return {"data": {"deleted": True, "id": alert_id}}


@router.post("/check")
def check_alerts(db: Session = Depends(get_db)):
    return {"data": alert_service.check_alerts(db)}
