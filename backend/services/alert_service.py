"""Alert service — บาง ๆ ครอบ price-alert store เดียวของระบบ (alerts/price_alert.py, JSON).

AUDIT.md H2/H8: เดิมมี alert 2 ชุดที่ไม่รู้จักกัน — ไฟล์ JSON (dashboard, Discord,
cron ใช้) กับตาราง SQLite ``price_alerts`` (API ใช้) — alert ที่ตั้งผ่าน API จึงไม่มี
วันถูกตรวจ และ ``POST /api/alerts`` ยังพังตอน serialize ORM กลับเป็น JSON อีกด้วย

ตอนนี้ทุกช่องทางใช้ store เดียวกัน และคืน dict ที่ serialize ได้
"""

from __future__ import annotations

from typing import Any

from alerts import price_alert

from ..schemas import PriceAlertCreate


def list_alerts() -> list[dict[str, Any]]:
    return price_alert.get_active_alerts_with_distance(near_threshold_pct=2.0) + [
        item for item in price_alert.list_alerts(include_triggered=True) if item.get("triggered")
    ]


def create_alert(payload: PriceAlertCreate) -> dict[str, Any]:
    return price_alert.add_alert(
        ticker=payload.ticker,
        alert_type=payload.alert_type,
        price=float(payload.target_price),
    )


def delete_alert(alert_id: str) -> bool:
    return price_alert.delete_alert(str(alert_id))


def check_alerts() -> dict[str, Any]:
    """ตรวจ alert ทั้งหมด (ตัวเดียวกับที่ cron รายวันเรียก) และส่ง Discord ถ้ามี trigger.

    ส่งต่อ ``unchecked`` / ``success`` / ``error`` ให้ผู้เรียกด้วย — "ดึงราคาไม่ได้" และ
    "อ่านคลัง alert ไม่ได้" ต้องไม่ถูกยุบเป็น "ตรวจแล้วไม่มีอะไร" (AUDIT_2026-08-06 A1/D1.1)

    **ห้ามใช้ ``result.get(key, 0)``/``[]`` กับคีย์ที่สัญญาบอกว่ามีเสมอ** — เดิมชั้นนี้
    เติมค่าดีฟอลต์ให้ครบทุกคีย์ ผลลัพธ์ที่ผิดสัญญา (เช่น refactor ในอนาคตลืมคีย์ไป)
    จึงออกทาง ``POST /api/alerts/check`` เป็น ``checked=0, triggered=[]`` = "ตรวจแล้ว
    ไม่มีอะไร" ทั้งที่ระบบไม่ได้ตรวจอะไรเลย  ตอนนี้ใช้ด่านตรวจสัญญาตัวเดียวกับ scheduler
    (``price_alert.check_result_contract_error``) แล้วโยน ``AlertCheckContractError``
    ให้ router แปลงเป็น 503 พร้อมสาเหตุภาษาไทย (AUDIT_ROUND2_2026-08-07)
    """
    result = price_alert.check_alerts()
    contract_error = price_alert.check_result_contract_error(result)
    if contract_error is not None:
        raise price_alert.AlertCheckContractError(
            f"ผลลัพธ์จาก check_alerts() ผิดสัญญา — {contract_error} ⇒ สรุปสถานะไม่ได้ "
            "(ยังไม่รู้ว่ามี alert ถึงเงื่อนไขหรือไม่ ไม่ใช่ 'ไม่มี')"
        )
    return {
        "success": bool(result["success"]),
        "checked": result["checked"],
        "triggered": result["triggered"],
        "unchecked": result["unchecked"],
        "store_error": bool(result["store_error"]),
        "error": result.get("error"),
        "daily_summary": result.get("daily_summary", ""),
        # ``None`` = ผลลัพธ์ไม่ได้แนบสถานะคลังมา (ไม่ทราบ) — คนละอย่างกับ status="ok"
        "store_status": result.get("store_status"),
    }
