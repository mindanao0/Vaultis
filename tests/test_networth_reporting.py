# -*- coding: utf-8 -*-
"""ทดสอบ A5 (H11 + L-NW-1/2/3) — Net Worth ห้ามตัด ETF ทิ้งเงียบ ๆ.

``_etf_assets_live()`` กรอง ``price_ok`` ทิ้งโดยไม่รายงานออกไปเลย และ ``etf_live``
เป็น ``True`` ทันทีที่มี ETF **ตัวใดตัวหนึ่ง** มีราคา — การข้ามโดยไม่รายงาน
ให้ผลตัวเลขเหมือนกับการนับเป็น 0 ทุกประการ เพราะมันหายจากตัวตั้งของ
``total_assets_thb``/``net_worth_thb`` (AUDIT_2026-08-06 ข้อ H11)

พี่น้องของมันทำถูกอยู่แล้ว: ``portfolio_service.get_portfolio_summary()`` จาก
สมุดเล่มเดียวกันคืน ``missing_prices``/``skipped_rows``/``skipped_reason`` ครบ
— **ชื่อคีย์ต้องเป็นชุดเดียวกัน ห้ามตั้งชื่อใหม่**
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models.networth_models import Asset, Liability, SnapshotRequest
from backend.models.orm import NetWorthSnapshot
from backend.services import networth_service
from utils.fx import FxRate

RATE = 33.0


@pytest.fixture()
def db(tmp_path):
    """SQLite ชั่วคราวใน tmp_path — ห้ามแตะฐานจริงของผู้ใช้ (AUDIT ข้อ 0.1)."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'networth_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _fx_live(monkeypatch):
    """FX คงที่ ไม่แตะเน็ต — เคสที่อยากได้ค่าสำรองจะ override เอง."""
    monkeypatch.setattr(networth_service.fx, "get_usdthb", lambda: FxRate(RATE, True))


def _holding(ticker: str, value_usd: float | None, price_ok: bool = True) -> dict:
    """แถว holding รูปทรงเดียวกับที่ ``portfolio_service.get_holdings()`` คืน."""
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


def _save_snapshot_row(db, *, snapshot_date: str, assets: list[dict], liabilities: list[dict]):
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


CASH = {"name": "เงินสด", "type": "cash", "value_thb": 500_000.0}
MORTGAGE = {"name": "สินเชื่อบ้าน", "value_thb": 2_000_000.0}

# เคส A ของผลตรวจ: 4 ตัวมีราคา GLDM ดึงไม่ได้ (มูลค่าหายไป 528,924 บาท)
FULL_BOOK = [
    _holding("VOO", 10_000.0),
    _holding("SCHD", 5_000.0),
    _holding("QQQM", 4_000.0),
    _holding("XLV", 3_000.0),
    _holding("GLDM", 16_028.0),
]
BROKEN_GLDM = FULL_BOOK[:4] + [_holding("GLDM", None, price_ok=False)]


class TestMissingPricesAreReported:
    """H11 — ETF ที่ดึงราคาไม่ได้ต้องมีช่องบอก ห้ามหายเงียบ."""

    def test_partial_prices_reported_with_same_keys_as_portfolio(self, db, monkeypatch):
        _stub_holdings(monkeypatch, BROKEN_GLDM)
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH], liabilities=[MORTGAGE])

        resp = networth_service.get_current(db)

        # ตัวเลขยังเป็นยอดที่นับได้จริง — แต่ต้องบอกว่ามันไม่ครบ
        assert resp.net_worth_thb == pytest.approx(-774_000.0)
        assert resp.missing_prices == ["GLDM"], "ETF ที่ดึงราคาไม่ได้ต้องถูกรายงาน ไม่ใช่หายเงียบ"
        assert resp.etf_status == "partial"
        assert resp.etf_live is False, "มีตัวที่ราคาหาย = ยังไม่ใช่ 'สด' ทั้งพอร์ต"
        assert any("GLDM" in w for w in resp.warnings), "ต้องมีข้อความไทยพร้อมแสดงบนหน้าจอ"

    def test_all_prices_live(self, db, monkeypatch):
        _stub_holdings(monkeypatch, FULL_BOOK)
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH], liabilities=[MORTGAGE])

        resp = networth_service.get_current(db)

        assert resp.missing_prices == []
        assert resp.etf_status == "live"
        assert resp.etf_live is True

    def test_skipped_rows_pass_through(self, db, monkeypatch):
        """แถวธุรกรรมที่ tracker ตัดทิ้งต้องเดินทางถึงผู้เรียก API เหมือนฝั่ง portfolio."""
        _stub_holdings(
            monkeypatch,
            FULL_BOOK,
            skipped_rows=[{"tx_id": "bad1", "ticker": "QQQM", "missing_fields": ["fx_rate_thb"]}],
            skipped_reason="ข้ามธุรกรรม 1 รายการ (QQQM) เพราะข้อมูลไม่ครบ",
        )
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH], liabilities=[])

        resp = networth_service.get_current(db)

        assert resp.skipped_rows and resp.skipped_rows[0]["tx_id"] == "bad1"
        assert "QQQM" in resp.skipped_reason
        assert any("QQQM" in w for w in resp.warnings)

    def test_broken_prices_differ_from_empty_ledger(self, db, monkeypatch):
        """เคส B/C ของผลตรวจ: เดิม ``model_dump()`` เท่ากันทุกฟิลด์."""
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH], liabilities=[MORTGAGE])

        _stub_holdings(monkeypatch, [_holding(t, None, price_ok=False) for t in ("VOO", "SCHD")])
        broken = networth_service.get_current(db)

        _stub_holdings(monkeypatch, [])
        empty = networth_service.get_current(db)

        assert broken.model_dump() != empty.model_dump(), "'ดึงไม่ได้' ต้องแยกออกจาก 'ไม่มี'"
        assert broken.etf_status == "unavailable"
        assert broken.missing_prices == ["VOO", "SCHD"]
        assert empty.etf_status == "no_holdings"
        assert empty.missing_prices == []

    def test_falls_back_to_snapshot_etf_when_all_prices_fail(self, db, monkeypatch):
        """เคส D: คอมเมนต์ ``etf_live`` สัญญา fallback ไป snapshot ไว้ แต่ไม่เคยมี."""
        _stub_holdings(monkeypatch, [_holding("VOO", None, price_ok=False)])
        _save_snapshot_row(
            db,
            snapshot_date="2026-07-01",
            assets=[CASH, {"name": "VOO", "type": "etf", "value_thb": 100_000.0}],
            liabilities=[],
        )

        resp = networth_service.get_current(db)

        assert resp.etf_status == "from_snapshot"
        assert resp.etf_live is False
        assert [a.name for a in resp.assets if a.type == "etf"] == ["VOO"]
        assert resp.total_assets_thb == pytest.approx(600_000.0)
        assert resp.as_of_snapshot_date == "2026-07-01"
        assert any("2026-07-01" in w for w in resp.warnings)

    def test_response_is_json_serialisable(self, db, monkeypatch):
        _stub_holdings(monkeypatch, BROKEN_GLDM, skipped_rows=[{"tx_id": "x", "missing_fields": ["a"]}])
        resp = networth_service.get_current(db)
        json.dumps(resp.model_dump())


class TestFxSourceReported:
    """L-NW-2 — ทิ้ง ``is_live`` ของอัตราแลกเปลี่ยนไม่ได้ ตัวเลขบาทอาจคลาดเคลื่อน."""

    def test_live_rate(self, db, monkeypatch):
        _stub_holdings(monkeypatch, FULL_BOOK)
        resp = networth_service.get_current(db)
        assert resp.fx_rate == pytest.approx(RATE)
        assert resp.fx_is_live is True

    def test_fallback_rate_is_flagged(self, db, monkeypatch):
        monkeypatch.setattr(networth_service.fx, "get_usdthb", lambda: FxRate(33.5, False))
        _stub_holdings(monkeypatch, FULL_BOOK)

        resp = networth_service.get_current(db)

        assert resp.fx_is_live is False
        assert any("อัตราแลกเปลี่ยน" in w for w in resp.warnings)


class TestSnapshotAge:
    """L-NW-3 — ``snapshot_date = วันนี้`` ให้กับข้อมูลเก่าแค่ไหนก็ได้."""

    def test_old_snapshot_is_dated_and_flagged(self, db, monkeypatch):
        _stub_holdings(monkeypatch, FULL_BOOK)
        _save_snapshot_row(db, snapshot_date="2019-01-01", assets=[CASH], liabilities=[MORTGAGE])

        resp = networth_service.get_current(db)

        assert resp.snapshot_date == date.today().isoformat()
        assert resp.as_of_snapshot_date == "2019-01-01"
        assert resp.snapshot_age_days > 2_000
        assert resp.snapshot_stale is True
        assert any("2019-01-01" in w for w in resp.warnings)

    def test_no_snapshot_at_all_is_announced(self, db, monkeypatch):
        """ไม่มี snapshot = ไม่มีเงินสด/หนี้สินในตัวเลข ต้องบอก ไม่ใช่โชว์ว่าหนี้เป็นศูนย์."""
        _stub_holdings(monkeypatch, FULL_BOOK)

        resp = networth_service.get_current(db)

        assert resp.as_of_snapshot_date is None
        assert resp.liabilities == []
        assert any("snapshot" in w for w in resp.warnings)


class TestHistoryCutoff:
    """L-NW-1 — ``months × 30`` วัน ตัดข้อมูลจริงทิ้ง (120 เดือนขาดไป 52 วัน)."""

    @staticmethod
    def _years_back(d: date, years: int) -> date:
        try:
            return d.replace(year=d.year - years)
        except ValueError:  # 29 ก.พ.
            return d.replace(year=d.year - years, day=28)

    def test_twelve_months_means_one_calendar_year(self, db, monkeypatch):
        _stub_holdings(monkeypatch, [])
        inside = self._years_back(date.today(), 1) + timedelta(days=2)
        _save_snapshot_row(db, snapshot_date=inside.isoformat(), assets=[CASH], liabilities=[])

        history = networth_service.get_history(db, months=12)

        assert [h.snapshot_date for h in history] == [inside.isoformat()]

    def test_ten_years_means_ten_calendar_years(self, db, monkeypatch):
        _stub_holdings(monkeypatch, [])
        inside = self._years_back(date.today(), 10) + timedelta(days=2)
        _save_snapshot_row(db, snapshot_date=inside.isoformat(), assets=[CASH], liabilities=[])

        history = networth_service.get_history(db, months=120)

        assert [h.snapshot_date for h in history] == [inside.isoformat()]

    def test_older_than_cutoff_is_excluded(self, db, monkeypatch):
        _stub_holdings(monkeypatch, [])
        outside = self._years_back(date.today(), 1) - timedelta(days=2)
        _save_snapshot_row(db, snapshot_date=outside.isoformat(), assets=[CASH], liabilities=[])

        assert networth_service.get_history(db, months=12) == []


class TestSaveSnapshotWarns:
    """H11 ข้อ 3 — snapshot ที่ ETF ขาดไปจะถูกตรึงไว้ถาวร ต้องเตือนตอนบันทึก."""

    def test_warns_when_a_ticker_has_no_price(self, db, monkeypatch):
        _stub_holdings(monkeypatch, BROKEN_GLDM)
        payload = SnapshotRequest(
            assets=[Asset(**CASH), Asset(name="VOO", type="etf", value_thb=330_000.0)],
            liabilities=[Liability(**MORTGAGE)],
        )

        resp = networth_service.save_snapshot(db, payload)

        assert resp.missing_prices == ["GLDM"]
        assert resp.warnings, "ต้องเตือนก่อนตัวเลขที่ไม่ครบจะถูกตรึงถาวร"
        assert db.query(NetWorthSnapshot).count() == 1, "เตือนแล้วยังต้องบันทึกให้"

    def test_clean_book_saves_without_warning(self, db, monkeypatch):
        _stub_holdings(monkeypatch, FULL_BOOK)
        payload = SnapshotRequest(
            assets=[Asset(**CASH), Asset(name="VOO", type="etf", value_thb=330_000.0)],
            liabilities=[],
        )

        resp = networth_service.save_snapshot(db, payload)

        assert resp.missing_prices == []
        assert resp.warnings == []
        assert resp.etf_status == "from_snapshot"

    def test_no_etf_in_payload_does_not_touch_the_ledger(self, db, monkeypatch):
        """ไม่มี ETF ในก้อนที่บันทึก = ไม่มีเหตุให้ไปดึงราคา (ห้ามยิงเน็ตฟรี ๆ)."""

        def _boom():
            raise AssertionError("ห้ามเรียก get_holdings เมื่อ snapshot ไม่มี ETF")

        monkeypatch.setattr(networth_service, "get_holdings", _boom)
        payload = SnapshotRequest(assets=[Asset(**CASH)], liabilities=[Liability(**MORTGAGE)])

        resp = networth_service.save_snapshot(db, payload)

        assert resp.net_worth_thb == pytest.approx(-1_500_000.0)
        assert resp.warnings == []
