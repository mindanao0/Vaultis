# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 — แถบราคาสด (`/ws/prices`) รายงาน %เปลี่ยนแปลงผิดเครื่องหมาย.

อาการที่วัดได้จริงในคอนเทนเนอร์: ``fast_info['last_price']=100.69`` คู่กับ
``fast_info['previous_close']=100.76`` ⇒ WebSocket ประกาศ ``-0.07%`` (แดง) ให้ ETF ที่
วันนั้นปิด ``+0.69%`` (เขียว) เพราะ ``previous_close`` ของ quote endpoint **ไม่ใช่**
แท่งปิดที่อยู่ก่อนราคาที่กำลังแสดง ตัวตั้งกับตัวหารมาจากคนละชุดข้อมูล

ความผิดชั้นที่สอง: ``change_pct = ... if prev > 0 else 0.0`` — ผลคำนวณที่ล้มเหลว
ถูกส่งออกไปเป็นตัวเลขจริง ``+0.00% 🟢`` ซึ่งผู้ใช้อ่านว่า "วันนี้ราคาไม่ขยับ"
(กฎ C1 ของโปรเจกต์: "ดึงไม่สำเร็จ" ≠ "ไม่มีข้อมูล" ≠ "0")

แถบนี้อยู่บนสุดของ**ทุกหน้า** ของ dashboard และคนอ่าน "สี" ก่อนตัวเลขเสมอ

เทสต์ในไฟล์นี้ปักสามอย่าง:
  1. %เปลี่ยนแปลงคิดจากแท่งรายวัน 2 แท่งของชุดเดียวกัน (เครื่องหมายต้องตรงกับความจริง)
  2. คำนวณไม่ได้ = ``None`` เสมอ ห้ามเป็น ``0.0`` และต้องรอดไปถึง JSON เป็น ``null``
  3. นิยามเดียวกับ ``jobs/daily_check._yfinance_snapshot`` — ห้ามมีสูตรที่สามในระบบ
"""

from __future__ import annotations

import asyncio
import json

import pandas as pd
import pytest
import yfinance

from backend.routers import websocket as ws


# ---------------------------------------------------------------------------
# เครื่องมือ: ปลอม yfinance ให้คืน "แท่งจริง" ที่เรากำหนดเอง
#
# จงใจปลอมที่ระดับ ``yfinance`` ไม่ใช่ที่ ``ws.get_price_snapshots`` — เพราะสิ่งที่ต้อง
# พิสูจน์คือ *เส้นทางทั้งเส้น* (websocket → get_price_snapshots → yf.download) อ่านแท่ง
# รายวันจริง ไม่ใช่แค่ว่าฟังก์ชันชั้นบนส่งค่าต่อถูก
# ---------------------------------------------------------------------------
def _daily_bars(ticker: str, closes: list[float]) -> pd.DataFrame:
    """เฟรมแบบที่ ``yf.download(group_by="ticker")`` คืนจริง (คอลัมน์ MultiIndex)."""
    idx = pd.date_range("2026-08-03", periods=len(closes), freq="D")
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1_000] * len(closes),
        },
        index=idx,
    )
    df.columns = pd.MultiIndex.from_product([[ticker], df.columns])
    return df


class _FastInfoTrap(dict):
    """``fast_info`` ที่ระเบิดเมื่อถูกอ่าน — กันการถอยกลับไปใช้ quote endpoint.

    ค่าในนี้คือคู่ที่วัดได้จริงตอนตรวจ (last=100.69 / previous_close=100.76) ถ้าโค้ด
    กลับไปอ่าน ``fast_info`` อีก เทสต์จะล้มด้วยข้อความที่บอกสาเหตุตรง ๆ แทนที่จะ
    "ผ่าน" ด้วยตัวเลขที่ผิดเครื่องหมาย
    """

    def __getitem__(self, key):  # pragma: no cover - เส้นทางนี้คือเทสต์แดง
        raise AssertionError(
            f"แถบราคาสดกลับไปอ่าน fast_info[{key!r}] อีกแล้ว — ค่านั้นไม่ใช่แท่งปิด "
            "ก่อนหน้าราคาที่กำลังแสดง จึงพลิกเครื่องหมาย %เปลี่ยนแปลงได้ "
            "(AUDIT_ROUND2_2026-08-07)"
        )


class _TickerTrap:
    def __init__(self, *_args, **_kwargs) -> None:
        self.fast_info = _FastInfoTrap(last_price=100.69, previous_close=100.76)


@pytest.fixture
def bars(monkeypatch: pytest.MonkeyPatch):
    """คืนฟังก์ชันตั้งแท่งราคาของ ticker หนึ่งตัว แล้วดัก ``yf.Ticker`` ไม่ให้ถูกใช้."""

    store: dict[str, list[float]] = {}

    def fake_download(*_args, **kwargs) -> pd.DataFrame:
        tickers = kwargs.get("tickers") or []
        symbol = str(tickers[0]).strip().upper() if tickers else ""
        closes = store.get(symbol)
        if closes is None:
            return pd.DataFrame()
        return _daily_bars(symbol, closes)

    monkeypatch.setattr(yfinance, "download", fake_download)
    monkeypatch.setattr(yfinance, "Ticker", _TickerTrap)

    def _set(ticker: str, closes: list[float]) -> None:
        store[str(ticker).strip().upper()] = closes

    return _set


# ---------------------------------------------------------------------------
# 1. เครื่องหมายต้องตรงกับความจริง
# ---------------------------------------------------------------------------
def test_เปอร์เซ็นต์คิดจากแท่งปิดจริงไม่ใช่_fast_info(bars) -> None:
    """เคสที่วัดได้จริง: ปิด +0.69% (เขียว) — โค้ดเดิมประกาศ -0.07% (แดง)."""
    bars("VOO", [98.50, 99.10, 100.00, 100.69])

    row = ws._fetch_ticker_snapshot("VOO")

    assert row is not None
    assert row["price"] == 100.69
    assert row["change_pct"] == pytest.approx(0.69), (
        "%เปลี่ยนแปลงต้องคิดจาก (100.69-100.00)/100.00 = +0.69% "
        f"ไม่ใช่ค่าจาก quote endpoint (ได้ {row['change_pct']})"
    )
    assert row["change_pct"] > 0, "ETF ที่ปิดบวกต้องขึ้นแถบสีเขียว ไม่ใช่แดง"


def test_ขาลงก็ต้องได้เครื่องหมายลบจากแท่งจริง(bars) -> None:
    bars("SCHD", [30.00, 29.00, 28.71])

    row = ws._fetch_ticker_snapshot("SCHD")

    assert row is not None
    assert row["change_pct"] == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# 2. คำนวณไม่ได้ = None ห้ามเป็น 0.0
# ---------------------------------------------------------------------------
def test_มีแท่งเดียวต้องได้_change_pct_เป็น_None_ไม่ใช่ศูนย์(bars) -> None:
    """ETF ที่ผู้ให้ข้อมูลส่งมาแท่งเดียว = ไม่รู้ว่าขยับเท่าไร ไม่ใช่ "ไม่ขยับ"."""
    bars("GLDM", [64.20])

    row = ws._fetch_ticker_snapshot("GLDM")

    assert row is not None, "ราคายังใช้ได้ ห้ามทิ้งทั้ง ticker เพราะคำนวณ % ไม่ได้"
    assert row["price"] == 64.20
    assert row["change_pct"] is None, (
        "มีแท่งปิดแท่งเดียว ⇒ คำนวณ %เปลี่ยนแปลงไม่ได้ ต้องเป็น None "
        f"(ได้ {row['change_pct']!r} — 0.0 อ่านว่า 'ราคาไม่ขยับ')"
    )
    assert row["change_pct"] != 0.0
    assert row.get("note"), "ต้องมีข้อความไทยกำกับว่าทำไมไม่มีตัวเลข"


def test_ราคาอ้างอิงศูนย์หรือติดลบต้องได้_None_ไม่ใช่ศูนย์(bars) -> None:
    """จุดที่โค้ดเดิมเขียน ``if prev > 0 else 0.0`` ตรง ๆ."""
    bars("XLV", [0.0, 141.20])

    row = ws._fetch_ticker_snapshot("XLV")

    assert row is not None
    assert row["price"] == 141.20
    assert row["change_pct"] is None, (
        "ราคาอ้างอิง <= 0 คือคำนวณไม่ได้ ห้ามแปลงเป็น 0.0 "
        f"(ได้ {row['change_pct']!r})"
    )


def test_ดึงไม่ได้เลยต้องคืน_None_ทั้งก้อน(bars) -> None:
    """ไม่มีแท่งเลย ⇒ ``None`` เพื่อให้ผู้เรียกใส่ ``unavailable`` (ไม่ใช่ราคา 0)."""
    # ไม่ได้ตั้งแท่งให้ QQQM ⇒ fake download คืนเฟรมว่าง
    assert ws._fetch_ticker_snapshot("QQQM") is None


def test_yfinance_ระเบิดต้องคืน_None_ไม่ใช่ลากลูปตาย(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("yfinance ล่ม")

    monkeypatch.setattr(ws, "get_price_snapshots", boom)
    assert ws._fetch_ticker_snapshot("VOO") is None


# ---------------------------------------------------------------------------
# 3. ค่า None ต้องรอดไปถึงสายจริงเป็น JSON null
# ---------------------------------------------------------------------------
async def _capture_one_broadcast(monkeypatch: pytest.MonkeyPatch) -> dict:
    """รัน ``_price_broadcast_loop`` จนได้ข้อความแรกแล้วยกเลิก (ไม่รอ sleep 30 วิ)."""
    sent: list[dict] = []
    got = asyncio.Event()

    async def fake_broadcast(payload: dict) -> None:
        sent.append(payload)
        got.set()

    monkeypatch.setattr(ws.manager, "broadcast", fake_broadcast)
    task = asyncio.create_task(ws._price_broadcast_loop())
    try:
        await asyncio.wait_for(got.wait(), timeout=10.0)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    return sent[0]


async def test_ข้อความที่ส่งออกต้องเป็น_null_ไม่ใช่_0(bars) -> None:
    """end-to-end: ``change_pct`` ที่คำนวณไม่ได้ต้องออกไปเป็น ``null`` ใน JSON จริง."""
    bars("VOO", [100.00, 100.69])
    bars("GLDM", [64.20])  # แท่งเดียว = คำนวณ % ไม่ได้

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ws, "TICKERS", ["VOO", "GLDM"])
        payload = await _capture_one_broadcast(mp)

    text = json.dumps(payload, ensure_ascii=False)
    assert '"change_pct": null' in text, (
        "%เปลี่ยนแปลงที่คำนวณไม่ได้ต้องเดินทางถึงเบราว์เซอร์เป็น null "
        f"(ข้อความที่ส่งจริง: {text})"
    )

    parsed = json.loads(text)
    assert parsed["data"]["GLDM"]["change_pct"] is None
    assert parsed["data"]["GLDM"]["price"] == 64.20
    assert parsed["data"]["VOO"]["change_pct"] == pytest.approx(0.69)
    # ได้ราคาแล้ว = ไม่ใช่ "ดึงไม่ได้" — สามสถานะต้องแยกจากกัน
    assert parsed["unavailable"] == []


# ---------------------------------------------------------------------------
# 4. หนึ่งนิยามต่อหนึ่งแนวคิด (CLAUDE.md) — ห้ามมีสูตรที่สาม
# ---------------------------------------------------------------------------
def test_ตรงกับสูตรของ_daily_check_เป๊ะ(monkeypatch: pytest.MonkeyPatch) -> None:
    """แท่งชุดเดียวกัน → ``jobs/daily_check`` กับ WebSocket ต้องได้ตัวเลขเดียวกัน.

    ``jobs/daily_check._yfinance_snapshot`` แก้ปัญหานี้ไปแล้ว (FIX_PLAN 2.2/2.3) แต่
    WebSocket ถูกลืม ถ้าใครเขียนสูตรที่สามขึ้นมาใหม่ เทสต์นี้จะจับความต่างได้ทันที
    """
    from jobs import daily_check

    closes = [98.50, 99.10, 100.00, 100.69]

    def fake_download(*_args, **kwargs) -> pd.DataFrame:
        return _daily_bars(str(kwargs["tickers"][0]).strip().upper(), closes)

    class _TickerWithHistory:
        def __init__(self, *_a, **_k) -> None:
            pass

        def history(self, *_a, **_k) -> pd.DataFrame:
            idx = pd.date_range("2026-08-03", periods=len(closes), freq="D")
            return pd.DataFrame({"Close": closes}, index=idx)

    monkeypatch.setattr(yfinance, "download", fake_download)
    monkeypatch.setattr(yfinance, "Ticker", _TickerWithHistory)

    ws_row = ws._fetch_ticker_snapshot("VOO")
    job_row = daily_check._yfinance_snapshot("VOO")

    assert ws_row is not None
    assert ws_row["price"] == pytest.approx(round(job_row["price"], 2))
    assert ws_row["change_pct"] == pytest.approx(round(job_row["change_pct"], 2)), (
        "WebSocket กับ Discord รายวันต้องรายงาน %เปลี่ยนแปลงตัวเดียวกันจากแท่งชุดเดียวกัน "
        f"(ws={ws_row['change_pct']} vs daily_check={job_row['change_pct']})"
    )


def test_websocket_ไม่นำเข้า_yfinance_เองอีกแล้ว() -> None:
    """ปักว่า router ไม่มีเส้นทางดึงราคาของตัวเอง — ต้องผ่านนิยามกลางเท่านั้น."""
    assert not hasattr(ws, "yf"), (
        "backend/routers/websocket.py ไม่ควรถือ yfinance ไว้เอง "
        "ไม่งั้นสูตร %เปลี่ยนแปลงจะแตกออกเป็นอันที่สามได้อีก"
    )
    assert hasattr(ws, "get_price_snapshots")


# ---------------------------------------------------------------------------
# 5. ปลายทางบนหน้าจอ — การแก้ฝั่ง backend ไม่จบถ้า JS ยังอ่าน null เป็น 0
# ---------------------------------------------------------------------------
def test_แถบราคาต้องแสดง_change_pct_ที่เป็น_null_ว่าไม่ทราบ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """แถบราคาบนสุดของทุกหน้าต้องไม่วาด ``null`` เป็นบวกสีเขียว.

    router ส่ง ``change_pct: null`` มาแล้วอย่างถูกต้อง แต่ JS เดิมเขียน
    ``info.change_pct >= 0`` ซึ่งใน JavaScript ``null`` ถูก coerce เป็น ``0``
    ⇒ ``null >= 0`` เป็น **true** ⇒ ช่องที่ "คำนวณไม่ได้" ถูกวาดเป็น ``+null%``
    **สีเขียว** — คนอ่านสีก่อนตัวเลขเสมอ จึงอ่านได้ว่า "วันนี้บวก" ทั้งที่แปลว่าไม่ทราบ
    (AUDIT_ROUND2_2026-08-07) เท่ากับย้ายบั๊ก C1 จาก Python ไปไว้ในเบราว์เซอร์เฉย ๆ
    """
    app = pytest.importorskip("dashboard.app")

    captured: list[str] = []
    monkeypatch.setattr(app.components, "html", lambda html, **kwargs: captured.append(html))
    app._render_realtime_price_ticker_bar()

    html = captured[0]
    assert "change_pct === null" in html or "change_pct == null" in html, (
        "JS ต้องเช็ก null ก่อนเทียบ >= 0 ไม่งั้น null ถูกอ่านเป็น 0 แล้ววาดเป็นเขียว"
    )
    assert "ไม่ทราบ" in html, "ต้องมีข้อความไทยบอกว่า %เปลี่ยนแปลงไม่ทราบ"
    # ⚠ บรรทัดที่ทำให้ null กลายเป็น "เขียว" คือบรรทัดที่เลือก **สี** ไม่ใช่บรรทัด sign
    # เดิม assert เชิงลบตัวเดียวของเทสต์นี้ล็อกผิดบรรทัด: คืน
    # ``const color = info.change_pct >= 0 ? ...`` กลับมาแล้วยังเขียวทั้งไฟล์
    # (พิสูจน์ด้วย mutation จริง) ⇒ เทสต์ปลายทางที่แพงที่สุดไม่ได้กันบั๊กที่มันประกาศว่ากัน
    assert 'const color = info.change_pct >= 0' not in html, (
        "สีต้องตัดสินจากธง 'คำนวณไม่ได้' ก่อนเสมอ — เทียบ change_pct กับ 0 ตรง ๆ "
        "ทำให้ null (ซึ่ง JS แปลงเป็น 0) ได้สีเขียวอีกครั้ง"
    )
    assert "#8B949E" in html, "ต้องมีสีเทากลาง ๆ สำหรับช่องที่คำนวณ %เปลี่ยนแปลงไม่ได้"
    # ธงต้องเป็น "ตัวตัดสินสี" จริง ไม่ใช่ประกาศไว้เฉย ๆ แล้วทาสีจากตัวเลขอยู่ดี —
    # ตรวจแบบไม่ผูกกับการจัดบรรทัด (โค้ด JS ถูกฟอร์แมตใหม่ได้)
    normalized = " ".join(html.split())
    assert 'pctUnknown ? "#8B949E"' in normalized, (
        "ธง pctUnknown ต้องเป็นตัวเลือกสีโดยตรง ไม่งั้นสีเทาอาจถูกประกาศไว้แต่ไม่เคยถูกใช้"
    )
