# -*- coding: utf-8 -*-
"""A3 (H4): debt_service นับเฉพาะ "ดอกเบี้ยที่จ่ายไหว" ไม่ใช่ "ดอกเบี้ยที่เกิดขึ้นจริง".

เมื่อ ``min_payment`` น้อยกว่าดอกเบี้ยรายเดือน ส่วนต่างถูกทบเข้าเงินต้น (หนี้โตขึ้น)
แต่ ``total_interest`` นับแค่ ``min(interest, payment)`` → ต่ำกว่าจริงเสมอ และต่ำไม่เท่ากัน
ระหว่าง avalanche กับ snowball จน ``interest_saved`` พลิกเครื่องหมายได้

หลักฐานที่ใช้ตรวจคือ identity ทางบัญชี ไม่ใช่ simulator ที่เขียนขึ้นใหม่:
    รวมเงินสดที่จ่ายจริง − ยอดหนี้ตั้งต้น = ดอกเบี้ยที่เกิดจริง
"""

import pytest

from backend.models.debt_models import Debt
from backend.services import debt_service

# ชุดหนี้ที่ min_payment ของบัตรเครดิต (3,000) < ดอกเบี้ยเดือนแรก (200,000 × 26%/12 = 4,333.33)
# → negative amortization จริง
NEG_AMORT = [
    Debt(name="สินเชื่อบุคคล", balance=20_000, interest_rate=8.0, min_payment=1_000),
    Debt(name="บัตรเครดิต", balance=200_000, interest_rate=26.0, min_payment=3_000),
]

# ชุดควบคุม: min_payment คลุมดอกเบี้ยทุกงวด — เคสนี้เดิมก็ถูกอยู่แล้ว ต้องไม่พังหลังแก้
COVERED = [
    Debt(name="บัตรเครดิต", balance=50_000, interest_rate=18.0, min_payment=2_000),
    Debt(name="สินเชื่อรถ", balance=200_000, interest_rate=5.0, min_payment=5_000),
]


def _cash_paid(result) -> float:
    return sum(e.payment for s in result.schedules for e in s.payments)


def _start_balance(debts) -> float:
    return sum(d.balance for d in debts)


@pytest.mark.parametrize("method", ["avalanche", "snowball"])
@pytest.mark.parametrize(
    "debts,budget",
    [(NEG_AMORT, 6_000), (COVERED, 10_000)],
    ids=["neg_amort", "covered"],
)
def test_total_interest_equals_cash_minus_principal(debts, budget, method):
    """ดอกเบี้ยที่รายงาน ต้องเท่ากับ เงินสดที่จ่ายจริง − ยอดหนี้ตั้งต้น."""
    result = debt_service._simulate(debts, budget, method)
    true_interest = _cash_paid(result) - _start_balance(debts)
    assert result.total_interest == pytest.approx(true_interest, abs=1.0)


def test_interest_saved_does_not_flip_sign():
    """avalanche ถูกกว่าจริงในเคสนี้ (208,772.50 vs 217,410.66) → interest_saved ต้องเป็นบวก."""
    cmp = debt_service.compare_methods(NEG_AMORT, 6_000)
    start = _start_balance(NEG_AMORT)
    true_av = _cash_paid(cmp.avalanche) - start
    true_sn = _cash_paid(cmp.snowball) - start
    assert true_av < true_sn, "เคสทดสอบต้องเป็นเคสที่ avalanche ถูกกว่าจริง"
    assert cmp.interest_saved == pytest.approx(true_sn - true_av, abs=1.0)
    assert cmp.interest_saved > 0


def test_payment_rows_reconcile_with_balance():
    """ทุกแถวต้องลงตัว: principal = payment − interest และยอดคงเหลือลดลงเท่ากับ principal.

    เดิมแถวแรกของบัตรเครดิตใน snowball โชว์ "จ่ายดอกเบี้ย 3,000 เงินต้น 0"
    ขณะยอดหนี้เพิ่ม 1,333.33 — ไม่ลงตัวกับตัวเอง
    """
    result = debt_service._simulate(NEG_AMORT, 6_000, "snowball")
    card = next(s for s in result.schedules if s.name == "บัตรเครดิต")

    prev = 200_000.0
    for entry in card.payments:
        assert entry.principal == pytest.approx(entry.payment - entry.interest, abs=0.02)
        assert entry.remaining_balance == pytest.approx(prev - entry.principal, abs=0.02)
        prev = entry.remaining_balance

    # แถวแรก: ดอกเบี้ยที่เกิดจริง 4,333.33 ไม่ใช่ 3,000 และเงินต้นติดลบเพราะหนี้โต
    first = card.payments[0]
    assert first.interest == pytest.approx(4_333.33, abs=0.01)
    assert first.principal == pytest.approx(-1_333.33, abs=0.01)
    assert first.remaining_balance == pytest.approx(201_333.33, abs=0.01)


def test_negative_amortization_months_are_reported():
    """ธง fail-loud: งวดที่จ่ายไม่พอดอกเบี้ยต้องรายงานออกไป ไม่ใช่ซ่อนไว้ในตาราง."""
    result = debt_service._simulate(NEG_AMORT, 6_000, "snowball")
    card = next(s for s in result.schedules if s.name == "บัตรเครดิต")
    loan = next(s for s in result.schedules if s.name == "สินเชื่อบุคคล")

    assert card.negative_amortization_months, "บัตรเครดิตจ่ายขั้นต่ำแล้วหนี้โต ต้องมีธง"
    assert card.negative_amortization_months[0] == 1
    assert all(
        card.payments[m - 1].payment < card.payments[m - 1].interest
        for m in card.negative_amortization_months
    )
    # งวดที่ไม่ติดธง ต้องไม่ใช่ negative amortization จริง ๆ
    flagged = set(card.negative_amortization_months)
    for entry in card.payments:
        if entry.month not in flagged:
            assert entry.payment >= entry.interest - 0.01
    assert loan.negative_amortization_months == []


def test_covered_case_has_no_negative_amortization_flag():
    """เคสที่ขั้นต่ำคลุมดอกเบี้ย ต้องไม่ขึ้นธงหลอก."""
    result = debt_service._simulate(COVERED, 10_000, "avalanche")
    for s in result.schedules:
        assert s.negative_amortization_months == []


@pytest.mark.parametrize("method", ["avalanche", "snowball"])
def test_schedule_totals_sum_to_result_total(method):
    """ผลรวมดอกเบี้ยรายก้อน ต้องตรงกับดอกเบี้ยรวมของแผน."""
    result = debt_service._simulate(NEG_AMORT, 6_000, method)
    assert sum(s.total_interest for s in result.schedules) == pytest.approx(
        result.total_interest, abs=0.05
    )


class TestDebtApiContract:
    """ธงต้องไปถึงผู้ใช้จริง ไม่ใช่อยู่แค่ในอ็อบเจกต์ — ยิงผ่าน TestClient เท่านั้น."""

    KEY = "test-key-A3"

    @pytest.fixture(autouse=True)
    def _api_key(self, monkeypatch):
        # ตั้งคีย์ทดสอบทับของจริงใน .env (load_dotenv ใช้ override=False จึงไม่เขียนทับกลับ)
        monkeypatch.setenv("VAULTIS_API_KEY", self.KEY)

    @staticmethod
    def _client():
        from fastapi.testclient import TestClient

        from backend.main import app

        # ไม่ใช้ ``with`` → ไม่จุด lifespan (APScheduler 07:00)
        return TestClient(app)

    def test_optimize_response_exposes_negative_amortization(self):
        payload = {
            "debts": [d.model_dump() for d in NEG_AMORT],
            "monthly_budget": 6_000,
            "method": "both",
        }
        res = self._client().post(
            "/api/debt/optimize", json=payload, headers={"X-API-Key": self.KEY}
        )
        assert res.status_code == 200, res.text
        body = res.json()

        assert body["interest_saved"] > 0
        card = next(
            s for s in body["snowball"]["schedules"] if s["name"] == "บัตรเครดิต"
        )
        assert card["negative_amortization_months"], "ธงต้องอยู่ใน JSON ที่ส่งออก"
        assert card["payments"][0]["principal"] < 0, "เงินต้นติดลบ = หนี้โต ห้ามบีบเป็น 0"
