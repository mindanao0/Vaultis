# -*- coding: utf-8 -*-
"""K2 — Net Worth: 4 รูที่ทำให้คำตอบ "ดูปกติ" ทั้งที่ข้อมูลหายหรือบอกไม่ได้.

ทุกเคสในไฟล์นี้เขียนให้ **แดงก่อนแก้** และผูกกับข้อใดข้อหนึ่งใน 4 ข้อ:

1. ETF ที่ผู้ใช้กรอกเองใน snapshot หายเงียบเมื่อสมุดธุรกรรมไม่มี ETF
   (snapshot = เงินสด 500,000 + VOO 100,000, ledger ว่าง → คืนแต่เงินสด)
2. ``etf_status="no_holdings"`` โกหกเมื่อ tracker ตัดทุกแถวทิ้ง
   (holdings=[] + skipped_rows ไม่ว่าง = "อ่านสมุดไม่ได้" ไม่ใช่ "ไม่มี ETF")
3. ``snapshot_date`` ไม่ถูก validate เลย → วันที่อนาคตผ่านฉลุย อายุติดลบ
4. ``snapshot_stale: bool`` แทน "ไม่รู้อายุ" ไม่ได้ → วันที่อ่านไม่ออกกลายเป็น
   "ยังใหม่" ทั้งที่ความจริงคือ "บอกไม่ได้"

ไม่มีเคสไหนยิง network / LLM / webhook จริง และไม่แตะฐานข้อมูลจริง
(SQLite ชั่วคราวใน ``tmp_path`` เท่านั้น)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ``backend.database`` สร้างไฟล์ SQLite ตอน import — ชี้ไป tmp เสมอ ห้ามแตะฐานจริง
os.environ.setdefault("VAULTIS_DB_PATH", str(Path(tempfile.gettempdir()) / "vaultis_test_k2.db"))

from backend.database import Base  # noqa: E402
from backend.models.networth_models import Asset, Liability, SnapshotRequest  # noqa: E402
from backend.models.orm import NetWorthSnapshot  # noqa: E402
from backend.services import networth_service  # noqa: E402
from utils.fx import FxRate  # noqa: E402

RATE = 33.0

CASH = {"name": "เงินสด", "type": "cash", "value_thb": 500_000.0}
VOO_SNAPSHOT = {"name": "VOO", "type": "etf", "value_thb": 100_000.0}
MORTGAGE = {"name": "สินเชื่อบ้าน", "value_thb": 2_000_000.0}


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'networth_k2.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _fx_live(monkeypatch):
    monkeypatch.setattr(networth_service.fx, "get_usdthb", lambda: FxRate(RATE, True))


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


def _save_snapshot_row(db, *, snapshot_date: str, assets: list[dict], liabilities: list[dict] = ()):
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


ALL_ROWS_SKIPPED = {
    "skipped_rows": [
        {"tx_id": "t1", "ticker": "VOO", "missing_fields": ["shares"]},
        {"tx_id": "t2", "ticker": "GLDM", "missing_fields": ["price_usd"]},
    ],
    "skipped_reason": "ข้ามธุรกรรม 2 รายการ (VOO, GLDM) เพราะข้อมูลไม่ครบ",
}


# ===========================================================================
# ข้อ 1 — ETF ที่กรอกเองใน snapshot ห้ามหายเงียบเมื่อสมุดไม่มี ETF
# ===========================================================================


class TestSnapshotEtfSurvivesEmptyLedger:
    def test_snapshot_etf_is_counted_when_ledger_has_no_etf(self, db, monkeypatch):
        """สมุดว่าง แต่ผู้ใช้กรอก VOO 100,000 ไว้เอง → ต้องอยู่ในยอด ไม่ใช่หายไป."""
        _stub_holdings(monkeypatch, [])
        _save_snapshot_row(
            db, snapshot_date="2026-07-01", assets=[CASH, VOO_SNAPSHOT], liabilities=[MORTGAGE]
        )

        resp = networth_service.get_current(db)

        assert [a.name for a in resp.assets if a.type == "etf"] == ["VOO"], (
            "ETF ที่ผู้ใช้กรอกเองใน snapshot หายไปจากคำตอบ"
        )
        assert resp.total_assets_thb == pytest.approx(600_000.0)
        assert resp.net_worth_thb == pytest.approx(-1_400_000.0)
        assert resp.etf_status == "from_snapshot", "ตัวเลข ETF ก้อนนี้มาจาก snapshot ต้องบอกให้ตรง"
        assert resp.etf_live is False
        assert any("2026-07-01" in w for w in resp.warnings), "ต้องบอกว่ามูลค่านี้ไม่ใช่ราคาสด"

    def test_empty_ledger_and_no_snapshot_etf_is_still_no_holdings(self, db, monkeypatch):
        """เคสปกติที่ต้องไม่เปลี่ยน: ไม่มี ETF ทั้งสองที่ = ``no_holdings`` เหมือนเดิม."""
        _stub_holdings(monkeypatch, [])
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH])

        resp = networth_service.get_current(db)

        assert resp.etf_status == "no_holdings"
        assert [a.name for a in resp.assets] == ["เงินสด"]
        assert resp.total_assets_thb == pytest.approx(500_000.0)

    def test_live_prices_win_and_snapshot_etf_is_not_double_counted(self, db, monkeypatch):
        """เคสปกติที่ต้องไม่เปลี่ยน: มีราคาสด → ห้ามบวก ETF จาก snapshot ซ้ำเข้าไปอีก."""
        _stub_holdings(monkeypatch, [_holding("VOO", 10_000.0)])
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH, VOO_SNAPSHOT])

        resp = networth_service.get_current(db)

        assert resp.etf_status == "live"
        assert [(a.name, a.value_thb) for a in resp.assets if a.type == "etf"] == [
            ("VOO", 330_000.0)
        ]
        assert resp.total_assets_thb == pytest.approx(830_000.0)

    def test_manual_etf_not_in_ledger_is_announced_not_dropped_silently(self, db, monkeypatch):
        """ETF ในสมุดมีราคาสด แต่ snapshot มีตัวที่ไม่ได้อยู่ในสมุด → ต้องบอกว่าไม่ได้นับ."""
        _stub_holdings(monkeypatch, [_holding("VOO", 10_000.0)])
        _save_snapshot_row(
            db,
            snapshot_date=date.today().isoformat(),
            assets=[CASH, VOO_SNAPSHOT, {"name": "กองทุน SSF", "type": "etf", "value_thb": 80_000.0}],
        )

        resp = networth_service.get_current(db)

        assert any("SSF" in w for w in resp.warnings), (
            "ETF ที่กรอกเองแต่ไม่ได้เข้ายอดรวม ต้องมีคำเตือน ห้ามหายเงียบ"
        )


# ===========================================================================
# ข้อ 2 — tracker ตัดทุกแถวทิ้ง ≠ สมุดไม่มี ETF
# ===========================================================================


class TestAllRowsSkippedIsNotNoHoldings:
    def test_status_does_not_claim_no_holdings(self, db, monkeypatch):
        _stub_holdings(monkeypatch, [], **ALL_ROWS_SKIPPED)
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH])

        resp = networth_service.get_current(db)

        assert resp.etf_status != "no_holdings", (
            "อ่านสมุดไม่ได้สักแถว แต่คำตอบบอกว่า 'สมุดไม่มี ETF' — โกหก"
        )
        assert resp.etf_status == "ledger_unreadable"
        assert resp.etf_live is False
        assert resp.skipped_rows, "แถวที่ถูกตัดต้องเดินทางถึงผู้เรียก"
        assert any("VOO" in w for w in resp.warnings)

    def test_differs_from_a_truly_empty_ledger(self, db, monkeypatch):
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH])

        _stub_holdings(monkeypatch, [], **ALL_ROWS_SKIPPED)
        unreadable = networth_service.get_current(db)

        _stub_holdings(monkeypatch, [])
        empty = networth_service.get_current(db)

        assert unreadable.etf_status != empty.etf_status, "'อ่านไม่ได้' ต้องแยกจาก 'ไม่มี'"
        assert empty.etf_status == "no_holdings"

    def test_snapshot_etf_still_used_when_every_row_is_skipped(self, db, monkeypatch):
        """อ่านสมุดไม่ได้ แต่มีมูลค่า ETF ที่กรอกไว้ → ใช้ของเก่า + เตือน ห้ามทิ้ง."""
        _stub_holdings(monkeypatch, [], **ALL_ROWS_SKIPPED)
        _save_snapshot_row(db, snapshot_date="2026-07-01", assets=[CASH, VOO_SNAPSHOT])

        resp = networth_service.get_current(db)

        assert resp.etf_status == "from_snapshot"
        assert resp.total_assets_thb == pytest.approx(600_000.0)
        assert any("2026-07-01" in w for w in resp.warnings)
        assert resp.skipped_reason, "เหตุผลที่แถวถูกตัดต้องยังอยู่"


# ===========================================================================
# ข้อ 3 — snapshot_date ต้องถูก validate ที่ชั้น schema
# ===========================================================================


class TestSnapshotDateValidation:
    def test_future_date_is_rejected(self):
        future = (date.today() + timedelta(days=1)).isoformat()
        with pytest.raises(ValidationError):
            SnapshotRequest(assets=[Asset(**CASH)], snapshot_date=future)

    def test_far_future_date_is_rejected(self):
        with pytest.raises(ValidationError):
            SnapshotRequest(assets=[Asset(**CASH)], snapshot_date="2099-01-01")

    def test_unparsable_date_is_rejected(self):
        for bad in ("banana", "2026-13-01", "2026-02-30", "07/08/2026"):
            with pytest.raises(ValidationError):
                SnapshotRequest(assets=[Asset(**CASH)], snapshot_date=bad)

    def test_past_and_today_are_accepted(self):
        today = date.today().isoformat()
        assert SnapshotRequest(assets=[Asset(**CASH)], snapshot_date=today).snapshot_date == today
        assert (
            SnapshotRequest(assets=[Asset(**CASH)], snapshot_date="2026-07-01").snapshot_date
            == "2026-07-01"
        )
        assert SnapshotRequest(assets=[Asset(**CASH)]).snapshot_date is None

    @staticmethod
    def _post(monkeypatch, tmp_path, snapshot_date: str):
        """ยิง POST /api/networth/snapshot จริงบนฐานชั่วคราว (ไม่แตะฐานของผู้ใช้)."""
        from fastapi.testclient import TestClient

        from backend.database import get_db
        from backend.main import app

        # ไม่ตั้ง VAULTIS_API_KEY → security.py ยอมให้ TestClient (localhost) เรียกได้
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
        engine = create_engine(
            f"sqlite:///{tmp_path / 'k2_api.db'}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)

        def _override():
            session = Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = _override
        try:
            return TestClient(app).post(
                "/api/networth/snapshot",
                json={"assets": [CASH], "liabilities": [], "snapshot_date": snapshot_date},
            )
        finally:
            app.dependency_overrides.pop(get_db, None)
            engine.dispose()

    def test_api_returns_422_not_201_for_a_future_date(self, monkeypatch, tmp_path):
        resp = self._post(monkeypatch, tmp_path, "2099-01-01")
        assert resp.status_code == 422, resp.text[:300]

    def test_api_returns_422_for_an_unparsable_date(self, monkeypatch, tmp_path):
        resp = self._post(monkeypatch, tmp_path, "banana")
        assert resp.status_code == 422, resp.text[:300]

    def test_api_still_accepts_a_past_date(self, monkeypatch, tmp_path):
        """เคสปกติที่ต้องไม่เปลี่ยน: วันที่ในอดีตยังบันทึกได้ 201 เหมือนเดิม."""
        past = (date.today() - timedelta(days=3)).isoformat()
        resp = self._post(monkeypatch, tmp_path, past)
        assert resp.status_code == 201, resp.text[:300]
        body = resp.json()
        assert body["snapshot_date"] == past
        assert body["snapshot_age_days"] == 3
        assert body["snapshot_stale"] is False
        assert body["snapshot_age_status"] == "fresh"

    def test_saved_snapshot_never_reports_negative_age(self, db, monkeypatch):
        """เคสปกติที่ต้องไม่เปลี่ยน: บันทึกวันนี้ → อายุ 0 วัน ไม่ใช่ค่าติดลบ."""
        monkeypatch.setattr(networth_service, "get_holdings", lambda: {"holdings": []})
        payload = SnapshotRequest(assets=[Asset(**CASH)], liabilities=[Liability(**MORTGAGE)])

        resp = networth_service.save_snapshot(db, payload)

        assert resp.snapshot_age_days == 0
        assert resp.snapshot_stale is False
        assert resp.net_worth_thb == pytest.approx(-1_500_000.0)


# ===========================================================================
# ข้อ 4 — "ไม่รู้อายุ" ต้องไม่ถูกรายงานเป็น "ยังใหม่"
# ===========================================================================


class TestStaleCanSayUnknown:
    def test_unreadable_date_is_unknown_not_fresh(self, db, monkeypatch):
        _stub_holdings(monkeypatch, [])
        _save_snapshot_row(db, snapshot_date="banana", assets=[CASH])

        resp = networth_service.get_current(db)

        assert resp.snapshot_age_days is None
        assert resp.snapshot_stale is None, "'บอกอายุไม่ได้' ห้ามกลายเป็น 'ยังใหม่'"
        assert resp.snapshot_age_status == "unreadable_date"
        assert any("banana" in w for w in resp.warnings)

    def test_future_row_already_in_db_is_unknown(self, db, monkeypatch):
        """แถวเก่าที่ลงวันที่อนาคตไว้ก่อนมี validation → อายุ "ไม่รู้" ไม่ใช่ค่าติดลบ."""
        _stub_holdings(monkeypatch, [])
        _save_snapshot_row(db, snapshot_date="2099-01-01", assets=[CASH])

        resp = networth_service.get_current(db)

        assert resp.snapshot_age_days is None, "อายุติดลบไม่ใช่ข้อมูล — ต้องเป็น 'ไม่รู้'"
        assert resp.snapshot_stale is None
        assert resp.snapshot_age_status == "future_date"
        assert any("2099-01-01" in w for w in resp.warnings)

    def test_no_snapshot_at_all_is_unknown_not_fresh(self, db, monkeypatch):
        _stub_holdings(monkeypatch, [])

        resp = networth_service.get_current(db)

        assert resp.snapshot_stale is None, "ไม่มี snapshot = ตอบไม่ได้ว่าเก่าหรือใหม่"
        assert resp.snapshot_age_status == "no_snapshot"

    def test_fresh_snapshot_is_explicitly_false(self, db, monkeypatch):
        """เคสปกติที่ต้องไม่เปลี่ยน: snapshot วันนี้ = ไม่เก่า (False จริง ๆ ไม่ใช่ None)."""
        _stub_holdings(monkeypatch, [])
        _save_snapshot_row(db, snapshot_date=date.today().isoformat(), assets=[CASH])

        resp = networth_service.get_current(db)

        assert resp.snapshot_stale is False
        assert resp.snapshot_age_status == "fresh"
        assert resp.snapshot_age_days == 0

    def test_old_snapshot_is_still_true(self, db, monkeypatch):
        _stub_holdings(monkeypatch, [])
        old = (date.today() - timedelta(days=200)).isoformat()
        _save_snapshot_row(db, snapshot_date=old, assets=[CASH])

        resp = networth_service.get_current(db)

        assert resp.snapshot_stale is True
        assert resp.snapshot_age_status == "stale"
        assert resp.snapshot_age_days == 200

    def test_history_rows_carry_the_same_age_status(self, db, monkeypatch):
        _stub_holdings(monkeypatch, [])
        recent = (date.today() - timedelta(days=5)).isoformat()
        _save_snapshot_row(db, snapshot_date=recent, assets=[CASH])

        history = networth_service.get_history(db, months=12)

        assert [h.snapshot_date for h in history] == [recent]
        assert history[0].snapshot_age_days == 5
        assert history[0].snapshot_stale is False
        assert history[0].snapshot_age_status == "fresh"
