"""WebSocket routes for real-time ETF price updates."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alerts.price_alert import get_price_snapshots

logger = logging.getLogger(__name__)

router = APIRouter()

TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
_BROADCAST_LOCK = asyncio.Lock()
_broadcaster_task: asyncio.Task[None] | None = None


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass

    async def broadcast(self, data: dict) -> None:
        dead: list[WebSocket] = []
        text = json.dumps(data, ensure_ascii=False)
        for connection in self.active_connections:
            try:
                await connection.send_text(text)
            except Exception:
                dead.append(connection)
        for connection in dead:
            self.disconnect(connection)


manager = ConnectionManager()


# ข้อความกำกับช่อง %เปลี่ยนแปลงที่คำนวณไม่ได้ — หน้าจอต้องมีอะไรพิมพ์แทนตัวเลข
# ไม่ใช่ปล่อยว่างหรือใส่ 0 ("คำนวณไม่ได้" ≠ "ราคาไม่ขยับ")
CHANGE_PCT_UNKNOWN_NOTE = "ไม่มีแท่งปิดก่อนหน้า — คำนวณ %เปลี่ยนแปลงไม่ได้"


def _fetch_ticker_snapshot(ticker: str) -> dict[str, float | str | None] | None:
    """ราคาล่าสุด + %เปลี่ยนแปลง จาก **แท่งรายวันชุดเดียวกัน 2 แท่ง**.

    สัญญาของค่าที่คืน — สามสถานะ ห้ามยุบรวมกัน:

    - ``None`` = ดึงราคาไม่ได้เลย (ผู้เรียกเอาไปใส่ ``unavailable``) ห้าม broadcast
      0.0 ปลอม (AUDIT.md C1)
    - ``{"price": x, "change_pct": None, "note": ...}`` = ได้ราคาจริง แต่**คำนวณ
      %เปลี่ยนแปลงไม่ได้** เพราะมีแท่งปิดแท่งเดียวหรือราคาอ้างอิง ``<= 0``
    - ``{"price": x, "change_pct": y}`` = ครบทั้งคู่

    เดิมใช้ ``fast_info['last_price']`` คู่กับ ``fast_info['previous_close']``
    ซึ่งผิดสองชั้น (AUDIT_ROUND2_2026-08-07):

    1. **คนละแหล่ง คนละความหมาย** — ``previous_close`` ของ ``fast_info`` ไม่ใช่
       ราคาปิดของแท่งที่อยู่ก่อนราคาที่กำลังแสดง (มันมาจาก quote endpoint และช่วง
       ตลาดเปิดมันคือแท่งก่อนวันเต็มวันล่าสุด) วัดจริงในคอนเทนเนอร์ได้
       ``last=100.69 prev=100.76`` ⇒ แถบราคารายงาน ``-0.07%`` (แดง) ให้ ETF ที่วันนั้น
       ปิด ``+0.69%`` (เขียว) — แถบนี้อยู่บนสุดของทุกหน้า และคนอ่าน "สี" ก่อนตัวเลขเสมอ
    2. ``change_pct = 0.0`` เมื่อ ``prev <= 0`` คือการเอาผลคำนวณที่ล้มเหลวไปแสดงเป็น
       ตัวเลขจริง — ``+0.00% 🟢`` อ่านได้ว่า "วันนี้ราคาไม่ขยับ" ทั้งที่แปลว่าไม่รู้

    นิยาม "ราคาล่าสุด + แท่งปิดก่อนหน้า" มีที่เดียวทั้งระบบคือ
    ``alerts.price_alert.get_price_snapshots`` (กติกาเดียวกับ
    ``jobs/daily_check._yfinance_snapshot`` และ ``etf_service.get_etf_daily_eod_snapshot``)
    ตัวตั้งกับตัวหารจึงมาจากชุดข้อมูลชุดเดียวกันเสมอ ไม่ใช่ราคาสดจาก quote หารด้วย
    แท่งรายวันคนละชุด — ต้นเหตุของการพลิกเครื่องหมายในข้อ 1
    """
    try:
        snapshot = get_price_snapshots([ticker]).get(str(ticker).strip().upper())
    except Exception as exc:  # ticker เดียวพังต้องไม่ลากลูป broadcast ลงไปทั้งตัว
        logger.warning("ws price fetch failed for %s: %s", ticker, exc)
        return None

    price = (snapshot or {}).get("latest_price")
    if price is None:
        logger.warning("ws price fetch failed for %s: ไม่มีแท่งราคาส่งกลับมา", ticker)
        return None

    prev = snapshot.get("previous_close")
    change_pct: float | None = None
    if prev is not None and prev > 0:
        change_pct = round((float(price) - float(prev)) / float(prev) * 100.0, 2)

    row: dict[str, float | str | None] = {
        "price": round(float(price), 2),
        "change_pct": change_pct,
    }
    if change_pct is None:
        row["note"] = CHANGE_PCT_UNKNOWN_NOTE
    return row


async def _price_broadcast_loop() -> None:
    while True:
        try:
            prices: dict[str, dict[str, float | str | None]] = {}
            unavailable: list[str] = []
            for ticker in TICKERS:
                # yfinance เป็น sync I/O — ต้องออกจาก event loop ไม่งั้น API ทั้งตัวค้าง (AUDIT.md M13)
                snapshot = await asyncio.to_thread(_fetch_ticker_snapshot, ticker)
                if snapshot is not None:
                    prices[ticker] = snapshot
                else:
                    # ดึงไม่ได้ต้องประกาศออกไป — ข้ามเงียบ ๆ ทำให้หน้าจอค้างราคาเก่าไว้
                    # โดยผู้ใช้ไม่รู้ว่าหยุดอัปเดตแล้ว (AUDIT_2026-08-06 B7)
                    unavailable.append(ticker)

            # ส่งทุกรอบแม้ดึงไม่ได้ทั้งหมด: "ดึงไม่สำเร็จ" ≠ "ไม่มีข้อมูล" และเงียบ = หน้าจอโกหก
            await manager.broadcast(
                {
                    "type": "price_update",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "data": prices,
                    "unavailable": unavailable,
                }
            )
        except Exception as exc:
            logger.exception("broadcast loop error: %s", exc)

        await asyncio.sleep(30)


async def _ensure_broadcaster() -> None:
    global _broadcaster_task
    async with _BROADCAST_LOCK:
        if _broadcaster_task is None or _broadcaster_task.done():
            _broadcaster_task = asyncio.create_task(_price_broadcast_loop())


@router.websocket("/ws/prices")
async def websocket_prices(websocket: WebSocket) -> None:
    await _ensure_broadcaster()
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
