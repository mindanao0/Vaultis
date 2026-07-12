"""WebSocket routes for real-time ETF price updates."""

from __future__ import annotations

import asyncio
import json
import logging

import yfinance as yf
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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


def _fetch_ticker_snapshot(ticker: str) -> dict[str, float] | None:
    """ดึงราคา + %เปลี่ยนแปลง; ดึงไม่ได้คืน None — ห้าม broadcast 0.0 ปลอม (AUDIT.md C1)."""
    try:
        info = yf.Ticker(ticker).fast_info
        price = float(info["last_price"])
        prev = float(info["previous_close"])
        change_pct = ((price - prev) / prev * 100.0) if prev > 0 else 0.0
        return {"price": round(price, 2), "change_pct": round(change_pct, 2)}
    except Exception as exc:
        logger.warning("ws price fetch failed for %s: %s", ticker, exc)
        return None


async def _price_broadcast_loop() -> None:
    while True:
        try:
            prices: dict[str, dict[str, float]] = {}
            for ticker in TICKERS:
                # yfinance เป็น sync I/O — ต้องออกจาก event loop ไม่งั้น API ทั้งตัวค้าง (AUDIT.md M13)
                snapshot = await asyncio.to_thread(_fetch_ticker_snapshot, ticker)
                if snapshot is not None:
                    prices[ticker] = snapshot

            if prices:
                await manager.broadcast({"type": "price_update", "data": prices})
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
