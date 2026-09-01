# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 — ตรึง "หน้าต่างเวลา" และตาข่ายกันล้มที่ยังไม่มีเทสต์แตะเลย.

รอบ T1 ของออดิตพิสูจน์ด้วย mutation testing ว่าโค้ดสี่จุดนี้ *ทำถูกอยู่แล้ว* แต่
**ไม่มีอะไรตรึงไว้** — แก้ตัวเลข/ย้ายบรรทัดแล้วชุดเทสต์ทั้ง 1,297 ตัวยังเขียวหมด
ทั้งที่สิ่งที่ผู้ใช้เห็นเปลี่ยนไปคนละเรื่อง ไฟล์นี้จึงเป็น "ตัวพิสูจน์เจตนา" ของสี่จุดนั้น:

* **K8 · หน้าต่าง 12 เดือนของ ``still_growing``** (``backend/services/debt_service.py``)
  แยก "ดอกเบี้ยเดินเร็วกว่าเงินต้น → รีไฟแนนซ์" ออกจาก "งบพอจ่ายดอกไหว แต่เงินต้น
  ลดช้า → เพิ่มงบ" ตัดหน้าต่างทิ้งแล้วคำแนะนำการเงินพลิกเป็นคนละเรื่อง (ออดิตบรรทัด 578)
* **K1 · หน้าต่าง 3 เดือนของ ``get_networth_change``** (``backend/services/report_service.py``)
  เทสต์เดิมสอง ตัว monkeypatch ``get_history`` ทิ้ง ⇒ ค่าตัดของจริงใน
  ``networth_service.get_history``/``_months_back`` ไม่เคยถูกเรียกเลย (ออดิตบรรทัด 1181)
* **K2 · เส้นแบ่ง ``STALE_SNAPSHOT_DAYS``** (``backend/services/networth_service.py``)
  เทสต์เดิมแตะแค่ 0/5 วัน กับ 200/2000+ วัน ⇒ ขอบจริงที่ 90 วันไม่มีใครแตะ (ออดิตบรรทัด 1207)
* **K1 · ``_build_prompt`` อยู่ใน ``try`` เดียวกับ LLM โดยตั้งใจ**
  (``backend/services/report_service.py``) ย้ายออกนอก ``try`` แล้วเทสต์เขียวหมด
  ทั้งที่รายงานรายเดือนจะตายทั้งฉบับแทนที่จะถอยไปเส้นทางฟรี (ออดิตบรรทัด 1232)

หมายเหตุขอบเขต: ค่าคงที่ในโค้ด (``12`` ของ debt_service, ``months=3`` ของ report_service)
ยังฝังเป็นตัวเลขในบรรทัด — ทั้งสองไฟล์อยู่นอกความรับผิดชอบของรอบแก้นี้ จึงตรึงด้วย
"พฤติกรรมที่ผู้ใช้เห็น" แทนการตั้งชื่อค่าคงที่ (เทสต์ที่นี่แดงทันทีถ้าใครขยับตัวเลข)

ห้ามแตะฐานจริงของผู้ใช้และห้ามยิง LLM จริง — ทุกเคสใช้ SQLite ใน ``tmp_path`` และ stub
``chat_text`` ไว้เสมอ
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models.debt_models import Debt
from backend.models.orm import NetWorthSnapshot
from backend.services import debt_service, networth_service, report_service


# ── ฐานข้อมูลชั่วคราว ────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    """SQLite ชั่วคราวใน tmp_path — ห้ามแตะ vaultis.db จริงของผู้ใช้ (AUDIT ข้อ 0.1)."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'networth_windows.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _save_snapshot(db, snapshot_date: str, net_worth: float) -> None:
    """เขียน snapshot แถวจริงลงตาราง — เทสต์ชุดนี้ **ห้าม** stub ``get_history``.

    จุดตายของ K1 คือค่าตัดใน ``networth_service.get_history`` ซึ่งเทสต์เดิม patch ทิ้ง
    ทั้งตัว การเขียนแถวจริงคือวิธีเดียวที่ทำให้ ``_months_back`` ถูกเรียกจริง
    """
    assets = [{"name": "เงินสด", "type": "cash", "value_thb": net_worth}]
    db.add(
        NetWorthSnapshot(
            snapshot_date=snapshot_date,
            total_assets_thb=net_worth,
            total_liabilities_thb=0.0,
            net_worth_thb=net_worth,
            assets_json=json.dumps(assets, ensure_ascii=False),
            liabilities_json=json.dumps([], ensure_ascii=False),
        )
    )
    db.commit()


class _FixedDate(date):
    """วันนี้แบบตรึงไว้ — ผลของเทสต์ต้องไม่ขึ้นกับว่ารันวันไหนของเดือน.

    ถ้าปล่อยให้ใช้วันจริง เคส "หน้าต่าง 3 เดือน" จะไม่กัดเลยในวันที่ 1 ของเดือน
    (เพราะ ``months=1`` ก็ยังครอบ snapshot วันที่ 1 ของเดือนก่อนพอดี)
    """

    @classmethod
    def today(cls) -> "_FixedDate":  # type: ignore[override]
        return cls(2026, 8, 20)


@pytest.fixture()
def frozen_today(monkeypatch):
    """ตรึง ``date.today()`` ของทั้งสองโมดูลที่ร่วมกันตัดหน้าต่างเวลา."""
    monkeypatch.setattr(networth_service, "date", _FixedDate)
    monkeypatch.setattr(report_service, "date", _FixedDate)
    return _FixedDate.today()


# ── K2: เส้นแบ่ง fresh/stale ของ snapshot (ออดิตบรรทัด 1207) ────────────────────

class TestStaleSnapshotBoundary:
    """ขอบจริงของ ``STALE_SNAPSHOT_DAYS`` — อ่านค่าคงที่จากโมดูล ไม่ฮาร์ดโค้ด 90.

    เทสต์เดิมใช้แค่ 0/5 วัน (fresh) กับ 200/2000+ วัน (stale) ⇒ ขยายเส้นแบ่งเป็นสองเท่า
    แล้วทั้งชุดยังเขียว snapshot อายุ 91–180 วันจะถูกรายงานว่า "ยังใหม่" โดยไม่มีธงเตือน
    ผู้ใช้จึงเห็นเงินสด/หนี้สินเก่าครึ่งปีเป็นตัวเลขปัจจุบัน
    """

    def test_exactly_on_the_line_is_still_fresh(self):
        today = date(2026, 8, 20)
        limit = networth_service.STALE_SNAPSHOT_DAYS
        age = networth_service._snapshot_age(
            (today - timedelta(days=limit)).isoformat(), today
        )
        assert age.days == limit
        assert age.stale is False, "อายุเท่ากับเส้นแบ่งพอดี ยังไม่ถือว่าเก่า"
        assert age.status == "fresh"

    def test_one_day_past_the_line_is_stale(self):
        today = date(2026, 8, 20)
        limit = networth_service.STALE_SNAPSHOT_DAYS
        age = networth_service._snapshot_age(
            (today - timedelta(days=limit + 1)).isoformat(), today
        )
        assert age.days == limit + 1
        assert age.stale is True, "เกินเส้นแบ่งไป 1 วัน ต้องถูกตีเป็นเก่าทันที"
        assert age.status == "stale"

    def test_the_line_is_one_quarter_by_policy(self):
        """ค่าคงที่คือ "นโยบาย" ไม่ใช่รายละเอียดภายใน — เปลี่ยนเมื่อไหร่ต้องแก้เทสต์นี้ด้วย.

        90 วัน ≈ หนึ่งไตรมาส คือช่วงที่เงินสด/หนี้สินที่ผู้ใช้กรอกเองยังพอเชื่อได้
        การขยายเงียบ ๆ (เช่นเป็น 180) = ยอมให้ข้อมูลครึ่งปีถูกเสนอเป็นของปัจจุบัน
        """
        assert networth_service.STALE_SNAPSHOT_DAYS == 90

    def test_flag_reaches_the_history_response(self, db):
        """ธงต้องไปถึงคำตอบจริง ไม่ใช่ถูกคำนวณทิ้งไว้ในฟังก์ชันภายใน."""
        today = date.today()
        limit = networth_service.STALE_SNAPSHOT_DAYS
        _save_snapshot(db, (today - timedelta(days=limit)).isoformat(), 1_000_000.0)
        _save_snapshot(db, (today - timedelta(days=limit + 1)).isoformat(), 900_000.0)

        history = networth_service.get_history(db, months=12)
        by_date = {row.snapshot_date: row for row in history}

        on_line = by_date[(today - timedelta(days=limit)).isoformat()]
        past_line = by_date[(today - timedelta(days=limit + 1)).isoformat()]
        assert on_line.snapshot_stale is False
        assert past_line.snapshot_stale is True


# ── K1: หน้าต่างมองย้อน 3 เดือนของรายงานรายเดือน (ออดิตบรรทัด 1181) ──────────────

class TestNetWorthBaselineLookbackWindow:
    """``get_networth_change`` ต้องหาจุดเทียบ "เดือนก่อน" เจอจริงจากฐานจริง.

    เทสต์เดิมของ K1/M-R2 patch ``get_history`` เป็น lambda ที่คืนลิสต์ตายตัว ⇒ ค่า
    ``months=3`` และ ``_months_back`` ไม่เคยถูกเรียก ลดเหลือ ``months=1`` แล้วเขียวหมด
    ทั้งที่ผู้ใช้ที่มี snapshot เดือนก่อนอยู่จริงจะถูกบอกว่า "ยังไม่มีเดือนก่อนหน้าให้เทียบ"
    — คือการซ่อนข้อมูลเงียบ ๆ ในทิศเดียวกับรูที่ K1/M-R2 เพิ่งปิดไป
    """

    def test_previous_month_snapshot_is_found(self, db, frozen_today):
        # snapshot รายเดือนของผู้ใช้ลงวันที่ 1 ของเดือน — ห่างจากวันนี้ (20 ส.ค.) เกิน
        # หนึ่งเดือนปฏิทินแบบวันชนวัน หน้าต่างต้องกว้างพอที่จะยังเห็นมัน
        _save_snapshot(db, "2026-07-01", 1_000_000.0)
        _save_snapshot(db, frozen_today.isoformat(), 1_200_000.0)

        out = report_service.get_networth_change(db)

        assert out["available"] is True
        assert out["has_baseline"] is True, "มี snapshot เดือนก่อนอยู่จริง ห้ามบอกว่าไม่มีจุดเทียบ"
        assert out["previous_net_worth_thb"] == pytest.approx(1_000_000.0)
        assert out["change_thb"] == pytest.approx(200_000.0)
        assert out["change_pct"] == pytest.approx(20.0)

    def test_cutoff_counts_calendar_months_not_thirty_day_blocks(self, db, frozen_today):
        """3 เดือนปฏิทิน ≠ 90 วัน — ``_months_back`` มีอยู่เพราะเรื่องนี้ (L-NW-1).

        snapshot วันที่ 20 พ.ค. อยู่บนขอบพอดีของหน้าต่าง 3 เดือนปฏิทิน แต่หลุดออกไป
        ทันทีถ้าใครเปลี่ยนไปคิดเป็น ``3 × 30`` วัน
        """
        _save_snapshot(db, "2026-05-20", 800_000.0)
        _save_snapshot(db, frozen_today.isoformat(), 1_200_000.0)

        out = report_service.get_networth_change(db)

        assert out["has_baseline"] is True
        assert out["previous_net_worth_thb"] == pytest.approx(800_000.0)

    def test_snapshot_older_than_the_window_is_not_a_baseline(self, db, frozen_today):
        """ของเก่าเกินหน้าต่างห้ามถูกเสนอว่าเป็น "เดือนก่อน".

        หน้าต่างกว้างเกินไปก็โกหกอีกแบบ: ผลต่างจาก snapshot อายุ 4 เดือนกว่าถูกเขียน
        ในรายงานว่าเป็นการเปลี่ยนแปลง "จากเดือนก่อน" ⇒ ต้องเป็น ``has_baseline=False``
        (ตัวเลขที่ไม่มีอยู่จริงห้ามโผล่ — หลักเดียวกับ M-R2)
        """
        _save_snapshot(db, "2026-04-01", 500_000.0)
        _save_snapshot(db, frozen_today.isoformat(), 1_200_000.0)

        out = report_service.get_networth_change(db)

        assert out["available"] is True
        assert out["has_baseline"] is False
        assert out["change_thb"] is None
        assert out["change_pct"] is None
        assert out["previous_net_worth_thb"] is None


# ── K1: พรอมป์อยู่ใน try เดียวกับ LLM โดยตั้งใจ (ออดิตบรรทัด 1232) ──────────────

def _all_data(goals: dict | None = None) -> dict:
    """ข้อมูลรูปเดียวกับที่ ``_aggregate_data()`` คืน — ครบทุกคีย์ที่สองเส้นทางใช้."""
    return {
        "portfolio": {
            "holdings_count": 5,
            "current_value_usd": 16_028.0,
            "invested_usd": 15_000.0,
            "pnl_usd": 1_028.0,
            "missing_prices": [],
            "skipped_rows": [],
            "skipped_reason": "",
            "derived_fx_reason": "",
            "inconsistent_reason": "",
            "top_holdings": [{"ticker": "VOO", "return_pct": 5.0}],
        },
        "networth": {"available": False},
        "screener": {
            "available": True,
            "unavailable_reason": "",
            "total_signals": 0,
            "symbols_with_signals": [],
            "by_preset": {},
        },
        "goals": goals if goals is not None else {"total": 2, "on_track": [], "off_track": []},
    }


class TestPromptAssemblyStaysInsideTheTry:
    """การประกอบพรอมป์ต้องอยู่ใน ``try`` เดียวกับการเรียก LLM — เจตนา ไม่ใช่บังเอิญ.

    ``_build_prompt`` อ่านคีย์ที่เส้นทางฟรีไม่ได้ใช้ (เช่น ``goals['total']``) วันที่
    contract ของ ``_aggregate_data`` เปลี่ยน (คีย์หาย/เปลี่ยนชื่อ) ถ้าพรอมป์ถูกประกอบ
    ก่อน ``try`` รายงานรายเดือนจะตายทั้งฉบับ แทนที่จะถอยไปเส้นทางฟรีพร้อมหมายเหตุ
    — คืออาการ K1/M-R4 กลับมาในรูปแบบใหม่ (AUDIT_ROUND2_2026-08-07)
    """

    def test_missing_prompt_only_key_falls_back_to_plain(self, monkeypatch):
        all_data = _all_data(goals={"on_track": ["เกษียณ"], "off_track": ["บ้าน"]})
        assert "total" not in all_data["goals"], "เคสนี้ต้องขาดคีย์ที่ใช้เฉพาะในพรอมป์"

        calls: list[tuple] = []

        def _must_not_be_called(*args, **kwargs):
            calls.append((args, kwargs))
            return "ไม่ควรมีข้อความนี้"

        monkeypatch.setattr(report_service, "chat_text", _must_not_be_called)

        # user_initiated=True เพื่อพิสูจน์ว่าที่ถอยไปเส้นทางฟรีคือ "ประกอบพรอมป์ไม่สำเร็จ"
        # ไม่ใช่ประตูคุมค่าใช้จ่าย (LLMDisabledError) ที่จะถอยอยู่แล้ว
        content, source = report_service.generate_narrative_with_source(
            all_data, "2026-08", user_initiated=True
        )

        assert source == "plain", "ประกอบพรอมป์ไม่สำเร็จ ต้องได้รายงานจากโมเดล ไม่ใช่ระเบิด"
        assert calls == [], "พรอมป์ยังประกอบไม่ได้ ห้ามยิง LLM (และห้ามเสียเงิน)"
        assert "AI เขียนบทสรุปไม่สำเร็จ" in content, "ต้องบอกผู้ใช้ว่าทำไมไม่มีบทสรุปจาก AI"
        assert "total" in content, "เหตุผลจริง (คีย์ที่หาย) ต้องอยู่ในหมายเหตุ"
        assert "📊 สรุปการเงินเดือน 2026-08" in content
        assert "16,028.00 USD" in content, "ตัวเลขจากโมเดลต้องยังอยู่ครบ"

    def test_complete_data_still_reaches_the_llm(self, monkeypatch):
        """เคสควบคุม — ข้อมูลครบต้องได้เส้นทาง AI ตามปกติ.

        กันไม่ให้เทสต์ข้างบน "เขียวเพราะเหตุผลอื่น" (เช่นเส้นทาง AI พังไปแล้วทั้งเส้น)
        """
        monkeypatch.setattr(
            report_service, "chat_text", lambda *a, **k: "บทสรุปจาก AI"
        )

        content, source = report_service.generate_narrative_with_source(
            _all_data(), "2026-08", user_initiated=True
        )

        assert source == "ai"
        assert content == "บทสรุปจาก AI"


# ── K8: หน้าต่าง 12 เดือนของ still_growing (ออดิตบรรทัด 578) ────────────────────

def _house_and_phone() -> list[Debt]:
    """หนี้บ้านดอกเบี้ยเดินเร็ว + ผ่อนมือถือ 0% ก้อนเล็ก (เคสเดียวกับ probe ของออดิต).

    บ้าน 6,000,000 @3.9% ⇒ ดอกเบี้ยเดือนละ ~19,500 ซึ่งสูงกว่ายอดขั้นต่ำ 5,000 มาก
    ผลลัพธ์จึงขึ้นกับว่า "เงินส่วนเกิน" ถูกส่งไปไหนในแต่ละเดือน — ซึ่งคือสิ่งที่หน้าต่าง
    12 เดือนวัด: negative amortization เกิด **ช่วงต้นแล้วหยุด** หรือ **ยังเดินอยู่**
    """
    return [
        Debt(name="บ้าน", balance=6_000_000, interest_rate=3.9, min_payment=5_000),
        Debt(name="ผ่อนมือถือ 0%", balance=50_000, interest_rate=0.0, min_payment=1_000),
    ]


class TestNegativeAmortizationRecencyWindow:
    """หนี้ไม่มีวันหมดมีสองสาเหตุ ต้องแยกกัน — คำแนะนำการเงินคนละเรื่อง.

    ``still_growing = any(months and months[-1] > month - 12 ...)`` คือหัวใจของ K8
    ตัดเงื่อนไข ``months[-1] > month - 12`` ทิ้ง (เหลือแค่ "เคยเกิด neg-amort ไหม")
    แล้วชุดเทสต์ 61 ตัวของ debt ยังเขียวหมด ทั้งที่ผู้ใช้จะถูกส่งไปรีไฟแนนซ์บ้าน
    ทั้งที่งบจ่ายดอกเบี้ยไหวอยู่แล้ว — สิ่งที่ต้องทำจริงคือเพิ่มงบต่อเดือน

    สองเคสข้างล่างใช้ **ชุดหนี้เดียวกัน** ต่างกันแค่งบ ⇒ สิ่งเดียวที่ทำให้ข้อความต่างกัน
    คือ *เวลาที่* negative amortization หยุด ไม่ใช่ว่ามันเคยเกิดหรือไม่
    """

    def test_early_only_negative_amortization_blames_slow_principal(self):
        # งบ 20,000: snowball เอาเงินส่วนเกินไปปิดมือถือก่อน (~4 เดือน) บ้านจึงจ่ายได้
        # แค่ 5,000 < ดอกเบี้ย 19,500 ในช่วงนั้น = neg-amort ช่วงต้น หลังมือถือหมด
        # บ้านได้เต็ม 20,000 ซึ่งคลุมดอกเบี้ยแล้ว — เงินต้นลดช้า แต่ไม่ได้โตขึ้นอีก
        with pytest.raises(ValueError) as exc:
            debt_service._simulate(_house_and_phone(), 20_000, "snowball")

        msg = str(exc.value)
        assert "เงินต้นลดช้า" in msg, "งบคลุมดอกเบี้ยแล้ว ต้องบอกว่าเงินต้นลดช้า"
        assert "รีไฟแนนซ์" not in msg, (
            "neg-amort หยุดไปตั้งแต่ปีแรก ห้ามส่งผู้ใช้ไปรีไฟแนนซ์"
        )

    def test_ongoing_negative_amortization_blames_the_interest(self):
        # งบ 6,000 = ยอดขั้นต่ำรวมพอดี บ้านได้ 5,000 (บวกอีก 1,000 หลังมือถือหมด)
        # ซึ่งยังต่ำกว่าดอกเบี้ย 19,500 ตลอด ⇒ หนี้โตขึ้นเรื่อย ๆ จนชนเพดาน 50 ปี
        with pytest.raises(ValueError) as exc:
            debt_service._simulate(_house_and_phone(), 6_000, "snowball")

        msg = str(exc.value)
        assert "ดอกเบี้ยเดินเร็วกว่าเงินต้น" in msg
        assert "รีไฟแนนซ์" in msg, "หนี้ยังโตอยู่จริง ต้องเสนอทางที่ลดดอกเบี้ย"
        assert "เงินต้นลดช้า" not in msg
