"""Price alert API — ครอบ store เดียวของระบบ (``alerts/price_alert.py``).

AUDIT_2026-08-06 ข้อ A1: store โยน ``AlertStoreUnavailable`` เมื่ออ่านคลังไม่ได้
(แทนที่จะคืนลิสต์ว่างแล้วเขียนทับของผู้ใช้) — ทุก route ต้องแปลงเป็น **503 พร้อมสาเหตุ
ภาษาไทย** ไม่ใช่ปล่อยหลุดเป็น ``500 Internal Server Error`` เปล่า ๆ ที่แยกไม่ออกจาก
"ระบบมีข้อผิดพลาด" ทั่วไป — "อ่านคลังไม่ได้" ≠ "ไม่มี alert" ต้องแยกกันจนถึงผู้ใช้
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from alerts.price_alert import AlertStoreUnavailable

from ..schemas import PriceAlertCreate
from ..services import alert_service

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])

_STORE_ERROR_PREFIX = "อ่านคลัง price alert ไม่ได้ — ระบบไม่ได้แตะไฟล์ของคุณ"


def _store_unavailable(exc: AlertStoreUnavailable) -> HTTPException:
    return HTTPException(status_code=503, detail=f"{_STORE_ERROR_PREFIX}: {exc}")


@router.get("")
def get_alerts():
    try:
        return JSONResponse(
            content={"data": alert_service.list_alerts()},
            media_type="application/json; charset=utf-8",
        )
    except AlertStoreUnavailable as exc:
        raise _store_unavailable(exc) from exc
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
    except AlertStoreUnavailable as exc:
        raise _store_unavailable(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{alert_id}")
def delete_alert(alert_id: str):
    try:
        deleted = alert_service.delete_alert(alert_id)
    except AlertStoreUnavailable as exc:
        # เดิมหลุดเป็น 500 เปล่า ๆ — ผู้ใช้แยกไม่ออกว่า "ไม่มี alert นี้" หรือ "อ่านคลังไม่ได้"
        raise _store_unavailable(exc) from exc
    if not deleted:
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
    except AlertStoreUnavailable as exc:  # ปกติ check_alerts() จับเองแล้วคืน store_error
        raise _store_unavailable(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
