from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..schemas import PriceAlertCreate
from ..services import alert_service

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])


@router.get("")
def get_alerts():
    try:
        return JSONResponse(
            content={"data": alert_service.list_alerts()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("")
def create_alert(payload: PriceAlertCreate):
    try:
        row = alert_service.create_alert(payload)
        return JSONResponse(
            content={"data": row},
            media_type="application/json; charset=utf-8",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{alert_id}")
def delete_alert(alert_id: str):
    if not alert_service.delete_alert(alert_id):
        raise HTTPException(status_code=404, detail="alert not found")
    return JSONResponse(
        content={"data": {"deleted": True, "id": alert_id}},
        media_type="application/json; charset=utf-8",
    )


@router.post("/check")
def check_alerts():
    try:
        return JSONResponse(
            content={"data": alert_service.check_alerts()},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
