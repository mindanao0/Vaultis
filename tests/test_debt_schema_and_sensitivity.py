# -*- coding: utf-8 -*-
"""K8: ช่องโหว่ 3 จุดของ ``backend/models/debt_models.py`` + ``backend/services/debt_service.py``

1. ``/api/debt/sensitivity`` ไม่พกธง negative amortization ออกมาเลย
   A3 (H4) เพิ่มการตรวจไว้ใน ``_simulate`` แล้ว และ ``/api/debt/optimize`` ส่งออกทาง
   ``DebtSchedule.negative_amortization_months`` แต่ ``SensitivityResult`` มีแค่ 4 คีย์
   → ผู้ใช้ที่ถามว่า "จ่ายเพิ่มเดือนละเท่าไหร่ดี" ไม่รู้ว่าตัวเลือกที่จ่ายน้อยยังทำให้หนี้โตขึ้น
   ("ตัดข้อมูลทิ้งเงียบ" ผิดพอกับ "กุตัวเลข")

2. ``Debt`` รับ ``inf`` ได้ทั้ง 3 ฟิลด์ เพราะ ``Field(gt=0)`` และ ``inf > 0`` เป็น True
   ปลายทางไปชนเพดาน ``_MAX_MONTHS`` แล้ว raise ข้อความ "ดอกเบี้ยเดินเร็วกว่าเงินต้น"
   ซึ่ง **ไม่ใช่สาเหตุจริง** — ต้องปฏิเสธที่ชั้น schema พร้อมข้อความไทย

3. ``interest_rate: Field(gt=0)`` ปฏิเสธสินเชื่อ 0% ด้วย 422 ทั้งที่ผ่อน 0% พบบ่อยมากในไทย
"""

import math

import pytest
from pydantic import ValidationError

from backend.models.debt_models import Debt, OptimizeRequest, SensitivityRequest
from backend.services import debt_service

# ---------------------------------------------------------------------------
# ชุดข้อมูลทดสอบ
# ---------------------------------------------------------------------------

# บัตร 300,000 @28% → ดอกเบี้ยเดือนแรก 7,000 แต่ขั้นต่ำ 3,000 + เงินเหลือ 3,000 = 6,000
# → งบ 8,000 เกิด negative amortization จริง แต่ยังจบใน 114 เดือน (ไม่ชนเพดาน)
# เพิ่มอีก 3,000 → บัตรได้ 9,000 > 7,000 → หายขาด ใช้แยกตัวเลือก "ดี" กับ "หนี้ยังโต"
NEG_AMORT = [
    Debt(name="บัตรเครดิต", balance=300_000, interest_rate=28.0, min_payment=3_000),
    Debt(name="สินเชื่อสั้น", balance=20_000, interest_rate=5.0, min_payment=2_000),
]
NEG_BUDGET = 8_000.0

# ชุดควบคุม: ขั้นต่ำคลุมดอกเบี้ยทุกงวด — ตัวเลขของเคสนี้ต้องไม่ขยับหลังแก้
BASE = [
    Debt(name="บัตรเครดิต", balance=50_000, interest_rate=18.0, min_payment=2_000),
    Debt(name="สินเชื่อรถ", balance=200_000, interest_rate=5.0, min_payment=5_000),
]

NON_FINITE = [float("inf"), float("-inf"), float("nan"), "inf", "NaN"]


def _flag_map(result) -> dict[str, list[int]]:
    """ชื่อหนี้ → งวดที่ negative amortization (จาก schedules ของ ``_simulate``)."""
    return {
        s.name: list(s.negative_amortization_months)
        for s in result.schedules
        if s.negative_amortization_months
    }


# ---------------------------------------------------------------------------
# 1) sensitivity ต้องพกธง negative amortization ออกมา
# ---------------------------------------------------------------------------

class TestSensitivityCarriesNegativeAmortization:
    def test_base_row_flags_negative_amortization(self):
        """แถว extra=0 คืองบปัจจุบันของผู้ใช้ — ถ้าหนี้โตขึ้นต้องบอกตรงนั้น."""
        rows = debt_service.sensitivity_analysis(
            NEG_AMORT, NEG_BUDGET, "avalanche", [3_000]
        )
        base = rows[0]
        assert base.extra_payment == 0.0
        assert base.has_negative_amortization is True, (
            "งบปัจจุบันจ่ายไม่พอดอกเบี้ยบัตร ต้องขึ้นธง ไม่ใช่โชว์แค่ 'ดอกเบี้ยรวม/จำนวนเดือน'"
        )
        flagged = {f.name: list(f.months) for f in base.negative_amortization}
        assert "บัตรเครดิต" in flagged
        assert flagged["บัตรเครดิต"][0] == 1

    def test_option_that_fixes_it_has_no_flag(self):
        """ตัวเลือกที่จ่ายพอ ต้องไม่ขึ้นธงหลอก (ธงต้องแยกตัวเลือกได้จริง)."""
        rows = debt_service.sensitivity_analysis(
            NEG_AMORT, NEG_BUDGET, "avalanche", [3_000]
        )
        good = next(r for r in rows if r.extra_payment == 3_000)
        assert good.has_negative_amortization is False
        assert good.negative_amortization == []

    def test_flags_match_the_simulation_of_the_same_budget(self):
        """ธงในตาราง sensitivity ต้องตรงกับ ``_simulate`` ที่งบเดียวกัน ทุกแถว."""
        extras = [500.0, 3_000.0]
        rows = debt_service.sensitivity_analysis(
            NEG_AMORT, NEG_BUDGET, "avalanche", extras
        )
        for row in rows:
            expected = _flag_map(
                debt_service._simulate(
                    NEG_AMORT, NEG_BUDGET + row.extra_payment, "avalanche"
                )
            )
            actual = {f.name: list(f.months) for f in row.negative_amortization}
            assert actual == expected, f"แถว extra={row.extra_payment} ธงไม่ตรงกับการจำลอง"
            assert row.has_negative_amortization == bool(expected)

    def test_flag_keeps_duplicate_names_apart(self):
        """หนี้ชื่อซ้ำกันต้องไม่ถูกยุบรวม/ทับกันเงียบ ๆ (จึงต้องไม่ใช่ dict คีย์ชื่อ)."""
        dupes = [
            Debt(name="บัตรเครดิต", balance=200_000, interest_rate=28.0, min_payment=2_000),
            Debt(name="บัตรเครดิต", balance=100_000, interest_rate=26.0, min_payment=1_000),
            Debt(name="สินเชื่อสั้น", balance=6_000, interest_rate=5.0, min_payment=6_000),
        ]
        rows = debt_service.sensitivity_analysis(dupes, 9_000.0, "avalanche", [])
        base = rows[0]
        assert base.has_negative_amortization is True
        assert len(base.negative_amortization) == 2, "หนี้ 2 ก้อนชื่อซ้ำ ต้องรายงาน 2 รายการ"
        assert sorted(f.debt_index for f in base.negative_amortization) == [0, 1]

    def test_clean_case_reports_no_flag(self):
        rows = debt_service.sensitivity_analysis(BASE, 10_000.0, "avalanche", [500.0])
        assert all(r.has_negative_amortization is False for r in rows)
        assert all(r.negative_amortization == [] for r in rows)


class TestSensitivityApiContract:
    """ธงต้องเดินทางมาถึง JSON จริง ไม่ใช่อยู่แค่ในอ็อบเจกต์."""

    KEY = "test-key-K8"

    @pytest.fixture(autouse=True)
    def _api_key(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_API_KEY", self.KEY)

    @staticmethod
    def _client():
        from fastapi.testclient import TestClient

        from backend.main import app

        # ไม่ใช้ ``with`` → ไม่จุด lifespan (APScheduler 07:00)
        return TestClient(app)

    def _post(self, path, payload):
        return self._client().post(path, json=payload, headers={"X-API-Key": self.KEY})

    def test_sensitivity_json_exposes_flag(self):
        res = self._post(
            "/api/debt/sensitivity",
            {
                "debts": [d.model_dump() for d in NEG_AMORT],
                "monthly_budget": NEG_BUDGET,
                "method": "avalanche",
                "extra_payments": [3_000],
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()

        base = next(r for r in body if r["extra_payment"] == 0)
        assert base["has_negative_amortization"] is True, "ธงต้องอยู่ใน JSON ที่ส่งออก"
        names = [f["name"] for f in base["negative_amortization"]]
        assert "บัตรเครดิต" in names
        assert base["negative_amortization"][0]["months"][0] == 1

        good = next(r for r in body if r["extra_payment"] == 3_000)
        assert good["has_negative_amortization"] is False
        assert good["negative_amortization"] == []


# ---------------------------------------------------------------------------
# 2) inf / nan ต้องถูกปฏิเสธที่ชั้น schema พร้อมข้อความไทย
# ---------------------------------------------------------------------------

class TestNonFiniteRejectedAtSchema:
    @pytest.mark.parametrize("field", ["balance", "interest_rate", "min_payment"])
    @pytest.mark.parametrize("bad", NON_FINITE)
    def test_debt_rejects_non_finite(self, field, bad):
        kwargs = dict(name="x", balance=1_000.0, interest_rate=10.0, min_payment=100.0)
        kwargs[field] = bad
        with pytest.raises(ValidationError) as exc:
            Debt(**kwargs)
        err = exc.value.errors()[0]
        assert err["loc"] == (field,)
        assert "ตัวเลข" in err["msg"], f"ข้อความต้องเป็นไทยที่อ่านออก ได้: {err['msg']!r}"

    @pytest.mark.parametrize("bad", NON_FINITE)
    def test_monthly_budget_rejects_non_finite(self, bad):
        """งบ inf ทำให้จำลองแล้ว 'หนี้หมดใน 1 เดือน' — ตัวเลขไร้ความหมายที่ดูเหมือนคำตอบ."""
        payload = {
            "debts": [d.model_dump() for d in BASE],
            "monthly_budget": bad,
        }
        with pytest.raises(ValidationError):
            OptimizeRequest(**payload)
        with pytest.raises(ValidationError):
            SensitivityRequest(**payload)

    @pytest.mark.parametrize("bad", NON_FINITE)
    def test_extra_payments_reject_non_finite(self, bad):
        with pytest.raises(ValidationError):
            SensitivityRequest(
                debts=[d.model_dump() for d in BASE],
                monthly_budget=10_000.0,
                extra_payments=[500, bad],
            )

    def test_finite_values_still_pass(self):
        d = Debt(name="ok", balance=1_000.0, interest_rate=10.0, min_payment=100.0)
        assert d.balance == 1_000.0
        req = SensitivityRequest(
            debts=[d], monthly_budget=1_000.0, extra_payments=[0, 500, 1_000]
        )
        assert req.extra_payments == [0, 500, 1_000]

    def test_no_debt_object_can_hold_inf(self):
        """กันไม่ให้ ``_simulate`` ได้เห็น inf อีกเลย."""
        with pytest.raises(ValidationError):
            Debt(name="x", balance=math.inf, interest_rate=10.0, min_payment=100.0)


class TestNonFiniteApiContract:
    KEY = "test-key-K8"

    @pytest.fixture(autouse=True)
    def _api_key(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_API_KEY", self.KEY)

    @staticmethod
    def _client():
        from fastapi.testclient import TestClient

        from backend.main import app

        return TestClient(app)

    @pytest.mark.parametrize("field", ["balance", "interest_rate", "min_payment"])
    def test_infinite_number_is_422_with_thai_reason(self, field):
        """``1e999`` เป็น JSON ที่ถูกไวยากรณ์ และ ``json.loads`` แปลงเป็น ``inf``
        (จึงส่ง body ดิบ — httpx ไม่ยอม encode ``float('inf')`` ให้)."""
        fields = {"balance": "1000.0", "interest_rate": "10.0", "min_payment": "100.0"}
        fields[field] = "1e999"
        body = (
            '{"debts": [{"name": "หนี้พัง", '
            + ", ".join(f'"{k}": {v}' for k, v in fields.items())
            + '}], "monthly_budget": 5000, "method": "avalanche"}'
        )
        res = self._client().post(
            "/api/debt/optimize",
            content=body.encode("utf-8"),
            headers={"X-API-Key": self.KEY, "Content-Type": "application/json"},
        )
        assert res.status_code == 422, res.text
        text = res.text
        assert "ตัวเลข" in text, f"ต้องบอกสาเหตุจริงเป็นไทย ได้: {text}"
        assert "ดอกเบี้ยเดินเร็วกว่า" not in text, "ห้ามโทษสาเหตุผิด (เดิมไปชนเพดาน 50 ปี)"

    def test_infinite_monthly_budget_is_422(self):
        body = (
            '{"debts": [{"name": "บัตร", "balance": 50000, "interest_rate": 18.0, '
            '"min_payment": 2000}], "monthly_budget": 1e999, "method": "avalanche"}'
        )
        res = self._client().post(
            "/api/debt/optimize",
            content=body.encode("utf-8"),
            headers={"X-API-Key": self.KEY, "Content-Type": "application/json"},
        )
        assert res.status_code == 422, res.text
        assert "ตัวเลข" in res.text


# ---------------------------------------------------------------------------
# 3) ดอกเบี้ย 0% ต้องรับได้ และคำนวณถูก
# ---------------------------------------------------------------------------

class TestZeroInterestRate:
    def test_zero_percent_debt_is_accepted(self):
        d = Debt(name="ผ่อน 0% 10 เดือน", balance=12_000, interest_rate=0.0, min_payment=1_200)
        assert d.interest_rate == 0.0

    def test_negative_rate_still_rejected(self):
        with pytest.raises(ValidationError):
            Debt(name="x", balance=1_000, interest_rate=-1.0, min_payment=100)

    def test_zero_balance_and_zero_min_payment_still_rejected(self):
        """0% ที่เปิดรับคือ *ดอกเบี้ย* เท่านั้น ยอดหนี้/ขั้นต่ำ 0 ยังไร้ความหมายเหมือนเดิม."""
        with pytest.raises(ValidationError):
            Debt(name="x", balance=0.0, interest_rate=5.0, min_payment=100)
        with pytest.raises(ValidationError):
            Debt(name="x", balance=1_000, interest_rate=5.0, min_payment=0.0)

    def test_zero_percent_only_has_zero_interest(self):
        """ไม่มีหารด้วยศูนย์ และดอกเบี้ยต้องเป็น 0 เป๊ะ ไม่ใช่ NaN."""
        debts = [Debt(name="ผ่อน 0%", balance=12_000, interest_rate=0.0, min_payment=1_200)]
        result = debt_service._simulate(debts, 1_200.0, "avalanche")
        assert result.total_interest == 0.0
        assert result.months_to_payoff == 10
        assert result.schedules[0].negative_amortization_months == []
        cash = sum(e.payment for e in result.schedules[0].payments)
        assert cash == pytest.approx(12_000.0, abs=0.01)
        assert all(math.isfinite(e.interest) for e in result.schedules[0].payments)

    @pytest.mark.parametrize("method", ["avalanche", "snowball"])
    def test_zero_percent_mixed_with_card(self, method):
        """0% ปนกับบัตร 24% — ดอกเบี้ยรวมต้องมาจากบัตรก้อนเดียว และบัญชีต้องลงตัว."""
        debts = [
            Debt(name="ผ่อนมือถือ 0%", balance=24_000, interest_rate=0.0, min_payment=2_000),
            Debt(name="บัตรเครดิต", balance=50_000, interest_rate=24.0, min_payment=2_500),
        ]
        result = debt_service._simulate(debts, 8_000.0, method)
        phone = next(s for s in result.schedules if s.name == "ผ่อนมือถือ 0%")
        assert phone.total_interest == 0.0
        assert result.total_interest == pytest.approx(
            next(s.total_interest for s in result.schedules if s.name == "บัตรเครดิต"),
            abs=0.05,
        )
        cash = sum(e.payment for s in result.schedules for e in s.payments)
        assert cash - 74_000.0 == pytest.approx(result.total_interest, abs=1.0)

    def test_zero_percent_via_api(self, monkeypatch):
        from fastapi.testclient import TestClient

        from backend.main import app

        monkeypatch.setenv("VAULTIS_API_KEY", "test-key-K8")
        client = TestClient(app)
        res = client.post(
            "/api/debt/optimize",
            headers={"X-API-Key": "test-key-K8"},
            json={
                "debts": [
                    {
                        "name": "ผ่อน 0%",
                        "balance": 12_000,
                        "interest_rate": 0,
                        "min_payment": 1_200,
                    }
                ],
                "monthly_budget": 1_200,
                "method": "avalanche",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["method"] == "avalanche"
        assert body["total_interest"] == 0.0
        assert body["months_to_payoff"] == 10

    def test_slow_zero_percent_loan_is_not_blamed_on_interest(self):
        """0% ที่ผ่อนเกิน 50 ปี ต้องบอกสาเหตุจริง (ผ่อนช้า) ไม่ใช่ 'ดอกเบี้ยเดินเร็วกว่าเงินต้น'."""
        debts = [Debt(name="0% ยาวมาก", balance=1_000_000, interest_rate=0.0, min_payment=1_000)]
        with pytest.raises(ValueError) as exc:
            debt_service._simulate(debts, 1_000.0, "avalanche")
        msg = str(exc.value)
        assert "ดอกเบี้ยเดินเร็วกว่า" not in msg, f"สาเหตุผิด: {msg}"
        assert "50 ปี" in msg

    def test_runaway_debt_still_blames_interest(self):
        """เคสที่ดอกเบี้ยเดินเร็วกว่าเงินต้นจริง ต้องยังบอกสาเหตุเดิม."""
        debts = [Debt(name="บัตร", balance=500_000, interest_rate=28.0, min_payment=5_000)]
        with pytest.raises(ValueError) as exc:
            debt_service._simulate(debts, 5_000.0, "avalanche")
        assert "ดอกเบี้ยเดินเร็วกว่า" in str(exc.value)


# ---------------------------------------------------------------------------
# 4) เคสปกติต้องไม่เปลี่ยน
# ---------------------------------------------------------------------------

class TestNoRegression:
    @pytest.mark.parametrize("method", ["avalanche", "snowball"])
    def test_base_numbers_unchanged(self, method):
        result = debt_service._simulate(BASE, 10_000.0, method)
        assert result.months_to_payoff == 27
        assert result.total_interest == pytest.approx(18_192.31, abs=0.01)

    def test_sensitivity_numbers_unchanged(self):
        rows = debt_service.sensitivity_analysis(BASE, 10_000.0, "avalanche", [500, 1_000])
        assert [r.extra_payment for r in rows] == [0.0, 500.0, 1_000.0]
        assert [r.total_interest for r in rows] == pytest.approx(
            [18_192.31, 17_131.86, 16_196.29], abs=0.01
        )
        assert [r.months_to_payoff for r in rows] == [27, 26, 25]
        assert [r.interest_saved for r in rows] == pytest.approx(
            [0.0, 1_060.45, 1_996.02], abs=0.01
        )

    def test_budget_below_minimum_still_rejected(self):
        with pytest.raises(ValueError, match="น้อยกว่ายอดขั้นต่ำ"):
            debt_service._simulate(BASE, 3_000.0, "avalanche")
