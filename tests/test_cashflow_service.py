# -*- coding: utf-8 -*-
"""B1 — `backend/services/cashflow_service.py` + models + router (6 ข้อ).

เดิมไม่มีเทสต์ cashflow สักตัว (coverage 18%) ทั้งหกข้อเป็นเรื่องเดียวกัน:
ข้อมูลที่ "ไม่ครบ" หรือ "ไม่ตรง" ถูกกลืนเข้าไปเป็นตัวเลขพยากรณ์เงียบ ๆ

- B1.1 เดือนที่ยังไม่จบถูกเฉลี่ยเป็นเดือนเต็ม → ค่าเฉลี่ย/threshold ต่ำกว่าจริง
- B1.2 scenario ที่ระบุหมวดไม่ตรงถูกทิ้งเงียบ (ตอบ 200 เหมือนไม่ได้ส่งอะไรมา)
- B1.3 scenario ซ้ำหมวดเดียวกัน อันก่อนหน้าถูกทับ
- B1.4 วันที่ผิดรูปสร้าง bucket ขยะ
- B1.5 `change_percent` ไม่มีขอบเขต → รายจ่ายติดลบ = เสกเงินเข้า
- B1.6 หมวดที่เพิ่งเกิดครั้งแรกถูกข้าม (`avg_val == 0`) แม้เป็นก้อนใหญ่ที่สุด
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.models.cashflow_models import ScenarioAdjustment, TransactionItem
from backend.services import cashflow_service as cs

# วันอ้างอิงตายตัวสำหรับเทสต์ระดับ service — มิ.ย./ก.ค. 2026 ครบเดือน, ส.ค. ยังค้าง
TODAY = date(2026, 8, 7)


def _tx(d: str, amount: float, category: str, typ: str) -> TransactionItem:
    return TransactionItem(date=d, amount=amount, category=category, type=typ)


def _full_months() -> list[TransactionItem]:
    """มิ.ย.+ก.ค. เต็มเดือน: รายรับ 60,000/เดือน รายจ่าย 50,000/เดือน"""
    out: list[TransactionItem] = []
    for m in ("2026-06", "2026-07"):
        out.append(_tx(f"{m}-25", 60_000, "เงินเดือน", "income"))
        out.append(_tx(f"{m}-05", 25_000, "ที่พัก", "expense"))
        out.append(_tx(f"{m}-10", 15_000, "อาหาร", "expense"))
        out.append(_tx(f"{m}-15", 10_000, "อื่นๆ", "expense"))
    return out


def _with_partial_august() -> list[TransactionItem]:
    """เพิ่ม ส.ค. 1–6 ที่มีแต่รายจ่าย (เงินเดือนออกวันที่ 25 ยังไม่ถึง)"""
    return _full_months() + [
        _tx("2026-08-02", 5_000, "ที่พัก", "expense"),
        _tx("2026-08-04", 3_000, "อาหาร", "expense"),
        _tx("2026-08-06", 1_000, "อื่นๆ", "expense"),
    ]


# ---------------------------------------------------------------------------
# B1.1 — เดือนที่ยังไม่จบต้องไม่ถูกเฉลี่ยเป็นเดือนเต็ม
# ---------------------------------------------------------------------------

class TestPartialMonthB1_1:
    def test_partial_month_does_not_drag_averages_down(self):
        """ส.ค. ที่มีแต่รายจ่าย 6 วันแรก ต้องไม่ทำให้ค่าเฉลี่ยเปลี่ยน"""
        full = cs.build_forecast_response(_full_months(), 12, 0.0, today=TODAY)
        part = cs.build_forecast_response(_with_partial_august(), 12, 0.0, today=TODAY)

        assert full.forecast[0].projected_income == pytest.approx(60_000.0)
        assert full.forecast[0].projected_expense == pytest.approx(50_000.0)
        # เดือนค้างต้องไม่เข้าค่าเฉลี่ย → ตัวเลขต้องเท่ากับชุดเต็มเดือนล้วน
        assert part.forecast[0].projected_income == pytest.approx(60_000.0)
        assert part.forecast[0].projected_expense == pytest.approx(50_000.0)

    def test_excluded_partial_month_is_reported_not_silent(self):
        """ตัดข้อมูลทิ้งได้ แต่ต้องรายงานออกไปให้ผู้ใช้เห็น"""
        part = cs.build_forecast_response(_with_partial_august(), 3, 0.0, today=TODAY)
        assert part.months_used == 2
        assert part.excluded_partial_months == ["2026-08"]

        full = cs.build_forecast_response(_full_months(), 3, 0.0, today=TODAY)
        assert full.months_used == 2
        assert full.excluded_partial_months == []

    def test_emergency_threshold_uses_complete_months_only(self):
        """threshold ดีฟอลต์ = 3 × รายจ่ายเฉลี่ย ต้องคิดจากเดือนครบเท่านั้น

        ยอดตั้งต้น 120,000 ตกอยู่ระหว่าง threshold จริง (150,000) กับ threshold
        ที่ถูกเดือนค้างกดลงมา (109,000) — เดือนค้างจึงพลิกคำเตือนจาก True เป็น False
        """
        part = cs.build_forecast_response(_with_partial_august(), 1, 120_000.0, today=TODAY)
        full = cs.build_forecast_response(_full_months(), 1, 120_000.0, today=TODAY)
        assert full.emergency_alert is True
        assert part.emergency_alert is True
        assert part.emergency_message == full.emergency_message

    def test_no_complete_month_refuses_to_forecast(self):
        """มีแต่เดือนที่ยังไม่จบ → พยากรณ์ไม่ได้ ต้องบอกตรง ๆ ห้ามคืนตัวเลข"""
        only_partial = [
            _tx("2026-08-02", 5_000, "ที่พัก", "expense"),
            _tx("2026-08-04", 60_000, "เงินเดือน", "income"),
        ]
        with pytest.raises(ValueError):
            cs.build_forecast_response(only_partial, 3, 0.0, today=TODAY)

    def test_no_transactions_refuses_to_forecast(self):
        """ไม่มีธุรกรรมเลย ≠ รายรับ/รายจ่าย 0 บาท — ห้ามคืน projection ศูนย์"""
        with pytest.raises(ValueError):
            cs.build_forecast_response([], 3, 0.0, today=TODAY)

    def test_partial_month_is_not_used_as_anomaly_last_month(self):
        """เดือนค้างเทียบกับเดือนเต็มไม่ได้ — เดิมแจ้ง 'ลดลง 80–90%' ทุกหมวด"""
        part = cs.build_forecast_response(_with_partial_august(), 3, 0.0, today=TODAY)
        assert part.anomalies == []


# ---------------------------------------------------------------------------
# B1.2 — scenario ที่หมวดไม่ตรงต้องไม่ถูกทิ้งเงียบ
# ---------------------------------------------------------------------------

class TestUnknownScenarioCategoryB1_2:
    def test_unknown_category_raises(self):
        with pytest.raises(ValueError) as exc:
            cs.build_forecast_response(
                _full_months(), 3, 0.0,
                scenarios=[ScenarioAdjustment(category="อาหารการกิน", change_percent=-20)],
                today=TODAY,
            )
        assert "อาหารการกิน" in str(exc.value)

    def test_known_category_still_applies(self):
        """เคสที่เคยถูกต้องอยู่แล้วต้องไม่พัง: อาหาร 15,000 ลด 20% → 50,000 − 3,000"""
        r = cs.build_forecast_response(
            _full_months(), 3, 0.0,
            scenarios=[ScenarioAdjustment(category="อาหาร", change_percent=-20)],
            today=TODAY,
        )
        assert r.forecast[0].projected_expense == pytest.approx(47_000.0)

    def test_income_only_category_is_unknown_for_scenarios(self):
        """scenario ปรับได้เฉพาะหมวดรายจ่าย — หมวดรายรับต้องไม่เงียบ"""
        with pytest.raises(ValueError):
            cs.build_forecast_response(
                _full_months(), 3, 0.0,
                scenarios=[ScenarioAdjustment(category="เงินเดือน", change_percent=-20)],
                today=TODAY,
            )


# ---------------------------------------------------------------------------
# B1.3 — scenario ซ้ำหมวดต้องไม่ทับกันเงียบ
# ---------------------------------------------------------------------------

class TestDuplicateScenarioB1_3:
    def test_duplicate_category_raises(self):
        with pytest.raises(ValueError) as exc:
            cs.build_forecast_response(
                _full_months(), 3, 0.0,
                scenarios=[
                    ScenarioAdjustment(category="ที่พัก", change_percent=-50),
                    ScenarioAdjustment(category="ที่พัก", change_percent=-10),
                ],
                today=TODAY,
            )
        assert "ที่พัก" in str(exc.value)

    def test_distinct_categories_accumulate(self):
        """สองหมวดต่างกันต้องรวมผลกัน: ที่พัก −50% (−12,500) + อาหาร −20% (−3,000)"""
        r = cs.build_forecast_response(
            _full_months(), 3, 0.0,
            scenarios=[
                ScenarioAdjustment(category="ที่พัก", change_percent=-50),
                ScenarioAdjustment(category="อาหาร", change_percent=-20),
            ],
            today=TODAY,
        )
        assert r.forecast[0].projected_expense == pytest.approx(34_500.0)


# ---------------------------------------------------------------------------
# B1.4 — วันที่ผิดรูปต้องถูกปฏิเสธ ไม่ใช่กลายเป็น bucket ขยะ
# ---------------------------------------------------------------------------

class TestMalformedDateB1_4:
    @pytest.mark.parametrize("bad", ["", "06/08/2026", "2026-13-01", "ไม่ทราบ", "2026-06"])
    def test_bad_date_rejected_by_model(self, bad):
        with pytest.raises(ValidationError):
            _tx(bad, 1_000, "อาหาร", "expense")

    def test_iso_date_still_accepted(self):
        assert _tx("2026-06-25", 1_000, "อาหาร", "expense").date == date(2026, 6, 25)


# ---------------------------------------------------------------------------
# B1.5 — change_percent ต้องมีขอบเขต
# ---------------------------------------------------------------------------

class TestChangePercentBoundsB1_5:
    @pytest.mark.parametrize("bad", [-300.0, -100.01, 1_000.01, 5_000.0])
    def test_out_of_range_rejected(self, bad):
        with pytest.raises(ValidationError):
            ScenarioAdjustment(category="ที่พัก", change_percent=bad)

    @pytest.mark.parametrize("ok", [-100.0, -20.0, 0.0, 50.0, 1_000.0])
    def test_in_range_accepted(self, ok):
        assert ScenarioAdjustment(category="ที่พัก", change_percent=ok).change_percent == ok

    def test_expense_can_never_go_negative(self):
        """ตัดทุกหมวดเหลือศูนย์ = รายจ่าย 0 ไม่ใช่ติดลบ (รายจ่ายติดลบ = เสกเงินเข้า)"""
        r = cs.build_forecast_response(
            _full_months(), 3, 0.0,
            scenarios=[
                ScenarioAdjustment(category=c, change_percent=-100)
                for c in ("ที่พัก", "อาหาร", "อื่นๆ")
            ],
            today=TODAY,
        )
        assert r.forecast[0].projected_expense == pytest.approx(0.0)
        assert r.forecast[0].projected_expense >= 0.0


# ---------------------------------------------------------------------------
# B1.6 — หมวดที่โผล่ครั้งแรกต้องไม่ถูกข้าม
# ---------------------------------------------------------------------------

class TestNewCategoryAnomalyB1_6:
    def _with_new_big_category(self) -> list[TransactionItem]:
        return _full_months() + [
            _tx("2026-07-20", 80_000, "ค่ารักษาพยาบาล", "expense"),
        ]

    def test_new_big_category_is_flagged(self):
        r = cs.build_forecast_response(self._with_new_big_category(), 3, 0.0, today=TODAY)
        cats = [a.category for a in r.anomalies]
        assert "ค่ารักษาพยาบาล" in cats

    def test_new_category_percent_is_none_not_zero(self):
        """เทียบกับฐาน 0 เป็นเปอร์เซ็นต์ไม่ได้ — ห้ามกุเป็น 0.0"""
        r = cs.build_forecast_response(self._with_new_big_category(), 3, 0.0, today=TODAY)
        new = next(a for a in r.anomalies if a.category == "ค่ารักษาพยาบาล")
        assert new.kind == "new_category"
        assert new.change_percent is None
        assert new.last_month == pytest.approx(80_000.0)

    def test_tiny_new_category_is_not_noise(self):
        """หมวดใหม่ยอดจิ๊บจ๊อยไม่ใช่ความผิดปกติ — ไม่งั้นรายการเตือนเต็มไปด้วยขยะ"""
        data = _full_months() + [_tx("2026-07-20", 100, "ค่าโอน", "expense")]
        r = cs.build_forecast_response(data, 3, 0.0, today=TODAY)
        assert [a.category for a in r.anomalies] == []

    def test_ordinary_change_anomaly_still_detected(self):
        """เคสเดิมต้องไม่พัง: อาหาร 15,000 → 30,000 = +100%"""
        data = _full_months() + [_tx("2026-07-28", 15_000, "อาหาร", "expense")]
        r = cs.build_forecast_response(data, 3, 0.0, today=TODAY)
        food = next(a for a in r.anomalies if a.category == "อาหาร")
        assert food.kind == "change"
        assert food.change_percent == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# router — ความล้มเหลวต้องออกมาเป็น 4xx ที่อ่านรู้เรื่อง ไม่ใช่ 200 หรือ 500
# ---------------------------------------------------------------------------

def _month_key(back: int) -> str:
    t = date.today()
    year, month = t.year, t.month - back
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def _relative_full_months() -> list[dict]:
    """สองเดือนก่อนหน้า (ครบเดือนแน่นอนไม่ว่าจะรันวันไหน)"""
    out: list[dict] = []
    for back in (2, 1):
        m = _month_key(back)
        out += [
            {"date": f"{m}-25", "amount": 60_000, "category": "เงินเดือน", "type": "income"},
            {"date": f"{m}-05", "amount": 25_000, "category": "ที่พัก", "type": "expense"},
            {"date": f"{m}-10", "amount": 15_000, "category": "อาหาร", "type": "expense"},
            {"date": f"{m}-15", "amount": 10_000, "category": "อื่นๆ", "type": "expense"},
        ]
    return out


@pytest.fixture()
def client() -> TestClient:
    from backend.routers import cashflow as router_mod

    # `_stored_transactions` เป็น global ระดับโมดูล — ต้องล้างทั้งก่อนและหลัง
    # ไม่งั้นชุดที่เคสหนึ่งอิมพอร์ตไว้ไปโผล่ในเคสถัดไป
    router_mod._stored_transactions = []
    app = FastAPI()
    app.include_router(router_mod.router)
    yield TestClient(app, raise_server_exceptions=False)
    router_mod._stored_transactions = []


class TestRouterContract:
    def test_scenario_unknown_category_is_422_not_200(self, client):
        r = client.post("/api/cashflow/scenario", json={
            "months": 3,
            "current_balance": 0,
            "transactions": _relative_full_months(),
            "scenarios": [{"category": "อาหารการกิน", "change_percent": -20}],
        })
        assert r.status_code == 422, r.text
        assert "อาหารการกิน" in r.text

    def test_scenario_duplicate_category_is_422(self, client):
        r = client.post("/api/cashflow/scenario", json={
            "months": 3,
            "current_balance": 0,
            "transactions": _relative_full_months(),
            "scenarios": [
                {"category": "ที่พัก", "change_percent": -50},
                {"category": "ที่พัก", "change_percent": -10},
            ],
        })
        assert r.status_code == 422, r.text

    def test_out_of_range_change_percent_is_422(self, client):
        r = client.post("/api/cashflow/scenario", json={
            "months": 3,
            "current_balance": 0,
            "transactions": _relative_full_months(),
            "scenarios": [{"category": "ที่พัก", "change_percent": -300}],
        })
        assert r.status_code == 422, r.text

    def test_malformed_date_is_422(self, client):
        bad = _relative_full_months() + [
            {"date": "06/08/2026", "amount": 1_000, "category": "อาหาร", "type": "expense"},
        ]
        r = client.post("/api/cashflow/scenario", json={
            "months": 3, "current_balance": 0, "transactions": bad, "scenarios": [],
        })
        assert r.status_code == 422, r.text

    def test_happy_path_reports_months_used(self, client):
        r = client.post("/api/cashflow/scenario", json={
            "months": 3,
            "current_balance": 0,
            "transactions": _relative_full_months(),
            "scenarios": [],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["months_used"] == 2
        assert body["excluded_partial_months"] == []
        assert body["forecast"][0]["projected_income"] == pytest.approx(60_000.0)

    def test_only_partial_month_is_422_not_500(self, client):
        m = _month_key(0)
        r = client.post("/api/cashflow/scenario", json={
            "months": 3,
            "current_balance": 0,
            "transactions": [
                {"date": f"{m}-01", "amount": 5_000, "category": "ที่พัก", "type": "expense"},
            ],
            "scenarios": [],
        })
        assert r.status_code == 422, r.text


class TestForecastRoute:
    """GET /forecast อ่านจากชุดที่ POST /transactions/bulk เก็บไว้"""

    def test_partial_month_excluded_and_reported(self, client):
        m_now = _month_key(0)
        txs = _relative_full_months() + [
            {"date": f"{m_now}-01", "amount": 4_000, "category": "ที่พัก", "type": "expense"},
        ]
        assert client.post(
            "/api/cashflow/transactions/bulk", json={"transactions": txs}
        ).status_code == 201

        r = client.get("/api/cashflow/forecast", params={"months": 3, "current_balance": 0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["months_used"] == 2
        assert body["excluded_partial_months"] == [m_now]
        assert body["forecast"][0]["projected_income"] == pytest.approx(60_000.0)
        assert body["forecast"][0]["projected_expense"] == pytest.approx(50_000.0)

    def test_bulk_rejects_malformed_date(self, client):
        r = client.post("/api/cashflow/transactions/bulk", json={"transactions": [
            {"date": "31/12/2026", "amount": 1_000, "category": "อาหาร", "type": "expense"},
        ]})
        assert r.status_code == 422, r.text

    def test_only_partial_month_is_422_not_a_number(self, client):
        m_now = _month_key(0)
        client.post("/api/cashflow/transactions/bulk", json={"transactions": [
            {"date": f"{m_now}-01", "amount": 5_000, "category": "ที่พัก", "type": "expense"},
        ]})
        r = client.get("/api/cashflow/forecast")
        assert r.status_code == 422, r.text
        assert m_now in r.json()["detail"]
