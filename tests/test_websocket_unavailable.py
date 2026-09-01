# -*- coding: utf-8 -*-
"""B7 — แถบราคาสดต้องบอกได้ว่า ticker ไหน "ดึงไม่ได้" ไม่ใช่เงียบไปเฉย ๆ.

`_fetch_ticker_snapshot()` คืน ``None`` เมื่อดึงราคาไม่ได้ (ถูกแล้ว ห้าม broadcast 0.0 ปลอม)
แต่เดิม `_price_broadcast_loop` แค่ข้ามตัวนั้นไป ข้อความที่ส่งออกจึงมีแค่
``{"type": "price_update", "data": {...}}`` — ไม่มีช่องบอกว่าใครหาย ฝั่ง JS วนเฉพาะคีย์
ที่มีใน ``data`` จึงคง ``innerHTML`` เดิม (ราคาเก่า สีเก่า) ค้างไว้ตลอด ทั้งที่แถบนี้อยู่บนสุด
ของทุกหน้า ⇒ "ดึงไม่สำเร็จ" ถูกอ่านเป็น "ราคายังเป็นเท่าเดิม"

กฎที่คุม: **"ตัดข้อมูลทิ้งเงียบ" ผิดพอกับ "กุตัวเลข"** — ตัดได้ แต่ต้องรายงานออกไปให้เห็น
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from backend.routers import websocket as ws


async def _capture_one_broadcast(
    monkeypatch: pytest.MonkeyPatch,
    snapshots: dict[str, dict[str, float] | None],
    timeout: float = 5.0,
) -> dict:
    """รัน `_price_broadcast_loop` จนได้ข้อความแรก แล้วยกเลิก task ทิ้ง.

    ไม่แตะ ``asyncio.sleep`` ของจริง — ยกเลิกทันทีที่ broadcast แรกออก จึงไม่ต้องรอ 30 วิ
    """
    sent: list[dict] = []
    got = asyncio.Event()

    async def fake_broadcast(payload: dict) -> None:
        sent.append(payload)
        got.set()

    monkeypatch.setattr(ws.manager, "broadcast", fake_broadcast)
    monkeypatch.setattr(ws, "_fetch_ticker_snapshot", lambda ticker: snapshots.get(ticker))

    task = asyncio.create_task(ws._price_broadcast_loop())
    try:
        await asyncio.wait_for(got.wait(), timeout=timeout)
    except asyncio.TimeoutError:  # pragma: no cover - ทางนี้คือเทสต์แดง
        pytest.fail("ลูป broadcast ไม่ส่งข้อความออกมาเลย — ผู้ใช้ไม่มีทางรู้ว่าราคาหยุดอัปเดต")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(sent) == 1
    return sent[0]


def _ok(price: float, change_pct: float) -> dict[str, float]:
    return {"price": price, "change_pct": change_pct}


async def test_ticker_ที่ดึงไม่ได้ต้องถูกประกาศในข้อความ():
    """GLDM ดึงไม่ได้ → ต้องโผล่ใน ``unavailable`` ไม่ใช่หายไปเฉย ๆ."""
    with pytest.MonkeyPatch.context() as mp:
        payload = await _capture_one_broadcast(
            mp,
            {
                "VOO": _ok(500.0, 1.0),
                "SCHD": _ok(28.0, -0.5),
                "QQQM": _ok(210.0, 0.2),
                "XLV": _ok(140.0, 0.0),
                "GLDM": None,
            },
        )

    assert payload["type"] == "price_update"
    assert set(payload["data"]) == {"VOO", "SCHD", "QQQM", "XLV"}
    assert payload["unavailable"] == ["GLDM"], (
        "ticker ที่ดึงไม่ได้ต้องถูกรายงานออกไป ไม่ใช่ถูกข้ามเงียบ ๆ "
        f"(ฟิลด์ระดับบนสุดที่ได้: {sorted(payload)})"
    )
    # ต้องแยก "ดึงไม่ได้" ออกจาก "ไม่มีข้อมูล" ได้ — ห้ามโผล่ใน data ด้วยค่าใด ๆ
    assert "GLDM" not in payload["data"]


async def test_ทุก_ticker_ดึงไม่ได้ก็ยังต้องส่งข้อความบอก():
    """เดิม ``if prices:`` ทำให้ไม่ส่งอะไรเลย — หน้าจอค้างราคาเก่าไว้แบบไม่มีสัญญาณเตือน."""
    with pytest.MonkeyPatch.context() as mp:
        payload = await _capture_one_broadcast(mp, dict.fromkeys(ws.TICKERS, None))

    assert payload["data"] == {}
    assert payload["unavailable"] == list(ws.TICKERS)


async def test_ข้อความมีเวลาอัปเดตล่าสุดและเป็น_json_ที่ถูกต้อง():
    """ต้องมี ``ts`` ให้หน้าจอบอกได้ว่าข้อมูลเก่าแค่ไหน และต้อง serialize เป็น JSON ได้จริง."""
    with pytest.MonkeyPatch.context() as mp:
        payload = await _capture_one_broadcast(
            mp, {"VOO": _ok(500.0, 1.0), "SCHD": None, "QQQM": None, "XLV": None, "GLDM": None}
        )

    assert "ts" in payload, f"ไม่มีเวลาอัปเดตในข้อความ (ได้: {sorted(payload)})"
    parsed = datetime.fromisoformat(payload["ts"])
    assert parsed.tzinfo is not None, "ts ต้องมี timezone ไม่งั้นฝั่ง JS แปลงเป็นเวลาท้องถิ่นผิด"

    text = json.dumps(payload, ensure_ascii=False)
    assert json.loads(text)["unavailable"] == ["SCHD", "QQQM", "XLV", "GLDM"]


def test_แถบราคาบน_dashboard_ต้องแสดงตัวที่ดึงไม่ได้(monkeypatch: pytest.MonkeyPatch):
    """JS ต้องอ่าน ``unavailable`` แล้วเปลี่ยนป้ายเป็น "ดึงไม่ได้" — ห้ามคงราคาเก่าค้างไว้."""
    app = pytest.importorskip("dashboard.app")

    captured: list[str] = []
    monkeypatch.setattr(
        app.components, "html", lambda html, **kwargs: captured.append(html)
    )
    app._render_realtime_price_ticker_bar()

    assert len(captured) == 1
    html = captured[0]
    assert "unavailable" in html, "JS ไม่ได้อ่านฟิลด์ unavailable เลย"
    assert "ดึงไม่ได้" in html, "ไม่มีข้อความไทยบอกผู้ใช้ว่าราคานี้ดึงไม่ได้"
    assert "data.ts" in html or "payload.ts" in html, "ไม่ได้แสดงเวลาอัปเดตล่าสุด"
