# -*- coding: utf-8 -*-
"""G3 — ``/api/networth/current`` ห้ามตายเพราะอัตราแลกเปลี่ยน **ที่มันไม่ได้ใช้**.

การแก้ B9 ทำให้ ``utils.fx._config_fallback()`` โยน :class:`FxRateUnavailable`
เมื่อ ``display.default_fx_rate`` อยู่นอกช่วง 20–50 ซึ่งถูกต้องแล้ว — แต่
``networth_service._etf_assets_live()`` เรียก ``fx.get_usdthb()`` แบบไม่มีเงื่อนไข
**ก่อนจะรู้ด้วยซ้ำว่ามีอะไรต้องแปลงเป็นบาทหรือไม่** ⇒ สมุดว่าง (ไม่มี ETF สักตัว
คำตอบไม่ได้ใช้ FX เลย) ก็ยังระเบิด แล้ว router แปลงเป็น HTTP 500 ⇒ **เงินสด
สินทรัพย์นอก ETF และหนี้สินทั้งก้อนใน snapshot — ตัวเลขบาทล้วนที่ผู้ใช้บันทึกเอง
และไม่พึ่ง FX แม้แต่บาทเดียว — หายไปพร้อมกัน**

กฎข้อ 6: fail-closed ต้องปิดเฉพาะส่วนที่เชื่อถือไม่ได้ ไม่ใช่ปิดทั้งแอป

และเมื่อ FX จำเป็นจริง ๆ แล้วล้ม สถานะต้องแยกจากกันครบตามกฎข้อ 2
("ดึงไม่สำเร็จ" ≠ "ไม่มีข้อมูล" ≠ "คอนฟิกผิด"):

===========================  ==========  ============  ==========
สถานการณ์                     fx_rate     fx_is_live    fx_error
===========================  ==========  ============  ==========
ค่าสด                          ตัวเลข       ``True``     ``None``
ค่าสำรองจาก config             ตัวเลข       ``False``    ``None``
**ไม่ได้ใช้ FX เลย**            ``None``    ``None``     ``None``
**ใช้ FX ไม่ได้**               ``None``    ``None``     ข้อความไทย
===========================  ==========  ============  ==========

ไม่มีเคสไหนยิง network / LLM / webhook จริง และไม่แตะไฟล์ข้อมูลจริงของผู้ใช้
(สมุดถูก stub, ``config.json`` ถูกแทนที่ระดับฟังก์ชัน, SQLite อยู่ใน ``tmp_path``)
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models.orm import NetWorthSnapshot
from backend.services import networth_service
from utils import fx
from utils.fx import FxRate, FxRateUnavailable

RATE = 33.0

CASH = {"name": "เงินสด", "type": "cash", "value_thb": 500_000.0}
VOO_SNAPSHOT = {"name": "VOO", "type": "etf", "value_thb": 100_000.0}
MORTGAGE = {"name": "สินเชื่อบ้าน", "value_thb": 2_000_000.0}


@pytest.fixture()
def db(tmp_path):
    """SQLite ชั่วคราวใน tmp_path — ห้ามแตะฐานจริงของผู้ใช้."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'networth_g3.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_network_fx(monkeypatch):
    """ตัดการยิงเน็ตของ ``utils.fx`` และล้างแคชก่อน/หลังทุกเคส."""
    monkeypatch.setattr(fx, "_cached", None, raising=False)
    monkeypatch.setattr(fx, "_fetch_live", lambda: None)
    yield
    fx._cached = None


def _break_fx(monkeypatch) -> None:
    """FX พังจริงทั้งสองทาง: ดึงสดไม่ได้ **และ** ค่าสำรองใน config อยู่นอกช่วง.

    ใช้ ``3.35`` (พิมพ์ตกจุดจาก ``33.5``) แบบเดียวกับหลักฐานในผลตรวจ —
    patch ที่ ``fx.load_config`` เท่านั้น ไม่แตะ ``config.json`` จริง
    """
    monkeypatch.setattr(fx, "load_config", lambda: {"display": {"default_fx_rate": 3.35}})


def _holding(ticker: str, value_usd: float | None, price_ok: bool = True) -> dict:
    return {
        "ticker": ticker,
        "shares": 10.0,
        "current_price_usd": (value_usd / 10.0) if value_usd is not None else None,
        "current_value_usd": value_usd,
        "price_ok": price_ok,
    }


def _stub_holdings(monkeypatch, holdings: list[dict], **report) -> None:
    payload = {
        "holdings": holdings,
        "skipped_rows": report.get("skipped_rows", []),
        "skipped_reason": report.get("skipped_reason", ""),
    }
    monkeypatch.setattr(networth_service, "get_holdings", lambda: payload)


def _save_snapshot_row(db, *, snapshot_date: str, assets: list[dict], liabilities=()):
    liabilities = list(liabilities)
    total_assets = sum(a["value_thb"] for a in assets)
    total_liabilities = sum(l["value_thb"] for l in liabilities)
    row = NetWorthSnapshot(
        snapshot_date=snapshot_date,
        total_assets_thb=total_assets,
        total_liabilities_thb=total_liabilities,
        net_worth_thb=total_assets - total_liabilities,
        assets_json=json.dumps(assets, ensure_ascii=False),
        liabilities_json=json.dumps(liabilities, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    return row


# ===========================================================================
# 1. ไม่มีอะไรต้องแปลงเป็นบาท ⇒ ห้ามแตะ FX เลย (และห้ามตายเพราะ FX)
# ===========================================================================


class TestFxIsNotFetchedWhenNothingNeedsIt:
    def test_empty_ledger_never_calls_fx(self, db, monkeypatch):
        """สมุดว่าง = ไม่มีดอลลาร์สักก้อนให้แปลง — การยิง FX คือการยิงฟรี ๆ."""

        def _boom():
            raise AssertionError("ห้ามเรียก fx.get_usdthb เมื่อไม่มีอะไรต้องแปลงเป็นบาท")

        monkeypatch.setattr(networth_service.fx, "get_usdthb", _boom)
        _stub_holdings(monkeypatch, [])
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH])

        resp = networth_service.get_current(db)

        assert resp.etf_status == "no_holdings"
        assert resp.fx_rate is None, "ไม่ได้ใช้อัตราแลกเปลี่ยน ⇒ ห้ามรายงานว่าใช้"
        assert resp.fx_is_live is None
        assert resp.fx_error is None

    def test_all_prices_missing_never_calls_fx(self, db, monkeypatch):
        """ดึงราคาไม่ได้สักกอง = ไม่มีมูลค่า USD ให้แปลงเช่นกัน."""

        def _boom():
            raise AssertionError("ห้ามเรียก fx.get_usdthb เมื่อไม่มีราคาให้แปลง")

        monkeypatch.setattr(networth_service.fx, "get_usdthb", _boom)
        _stub_holdings(monkeypatch, [_holding(t, None, price_ok=False) for t in ("VOO", "SCHD")])
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH])

        resp = networth_service.get_current(db)

        assert resp.etf_status == "unavailable"
        assert resp.missing_prices == ["VOO", "SCHD"]
        assert resp.fx_rate is None and resp.fx_is_live is None and resp.fx_error is None
        assert not any("อัตราแลกเปลี่ยนสำรอง" in w for w in resp.warnings), (
            "ไม่ได้ใช้ FX เลย แต่ไปเตือนว่าใช้ค่าสำรอง = ข้อมูลผิด"
        )

    def test_from_snapshot_path_never_calls_fx(self, db, monkeypatch):
        """คำตอบมาจาก snapshot ล้วน (บาทอยู่แล้ว) — ไม่ต้องใช้ FX เหมือนกัน."""

        def _boom():
            raise AssertionError("ห้ามเรียก fx.get_usdthb เมื่อยอด ETF มาจาก snapshot")

        monkeypatch.setattr(networth_service.fx, "get_usdthb", _boom)
        _stub_holdings(monkeypatch, [_holding("VOO", None, price_ok=False)])
        _save_snapshot_row(db, snapshot_date="2026-07-01", assets=[CASH, VOO_SNAPSHOT])

        resp = networth_service.get_current(db)

        assert resp.etf_status == "from_snapshot"
        assert resp.total_assets_thb == pytest.approx(600_000.0)
        assert resp.fx_rate is None and resp.fx_is_live is None


class TestBrokenFxDoesNotKillTheWholeAnswer:
    def test_empty_ledger_survives_a_broken_fallback_rate(self, db, monkeypatch):
        """หลักฐานตรงจากผลตรวจ: FX สดล่ม + ``default_fx_rate=3.35`` ⇒ เดิม 500."""
        _break_fx(monkeypatch)
        _stub_holdings(monkeypatch, [])
        _save_snapshot_row(
            db, snapshot_date=date.today().isoformat(), assets=[CASH], liabilities=[MORTGAGE]
        )

        resp = networth_service.get_current(db)

        assert resp.net_worth_thb == pytest.approx(-1_500_000.0), (
            "เงินสด/หนี้สินเป็นตัวเลขบาทที่บันทึกเอง ไม่พึ่ง FX — ห้ามหายไปกับความล้มเหลวของ FX"
        )
        assert resp.etf_status == "no_holdings"
        assert resp.fx_error is None, "ไม่ได้เรียกใช้ FX เลย ⇒ ไม่มีความล้มเหลวของ FX ให้รายงาน"

    def test_router_returns_200_not_500(self, db, monkeypatch):
        """เส้นทางจริงของผู้ใช้: ``GET /api/networth/current``."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.database import get_db
        from backend.routers import networth as networth_router

        _break_fx(monkeypatch)
        _stub_holdings(monkeypatch, [])
        _save_snapshot_row(
            db, snapshot_date=date.today().isoformat(), assets=[CASH], liabilities=[MORTGAGE]
        )

        app = FastAPI()
        app.include_router(networth_router.router)
        app.dependency_overrides[get_db] = lambda: db
        resp = TestClient(app, raise_server_exceptions=False).get("/api/networth/current")

        assert resp.status_code == 200, resp.text[:400]
        body = resp.json()
        assert body["net_worth_thb"] == pytest.approx(-1_500_000.0)
        assert body["fx_rate"] is None


# ===========================================================================
# 2. FX จำเป็นจริงแล้วล้ม ⇒ 200 + สถานะที่แยก "แปลงไม่ได้" ออกจาก "ไม่มีราคา"
# ===========================================================================


class TestFxFailureWhenItIsActuallyNeeded:
    def test_priced_holdings_report_fx_unavailable_instead_of_500(self, db, monkeypatch):
        _break_fx(monkeypatch)
        _stub_holdings(monkeypatch, [_holding("VOO", 10_000.0), _holding("SCHD", 5_000.0)])
        _save_snapshot_row(
            db, snapshot_date=date.today().isoformat(), assets=[CASH], liabilities=[MORTGAGE]
        )

        resp = networth_service.get_current(db)

        assert resp.etf_status == "fx_unavailable"
        assert resp.etf_live is False
        assert resp.fx_rate is None and resp.fx_is_live is None
        assert resp.fx_error and "อัตราแลกเปลี่ยน" in resp.fx_error
        assert resp.missing_prices == [], (
            "ราคาดึงมาได้ตามปกติ — ห้ามยัดลง missing_prices ซึ่งแปลว่า 'ดึงราคาไม่ได้'"
        )
        # ส่วนที่ไม่พึ่ง FX ต้องยังตอบได้ตามปกติ (fail-closed เฉพาะส่วนที่ใช้ FX)
        assert resp.net_worth_thb == pytest.approx(-1_500_000.0)
        assert [a.name for a in resp.assets] == ["เงินสด"]
        assert any("VOO" in w and "บาท" in w for w in resp.warnings), (
            "ต้องบอกเป็นภาษาไทยว่า ETF ตัวไหนหายไปจากยอดและเพราะอะไร"
        )

    def test_fx_failure_falls_back_to_snapshot_etf_and_still_explains_why(self, db, monkeypatch):
        _break_fx(monkeypatch)
        _stub_holdings(monkeypatch, [_holding("VOO", 10_000.0)])
        _save_snapshot_row(db, snapshot_date="2026-07-01", assets=[CASH, VOO_SNAPSHOT])

        resp = networth_service.get_current(db)

        assert resp.etf_status == "from_snapshot"
        assert resp.total_assets_thb == pytest.approx(600_000.0)
        assert resp.fx_error, "ที่มาของการถอยไปใช้ snapshot ต้องอ่านได้แบบ machine-readable ด้วย"
        assert any("2026-07-01" in w for w in resp.warnings)

    def test_ledger_that_cannot_be_valued_without_fx_is_not_a_500(self, db, monkeypatch):
        """ทางเข้าที่สอง: ``get_holdings()`` เองโยน ``FxRateUnavailable``.

        ``tracker.get_portfolio_summary()`` แปลงมูลค่าเป็นบาทตั้งแต่ต้นทาง สมุดที่มี
        ธุรกรรมจริงจึงล้มก่อนถึงบรรทัด ``fx.get_usdthb()`` ของไฟล์นี้ — ผลลัพธ์ต้อง
        เหมือนกัน คือเงินสด/หนี้สินยังอยู่ ไม่ใช่ทั้งคำตอบหายไป
        """

        def _raise():
            raise FxRateUnavailable("ดึงอัตราแลกเปลี่ยน THB/USD สดไม่ได้ และค่าสำรองใช้ไม่ได้")

        monkeypatch.setattr(networth_service, "get_holdings", _raise)
        _save_snapshot_row(
            db, snapshot_date=date.today().isoformat(), assets=[CASH], liabilities=[MORTGAGE]
        )

        resp = networth_service.get_current(db)

        assert resp.net_worth_thb == pytest.approx(-1_500_000.0)
        assert resp.etf_status == "fx_unavailable"
        assert resp.fx_error
        assert any("อัตราแลกเปลี่ยน" in w for w in resp.warnings)

    def test_fx_unavailable_differs_from_prices_unavailable(self, db, monkeypatch):
        """กฎข้อ 2 — "แปลงเป็นบาทไม่ได้" กับ "ดึงราคาไม่ได้" ห้ามยุบเป็นสถานะเดียว."""
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH])

        _break_fx(monkeypatch)
        _stub_holdings(monkeypatch, [_holding("VOO", 10_000.0)])
        fx_broken = networth_service.get_current(db)

        monkeypatch.setattr(networth_service.fx, "get_usdthb", lambda: FxRate(RATE, True))
        _stub_holdings(monkeypatch, [_holding("VOO", None, price_ok=False)])
        price_broken = networth_service.get_current(db)

        assert fx_broken.etf_status != price_broken.etf_status
        assert price_broken.etf_status == "unavailable"
        assert price_broken.missing_prices == ["VOO"]
        assert fx_broken.missing_prices == []


# ===========================================================================
# 3. เคสปกติที่ต้องไม่เปลี่ยน — FX ยังถูกใช้และยังรายงานที่มาเหมือนเดิม
# ===========================================================================


class TestNormalPathsUnchanged:
    def test_live_rate_is_still_used_and_reported(self, db, monkeypatch):
        calls: list[int] = []

        def _rate():
            calls.append(1)
            return FxRate(RATE, True)

        monkeypatch.setattr(networth_service.fx, "get_usdthb", _rate)
        _stub_holdings(monkeypatch, [_holding("VOO", 10_000.0)])
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH])

        resp = networth_service.get_current(db)

        assert calls, "มีมูลค่า USD ให้แปลง — FX ต้องถูกเรียกจริง"
        assert resp.etf_status == "live"
        assert resp.fx_rate == pytest.approx(RATE)
        assert resp.fx_is_live is True
        assert resp.fx_error is None
        assert [(a.name, a.value_thb) for a in resp.assets if a.type == "etf"] == [
            ("VOO", 330_000.0)
        ]

    def test_fallback_rate_is_still_flagged(self, db, monkeypatch):
        monkeypatch.setattr(networth_service.fx, "get_usdthb", lambda: FxRate(33.5, False))
        _stub_holdings(monkeypatch, [_holding("VOO", 10_000.0)])

        resp = networth_service.get_current(db)

        assert resp.fx_is_live is False
        assert resp.fx_rate == pytest.approx(33.5)
        assert resp.fx_error is None, "ค่าสำรองที่ใช้ได้ ≠ ไม่มีอัตราให้ใช้"
        assert any("อัตราแลกเปลี่ยนสำรอง" in w for w in resp.warnings)

    def test_response_stays_json_serialisable(self, db, monkeypatch):
        _break_fx(monkeypatch)
        _stub_holdings(monkeypatch, [_holding("VOO", 10_000.0)])
        json.dumps(networth_service.get_current(db).model_dump())
