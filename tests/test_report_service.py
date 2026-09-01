# -*- coding: utf-8 -*-
"""ทดสอบ A2 — ``backend/services/report_service.py`` (AUDIT_2026-08-06)

5 ข้อในไฟล์เดียวกัน:

* **H3** ``get_screener_summary()`` เทียบ ``created_at`` tz-naive ที่ Postgres คืน
  (คอลัมน์เป็น ``TIMESTAMP`` ไร้ tz) กับ cutoff tz-aware → ``TypeError`` ⇒ รายงาน
  รายเดือนตายทั้งงาน **ทันทีที่ screener บันทึกสัญญาณแรก**
* **M-R1** ``except Exception`` คืน "ไม่สามารถสร้าง narrative ได้: …" เป็นตัวรายงาน
  แล้วทับรายงานเดิมของเดือนนั้น + ส่งเข้า Telegram
* **M-R2** มี snapshot เดียว → ยัดค่าปัจจุบันเป็นฐานเทียบของตัวเอง แล้วรายงาน
  "+0 / +0.0%" ทั้งที่ไม่มีจุดเทียบอยู่จริง (โค้ดป้อนเลขที่ไม่มีอยู่จริงให้ AI)
* **M-R3** อ่านประวัติ screener ไม่ได้ (ฐานล่ม) ถูกรายงานว่า "0 รายการ" —
  "ดึงไม่สำเร็จ" ต้องไม่เท่ากับ "ไม่มีข้อมูล"
* **M-R4** ``return_pct`` เป็น ``None`` (``portfolio_service._clean()`` ตั้งใจผลิต)
  ทำให้ f-string ระเบิด **ก่อน** เข้า try/except ⇒ เส้นทางฟรี (cron) ตายด้วย
* **K1** ``current_value_usd``/``pnl_usd`` เป็น ``None`` เมื่อดึงราคาไม่ได้สักกอง
  (``portfolio_service`` ตั้งใจคืนตามสเปก H9) แต่ยังถูก format ด้วย ``:,.2f`` ตรง ๆ
  ⇒ ``TypeError`` ทั้งเส้นทาง AI และเส้นทางฟรี รายงานรายเดือนสร้างไม่ได้เลย

ห้ามแตะฐานจริงของผู้ใช้และห้ามยิง LLM/Telegram จริง — ทุกเคส stub ครบ
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from analysis.llm import LLMDisabledError
from backend.database import Base
from backend.models.orm import MonthlyReport  # noqa: F401  (ต้อง import ให้ตารางถูกสร้าง)
from backend.screener import history_service as hs
from backend.services import report_service as rs

BKK = ZoneInfo("Asia/Bangkok")


# ── helper: อัดข้อมูลปลอมเข้า get_screener_summary ─────────────────────────────

class _FakeHistoryService:
    """แทน ``ScreenerHistoryService`` — คุมได้ทั้งแถวและสถานะของแหล่งข้อมูล."""

    def __init__(self, rows, status="ok", detail=""):
        self._rows = rows
        self._status = status
        self._detail = detail
        self.engine = None if status == "off" else object()

    async def get_history(self, symbol=None, limit=50):
        return self._rows

    async def get_history_with_status(self, symbol=None, limit=50):
        return self._rows, {"status": self._status, "detail": self._detail}


@pytest.fixture()
def fake_history(monkeypatch):
    def _install(rows, status="ok", detail=""):
        monkeypatch.setattr(
            rs, "ScreenerHistoryService", lambda: _FakeHistoryService(rows, status, detail)
        )
    return _install


def _row(created_at, symbol="VOO", preset="oversold_momentum"):
    return {
        "id": "1",
        "symbol": symbol,
        "preset_name": preset,
        "matched_rules": "[]",
        "price": 500.0,
        "signal_strength": 8.0,
        "created_at": created_at,
    }


def _all_data(portfolio=None, networth=None, screener=None, goals=None):
    return {
        "portfolio": portfolio or {
            "holdings_count": 5,
            "current_value_usd": 16028.0,
            "invested_usd": 15000.0,
            "pnl_usd": 1028.0,
            "missing_prices": [],
            "skipped_rows": [],
            "skipped_reason": "",
            "top_holdings": [{"ticker": "VOO", "return_pct": 5.0}],
        },
        "networth": networth or {"available": False},
        "screener": screener or {
            "available": True,
            "unavailable_reason": "",
            "total_signals": 0,
            "symbols_with_signals": [],
            "by_preset": {},
        },
        "goals": goals or {"total": 0, "on_track": [], "off_track": []},
    }


# ── H3 ────────────────────────────────────────────────────────────────────────

class TestScreenerSummaryTimezone:
    """H3 — ``created_at`` ไร้ timezone ต้องไม่ทำให้รายงานรายเดือนตาย."""

    async def test_naive_created_at_does_not_explode(self, fake_history):
        # เวลาที่ Postgres (TZ=Asia/Bangkok, คอลัมน์ TIMESTAMP) คืนกลับมาจริง
        naive_now = datetime.now(BKK).replace(tzinfo=None)
        fake_history([_row(naive_now)])

        out = await rs.get_screener_summary()

        assert out["total_signals"] == 1, "แถวที่เพิ่งบันทึกต้องถูกนับ ไม่ใช่ระเบิดหรือหายไป"
        assert out["symbols_with_signals"] == ["VOO"]

    async def test_naive_old_row_is_outside_window(self, fake_history):
        old = (datetime.now(BKK) - timedelta(days=60)).replace(tzinfo=None)
        fake_history([_row(old)])

        out = await rs.get_screener_summary()

        assert out["total_signals"] == 0, "แถวอายุ 60 วันต้องอยู่นอกหน้าต่าง 30 วัน"

    async def test_aware_and_iso_string_still_work(self, fake_history):
        aware = datetime.now(UTC) - timedelta(days=1)
        iso_naive = (datetime.now(BKK) - timedelta(days=2)).replace(tzinfo=None).isoformat()
        fake_history([_row(aware, symbol="SCHD"), _row(iso_naive, symbol="QQQM")])

        out = await rs.get_screener_summary()

        assert out["total_signals"] == 2
        assert sorted(out["symbols_with_signals"]) == ["QQQM", "SCHD"]

    def test_ddl_stores_timezone(self):
        """ต้นตอของ H3 อยู่ที่ DDL — คอลัมน์ต้องเก็บ timezone ไปเลย."""
        ddl = hs.SCREENER_HISTORY_DDL.upper()
        assert "TIMESTAMPTZ" in ddl or "TIMESTAMP WITH TIME ZONE" in ddl, (
            "created_at เป็น TIMESTAMP ไร้ tz → เทียบกับ cutoff แบบ aware ไม่ได้"
        )


class TestLegacyTableMigration:
    """H3 (ต่อ) — ตารางที่สร้างไว้แล้วต้องถูก migrate ไม่ใช่ปล่อยไว้ไร้ tz.

    ``CREATE TABLE IF NOT EXISTS`` ไม่แตะตารางที่มีอยู่ — ฐานของผู้ใช้ที่สร้างก่อน
    การแก้นี้จึงยังเก็บ ``TIMESTAMP`` ไร้ tz ต่อไป ต้องมี ``ALTER … USING created_at
    AT TIME ZONE 'Asia/Bangkok'`` (เวลาที่ค่าเก่าถูกเขียนไว้จริง เพราะคอนเทนเนอร์
    postgres ตั้ง ``TZ: Asia/Bangkok``) ไม่งั้นค่าเดิมจะถูกตีเป็น UTC = เลื่อน 7 ชม.
    """

    class _FakeConn:
        def __init__(self, created_at_type: str):
            self.executed: list[str] = []
            self._type = created_at_type

        def execute(self, stmt, params=None):
            self.executed.append(str(stmt))
            return SimpleNamespace(scalar=lambda: self._type)

    class _FakeEngine:
        def __init__(self, conn):
            self.conn = conn
            self.disposed = False

        def begin(self):
            @contextmanager
            def _cm():
                yield self.conn
            return _cm()

        def dispose(self):
            self.disposed = True

    def _run(self, monkeypatch, created_at_type: str):
        conn = self._FakeConn(created_at_type)
        engine = self._FakeEngine(conn)
        monkeypatch.setattr(hs, "DATABASE_URL", "postgresql+psycopg2://probe/probe")
        monkeypatch.setattr(hs, "create_engine", lambda url, **kw: engine)
        hs.create_screener_history_table()
        return engine, conn

    def test_naive_column_is_converted_as_bangkok_time(self, monkeypatch):
        _, conn = self._run(monkeypatch, "timestamp without time zone")

        alters = [s for s in conn.executed if "ALTER TABLE" in s.upper()]
        assert alters, "ตารางเดิมที่คอลัมน์ไร้ tz ต้องถูก migrate ไม่ใช่ปล่อยผ่าน"
        sql = alters[0].upper()
        assert "TIMESTAMPTZ" in sql or "TIMESTAMP WITH TIME ZONE" in sql
        assert "AT TIME ZONE 'ASIA/BANGKOK'" in sql, (
            "ค่าเก่าถูกเขียนเป็นเวลาไทย — ตีเป็น UTC จะเลื่อนไป 7 ชม."
        )

    def test_already_timestamptz_is_left_alone(self, monkeypatch):
        engine, conn = self._run(monkeypatch, "timestamp with time zone")

        assert not [s for s in conn.executed if "ALTER TABLE" in s.upper()], (
            "คอลัมน์ที่ถูกต้องอยู่แล้วต้องไม่ถูก ALTER ซ้ำทุกครั้งที่เรียก"
        )
        assert engine.disposed, "ต้องคืน connection pool ทุกเส้นทาง"


# ── M-R3 ──────────────────────────────────────────────────────────────────────

class TestScreenerSummaryAvailability:
    """M-R3 — "อ่านประวัติไม่ได้" ต้องไม่ถูกรายงานว่า "0 รายการ"."""

    async def test_db_error_marks_unavailable(self, fake_history):
        fake_history([], status="error", detail="(psycopg2.OperationalError) Connection refused")

        out = await rs.get_screener_summary()

        assert out["available"] is False, "ฐานล่มต้องไม่ถูกนับว่าไม่มีสัญญาณ"
        assert "Connection refused" in out["unavailable_reason"]
        assert out["total_signals"] is None, "ตัวเลขที่ไม่รู้ ห้ามกลายเป็น 0"

    async def test_database_url_unset_is_off_not_error(self, fake_history):
        fake_history([], status="off", detail="")

        out = await rs.get_screener_summary()

        assert out["available"] is False
        assert "DATABASE_URL" in out["unavailable_reason"], "ต้องแยก 'ไม่ได้เปิดใช้' ออกจาก 'สแกนไม่ได้'"

    async def test_empty_table_is_a_real_zero(self, fake_history):
        fake_history([], status="ok")

        out = await rs.get_screener_summary()

        assert out["available"] is True
        assert out["total_signals"] == 0

    def test_plain_narrative_says_unreadable(self):
        all_data = _all_data(screener={
            "available": False,
            "unavailable_reason": "(psycopg2.OperationalError) Connection refused",
            "total_signals": None,
            "symbols_with_signals": [],
            "by_preset": {},
        })

        narrative = rs._plain_narrative(all_data, "2026-08")

        assert "0 รายการ" not in narrative, "ฐานล่มแล้วบอกว่า 0 รายการ = รายงานเท็จ"
        assert "อ่านประวัติ" in narrative

    def test_llm_prompt_never_claims_zero(self, monkeypatch):
        captured = {}

        def _capture(system, user, **kwargs):
            captured["user"] = user
            return "สรุปจาก AI"

        monkeypatch.setattr(rs, "chat_text", _capture)
        all_data = _all_data(screener={
            "available": False,
            "unavailable_reason": "Connection refused",
            "total_signals": None,
            "symbols_with_signals": [],
            "by_preset": {},
        })

        rs.generate_narrative(all_data, "2026-08", user_initiated=True)

        assert "สัญญาณทั้งหมด: 0" not in captured["user"], "ห้ามป้อนเลขปลอมให้ AI"
        assert "อ่านประวัติ" in captured["user"]


# ── M-R2 ──────────────────────────────────────────────────────────────────────

class TestNetWorthBaseline:
    """M-R2 — ไม่มีจุดเทียบ ต้องไม่ถูกรายงานเป็น "+0 / +0.0%"."""

    @staticmethod
    def _snap(month_day: str, value: float):
        return SimpleNamespace(snapshot_date=month_day, net_worth_thb=value)

    def test_single_snapshot_has_no_baseline(self, monkeypatch):
        monkeypatch.setattr(
            rs.networth_service, "get_history",
            lambda db, months=12: [self._snap("2026-08-01", 1234567.0)],
        )

        out = rs.get_networth_change(None)

        assert out["has_baseline"] is False
        assert out["change_thb"] is None, "ไม่มีจุดเทียบ → ผลต่างต้องเป็น None ไม่ใช่ 0"
        assert out["change_pct"] is None
        assert out["current_net_worth_thb"] == 1234567.0

    def test_real_baseline_still_computes(self, monkeypatch):
        monkeypatch.setattr(
            rs.networth_service, "get_history",
            lambda db, months=12: [
                self._snap(datetime.now(BKK).date().isoformat(), 1234567.0),
                self._snap("2026-07-01", 1000000.0),
            ],
        )

        out = rs.get_networth_change(None)

        assert out["has_baseline"] is True
        assert out["change_thb"] == pytest.approx(234567.0)
        assert out["change_pct"] == pytest.approx(23.46, abs=0.01)

    def test_narrative_says_no_baseline(self):
        all_data = _all_data(networth={
            "available": True,
            "has_baseline": False,
            "current_net_worth_thb": 1234567.0,
            "previous_net_worth_thb": None,
            "change_thb": None,
            "change_pct": None,
        })

        narrative = rs._plain_narrative(all_data, "2026-08")

        assert "+0" not in narrative, "ตัวเลขที่ไม่มีอยู่จริงห้ามโผล่ในรายงาน"
        assert "1,234,567" in narrative
        assert "เทียบ" in narrative


# ── M-R4 ──────────────────────────────────────────────────────────────────────

class TestNoneReturnPct:
    """M-R4 — ``return_pct=None`` ต้องไม่ทำให้เส้นทางฟรีระเบิด."""

    def test_none_return_pct_survives_free_path(self, monkeypatch):
        def _disabled(*a, **k):
            raise LLMDisabledError("ปิดอยู่")

        monkeypatch.setattr(rs, "chat_text", _disabled)
        pf = _all_data()["portfolio"] | {"top_holdings": [{"ticker": "SCHD", "return_pct": None}]}

        narrative = rs.generate_narrative(_all_data(portfolio=pf), "2026-08")

        assert "16,028" in narrative, "รายงานต้องมีตัวเลขจริงเหมือนเดิม"

    def test_none_return_pct_in_prompt(self, monkeypatch):
        captured = {}

        def _capture(system, user, **kwargs):
            captured["user"] = user
            return "ok"

        monkeypatch.setattr(rs, "chat_text", _capture)
        pf = _all_data()["portfolio"] | {"top_holdings": [{"ticker": "SCHD", "return_pct": None}]}

        rs.generate_narrative(_all_data(portfolio=pf), "2026-08", user_initiated=True)

        assert "SCHD" in captured["user"]
        assert "0.0%" not in captured["user"], "ไม่รู้ผลตอบแทน ห้ามเขียนเป็น 0.0%"


# ── M-R1 ──────────────────────────────────────────────────────────────────────

class TestNarrativeFailureFallsBackToNumbers:
    """M-R1 — LLM ล้มเหลว (ไม่ใช่ปิดอยู่) ต้องได้ตัวเลขเหมือนเดิม + คำเตือน."""

    def test_runtime_error_keeps_numbers(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("เรียก LLM ไม่สำเร็จ: anthropic: overloaded_error")

        monkeypatch.setattr(rs, "chat_text", _boom)

        narrative = rs.generate_narrative(_all_data(), "2026-08", user_initiated=True)

        assert "16,028" in narrative, "รายงานต้องไม่กลายเป็นสตริง error เปล่า ๆ"
        assert "overloaded_error" in narrative, "ต้องบอกสาเหตุที่ AI เขียนไม่สำเร็จด้วย"
        assert not narrative.startswith("ไม่สามารถสร้าง narrative ได้")

    def test_source_is_reported(self, monkeypatch):
        monkeypatch.setattr(rs, "chat_text", lambda *a, **k: "บทสรุปจาก AI")
        _, source = rs.generate_narrative_with_source(_all_data(), "2026-08", user_initiated=True)
        assert source == "ai"

        def _boom(*a, **k):
            raise RuntimeError("529 overloaded")

        monkeypatch.setattr(rs, "chat_text", _boom)
        _, source = rs.generate_narrative_with_source(_all_data(), "2026-08", user_initiated=True)
        assert source == "plain"


# ── M-R1 (ฝั่งบันทึก) + "cron ตายต้องไม่เงียบ" ────────────────────────────────

@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """SQLite ชั่วคราว — ห้ามแตะ ``.docker-data/vaultis.db`` ของผู้ใช้."""
    engine = create_engine(f"sqlite:///{tmp_path/'reports.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine, tables=[MonthlyReport.__table__])
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(rs, "SessionLocal", Session)
    return engine, Session


@pytest.fixture()
def no_telegram(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def _fake(content, month):
        sent.append((content, month))

    monkeypatch.setattr(rs, "_send_telegram", _fake)
    return sent


class TestGenerateAndSaveReport:
    def _stub_aggregate(self, monkeypatch, all_data=None):
        async def _agg(db):
            return all_data or _all_data()

        monkeypatch.setattr(rs, "_aggregate_data", _agg)

    async def test_plain_never_overwrites_ai(self, temp_db, no_telegram, monkeypatch):
        self._stub_aggregate(monkeypatch)
        _, Session = temp_db

        monkeypatch.setattr(rs, "chat_text", lambda *a, **k: "บทสรุปจาก AI ที่ผู้ใช้จ่ายเงินไปแล้ว")
        first = await rs.generate_and_save_report(user_initiated=True)
        assert first["source"] == "ai"

        def _boom(*a, **k):
            raise RuntimeError("529 overloaded")

        monkeypatch.setattr(rs, "chat_text", _boom)
        second = await rs.generate_and_save_report(user_initiated=True)
        assert second["source"] == "plain"
        assert second["saved"] is False, "plain ห้ามทับ ai ของเดือนเดียวกัน"

        db = Session()
        try:
            rows = db.query(MonthlyReport).all()
            assert len(rows) == 1
            assert rows[0].content == "บทสรุปจาก AI ที่ผู้ใช้จ่ายเงินไปแล้ว"
            assert rows[0].source == "ai"
        finally:
            db.close()

    async def test_ai_may_overwrite_plain(self, temp_db, no_telegram, monkeypatch):
        self._stub_aggregate(monkeypatch)
        _, Session = temp_db

        def _disabled(*a, **k):
            raise LLMDisabledError("ปิดอยู่")

        monkeypatch.setattr(rs, "chat_text", _disabled)
        await rs.generate_and_save_report(user_initiated=False)

        monkeypatch.setattr(rs, "chat_text", lambda *a, **k: "บทสรุปจาก AI")
        out = await rs.generate_and_save_report(user_initiated=True)

        assert out["saved"] is True
        db = Session()
        try:
            row = db.query(MonthlyReport).one()
            assert row.source == "ai"
            assert row.content == "บทสรุปจาก AI"
        finally:
            db.close()

    async def test_legacy_table_without_source_column_is_migrated(
        self, tmp_path, no_telegram, monkeypatch
    ):
        """ฐานเดิมของผู้ใช้ไม่มีคอลัมน์ ``source`` — ต้องเติมให้ ไม่ใช่ระเบิด."""
        engine = create_engine(f"sqlite:///{tmp_path/'legacy.db'}", connect_args={"check_same_thread": False})
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE monthly_reports ("
                " id INTEGER PRIMARY KEY, month VARCHAR NOT NULL UNIQUE,"
                " content TEXT NOT NULL, sent_at DATETIME NOT NULL)"
            ))
            conn.execute(text(
                "INSERT INTO monthly_reports (month, content, sent_at)"
                " VALUES ('2026-07', 'รายงานเก่า', '2026-07-01 08:00:00')"
            ))
        Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        monkeypatch.setattr(rs, "SessionLocal", Session)
        self._stub_aggregate(monkeypatch)
        monkeypatch.setattr(rs, "chat_text", lambda *a, **k: "บทสรุปจาก AI")

        out = await rs.generate_and_save_report(user_initiated=True)

        assert out["saved"] is True
        db = Session()
        try:
            old = db.query(MonthlyReport).filter(MonthlyReport.month == "2026-07").one()
            assert old.content == "รายงานเก่า"
            assert old.source == "plain", "แถวเก่าที่ไม่รู้ที่มา ต้องไม่ถูกอ้างว่าเป็น ai"
        finally:
            db.close()

    async def test_failure_is_reported_not_silent(self, temp_db, no_telegram, monkeypatch):
        """cron ตายต้องมีคนรู้ — log + แจ้งเตือน แล้วค่อยโยนต่อให้ผู้เรียก."""
        async def _agg(db):
            raise RuntimeError("ฐานข้อมูลล่ม")

        monkeypatch.setattr(rs, "_aggregate_data", _agg)

        with pytest.raises(RuntimeError, match="ฐานข้อมูลล่ม"):
            await rs.generate_and_save_report(user_initiated=False)

        assert no_telegram, "รายงานรายเดือนพังต้องแจ้งเตือนออกไป ไม่ใช่ตายเงียบ"
        assert "ฐานข้อมูลล่ม" in no_telegram[0][0]


# ── K1 ────────────────────────────────────────────────────────────────────────

# ราคาหายทั้งพอร์ต: ``portfolio_service.get_portfolio_summary()`` **ตั้งใจ** คืน
# ``None`` ให้มูลค่า/กำไร (H9 — "ไม่รู้" ห้ามกลายเป็น 0) ส่วน ``invested_usd``
# ยังรู้อยู่เสมอ เพราะเป็นเงินที่จ่ายไปจริง ไม่ต้องพึ่งราคาปัจจุบัน
# จำนวนเงินเลือกให้ไม่มี ".00" ท้ายจงใจ — assertion "0.00 USD" จะได้ไม่ชนตัวเอง
_PRICELESS_PF = {
    "holdings_count": 5,
    "current_value_usd": None,
    "invested_usd": 15432.10,
    "pnl_usd": None,
    "missing_prices": ["VOO", "SCHD", "QQQM", "XLV", "GLDM"],
    "skipped_rows": [],
    "skipped_reason": "",
    "top_holdings": [],
}


class TestMissingPricesDoNotKillReport:
    """K1 — ดึงราคาไม่ได้ทั้งพอร์ต ต้องได้รายงานที่ "บอกว่าดึงราคาไม่ได้".

    ไม่ใช่ ``TypeError`` (รายงานหายทั้งเดือน) และไม่ใช่ "0.00 USD" (รายงานเท็จ)
    """

    def test_plain_narrative_survives_and_says_why(self):
        narrative = rs._plain_narrative(_all_data(portfolio=_PRICELESS_PF), "2026-08")

        assert "ดึงราคาไม่ได้" in narrative, "ต้องบอกสาเหตุ ไม่ใช่เว้นว่าง"
        assert "0.00 USD" not in narrative, "ไม่รู้มูลค่า ห้ามพิมพ์เป็น 0"
        assert "+0" not in narrative
        assert "VOO" in narrative and "GLDM" in narrative, "ต้องบอกว่ากองไหนดึงไม่ได้"
        assert "15,432.10" in narrative, "เงินที่ลงทุนไปแล้วยังรู้อยู่ ควรบอกผู้ใช้"

    def test_free_path_survives(self, monkeypatch):
        """cron วันที่ 1 (ไม่ใช้ AI) — เดิม ``_plain_narrative`` ระเบิดซ้ำใน except."""
        def _disabled(*a, **k):
            raise LLMDisabledError("ปิดอยู่")

        monkeypatch.setattr(rs, "chat_text", _disabled)

        narrative, source = rs.generate_narrative_with_source(
            _all_data(portfolio=_PRICELESS_PF), "2026-08"
        )

        assert source == "plain"
        assert "ดึงราคาไม่ได้" in narrative

    def test_prompt_never_claims_zero(self, monkeypatch):
        captured = {}

        def _capture(system, user, **kwargs):
            captured["user"] = user
            return "สรุปจาก AI"

        monkeypatch.setattr(rs, "chat_text", _capture)

        _, source = rs.generate_narrative_with_source(
            _all_data(portfolio=_PRICELESS_PF), "2026-08", user_initiated=True
        )

        assert source == "ai", "พรอมป์ต้องประกอบได้ ไม่ใช่ร่วงไป plain เพราะ TypeError"
        assert "0.00 USD" not in captured["user"], "ห้ามป้อนเลข 0 ปลอมให้ AI"
        assert "ดึงราคาไม่ได้" in captured["user"]
        assert "ห้าม" in captured["user"], "ต้องสั่ง AI ไม่ให้เดาตัวเลขที่ยังไม่รู้"

    def test_partial_prices_keep_numbers_and_warn(self):
        """ราคาหายบางตัว — ยอดที่คำนวณได้ยังต้องแสดง พร้อมคำเตือนว่าไม่ครบ."""
        pf = _all_data()["portfolio"] | {"missing_prices": ["GLDM"]}

        narrative = rs._plain_narrative(_all_data(portfolio=pf), "2026-08")

        assert "16,028.00 USD" in narrative
        assert "+1,028.00 USD" in narrative
        assert "GLDM" in narrative and "ดึงราคาไม่ได้" in narrative

    def test_full_prices_unchanged(self):
        narrative = rs._plain_narrative(_all_data(), "2026-08")

        assert "มูลค่า 16,028.00 USD" in narrative
        assert "กำไร/ขาดทุน +1,028.00 USD" in narrative
        assert "ดึงราคาไม่ได้" not in narrative

    def test_full_prices_prompt_unchanged(self, monkeypatch):
        captured = {}

        def _capture(system, user, **kwargs):
            captured["user"] = user
            return "ok"

        monkeypatch.setattr(rs, "chat_text", _capture)

        rs.generate_narrative(_all_data(), "2026-08", user_initiated=True)

        assert "- มูลค่ารวม: 16,028.00 USD" in captured["user"]
        assert "- กำไร/ขาดทุน: +1,028.00 USD" in captured["user"]

    def test_empty_ledger_still_reports_real_zero(self):
        """สมุดว่าง = 0 จริง ๆ (คนละเรื่องกับดึงไม่ได้) ต้องพิมพ์ 0 ตามเดิม."""
        pf = _all_data()["portfolio"] | {
            "holdings_count": 0,
            "current_value_usd": 0.0,
            "invested_usd": 0.0,
            "pnl_usd": 0.0,
            "top_holdings": [],
        }

        narrative = rs._plain_narrative(_all_data(portfolio=pf), "2026-08")

        assert "มูลค่า 0.00 USD" in narrative
        assert "ดึงราคาไม่ได้" not in narrative, "สมุดว่างไม่ใช่ความล้มเหลวในการดึงราคา"

    def test_networth_value_unreadable_is_not_formatted(self):
        """``available=True`` แต่ค่าเป็น ``None`` ต้องไม่ระเบิดและไม่กลายเป็น 0."""
        txt = rs._networth_txt(
            {
                "available": True,
                "has_baseline": True,
                "current_net_worth_thb": None,
                "previous_net_worth_thb": None,
                "change_thb": None,
                "change_pct": None,
            },
            prefix="Net Worth:",
            unit="บาท",
        )

        assert "0" not in txt, "ไม่รู้ Net Worth ห้ามพิมพ์เป็น 0"
        assert "ไม่ได้" in txt

    def test_networth_change_unreadable_keeps_current(self):
        txt = rs._networth_txt(
            {
                "available": True,
                "has_baseline": True,
                "current_net_worth_thb": 1234567.0,
                "previous_net_worth_thb": None,
                "change_thb": None,
                "change_pct": None,
            },
            prefix="Net Worth:",
            unit="บาท",
        )

        assert "1,234,567" in txt, "ค่าที่รู้ต้องยังแสดง"
        assert "+0" not in txt

    async def test_report_is_generated_and_saved(self, temp_db, no_telegram, monkeypatch):
        """end-to-end: ราคาหายทั้งพอร์ตต้องยังได้รายงานลงฐาน + ส่งออกจริง."""
        async def _agg(db):
            return _all_data(portfolio=_PRICELESS_PF)

        def _disabled(*a, **k):
            raise LLMDisabledError("ปิดอยู่")

        monkeypatch.setattr(rs, "_aggregate_data", _agg)
        monkeypatch.setattr(rs, "chat_text", _disabled)

        out = await rs.generate_and_save_report(user_initiated=False)

        assert out["saved"] is True
        assert out["source"] == "plain"
        assert "ดึงราคาไม่ได้" in out["content"]
        assert no_telegram and "ไม่สำเร็จ" not in no_telegram[0][0], (
            "ต้องส่งรายงานจริง ไม่ใช่ข้อความแจ้งว่างานพัง"
        )

        _, Session = temp_db
        db = Session()
        try:
            assert db.query(MonthlyReport).one().source == "plain"
        finally:
            db.close()
