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
    """ตรวจ alert ทั้งหมด (ตัวเดียวกับที่ cron รายวันเรียก) และส่ง Discord ถ้ามี trigger."""
    result = price_alert.check_alerts()
    return {
        "checked": result.get("checked", 0),
        "triggered": result.get("triggered", []),
        "daily_summary": result.get("daily_summary", ""),
    }
