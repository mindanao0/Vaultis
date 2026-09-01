# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 (mutation M30) — เส้นแบ่ง "ไม่ต้องทำอะไร" ของแผน rebalance.

``_build_actions()`` ตัดสินด้วย ``abs(delta_usd) < HOLD_BAND_USD`` ว่า ETF ตัวนี้
"ต่างน้อยจนไม่คุ้มทำ" (hold) หรือ "ต้องซื้อ/ขายจริง" — เดิมเป็นเลข ``0.01`` ลอยอยู่
กลางฟังก์ชัน ขยายเป็น ``100.0`` แล้วชุดเทสต์เต็มยังเขียวครบ 1297 ตัว ทั้งที่ส่วนต่าง
จริงหลักสิบดอลลาร์กลายเป็น ``hold`` ทั้งพอร์ต ผู้ใช้เห็นว่า "ไม่ต้องทำอะไร" ซึ่งเป็น
**คำตอบที่ผิดแต่หน้าตาเหมือนคำตอบที่ถูก** (ไม่ใช่ error จึงไม่มีอะไรเตือน)

ไฟล์นี้ตรึงไว้ 4 ชั้น ต้องมีครบทั้ง 4 เพราะแต่ละชั้นปิดคนละรู:

1. **ค่าของค่าคงที่** — ``HOLD_BAND_USD == 0.01`` จับการแก้ตัวเลขตรง ๆ
2. **พฤติกรรมที่ตัวเลขจริงตายตัว** — 0.005 USD ต้อง hold, 0.02 USD และ 30 USD
   ต้องเป็นคำสั่งซื้อ/ขาย · เทสต์กลุ่มนี้ **ห้ามคำนวณฉากจาก ``HOLD_BAND_USD``**
   ไม่งั้นฉากจะเลื่อนตามค่าคงที่ที่ถูกแก้แล้วเขียวต่อไป (คือรูเดิมเป๊ะ ๆ)
3. **การต่อสาย** — ``_build_actions`` ต้องอ่านค่าคงที่ของโมดูลจริง ไม่ใช่เลขที่เขียนซ้ำ
   ไว้ในตัวฟังก์ชัน (ถ้าเขียนซ้ำ ชั้นที่ 1 จะเขียวทั้งที่พฤติกรรมเปลี่ยนไปแล้ว)
4. **ปลายทางที่ผู้ใช้เห็น** — ``compute_rebalance()`` กับพอร์ตที่ XLV ขาดอยู่ 30 USD
   ต้องยังสั่ง "ซื้อ" ไม่ใช่กลืนเป็น hold (30 USD อยู่ในช่วงที่ mutation M30 กลืนหาย
   พอดี — เทสต์เดิมที่มีอยู่ใช้ delta 150 USD จึงรอดมาได้)

ไม่มีเทสต์ในไฟล์นี้แตะเน็ต: ``_build_actions`` รับราคามาเป็นอาร์กิวเมนต์ ส่วนเทสต์
ปลายทางแทน ``get_current_prices``/``_get_usdthb_rate``/``chat_text`` ด้วยของจำลอง

ท้ายไฟล์ยังตรึงด่าน **งบก้อนใหม่** ของโหมด "ดึงเข้าเป้าโดยไม่ขาย"
(``portfolio/cashflow_rebalance.py``) ไว้ด้วย — เป็นเส้นแบ่งบนเส้นทางเงินอีกเส้น
ที่เขียนด้วยการเปรียบเทียบล้วน ๆ จึงปล่อย ``nan``/``inf`` ผ่านไปตายข้างหน้า
"""

from __future__ import annotations

import pytest

from analysis.llm import LLMDisabledError
from backend.services import rebalance_service
from portfolio.cashflow_rebalance import UNIT_THB, rebalance_with_new_money
from portfolio.fees import dime_fee_thb

FX_RATE = 35.0

# ฉากสังเคราะห์: ราคาหน่วยละ 1 USD ทั้งคู่ → "จำนวนหน่วย" กับ "มูลค่า" เป็นเลขเดียวกัน
# จึงตั้ง delta ได้ตรง ๆ โดยไม่ต้องเดาผลการปัดเศษ
UNIT_PRICES = {"AAA": 1.0, "BBB": 1.0}
HALF_HALF = {"AAA": 0.5, "BBB": 0.5}

# ตัวเลขดิบทั้งหมดด้านล่างเขียนเป็นค่าคงที่จริง ๆ ห้ามอ้าง ``HOLD_BAND_USD``
BELOW_BAND_USD = 0.005  # เศษปัดทศนิยม — ไม่คุ้มค่าธรรมเนียมแน่นอน
ABOVE_BAND_USD = 0.02  # เกินเส้นแค่นิดเดียว แต่เกินคือเกิน
REAL_MONEY_DELTA_USD = 30.0  # ~1,050 บาท — เงินจริงที่ mutation M30 (แบนด์ 100) กลืนหาย


def _actions_for_delta(delta_usd: float) -> dict[str, dict]:
    """แผนของฉากที่ AAA **ขาดอยู่** ``delta_usd`` และ BBB **เกินอยู่** เท่ากันพอดี.

    มูลค่ารวม 200 USD เป้า 50/50 → เป้าของแต่ละตัว 100 USD
    AAA ถือ ``100 - delta`` (ต้องซื้อเพิ่ม delta) · BBB ถือ ``100 + delta`` (ต้องขาย delta)
    งบใหม่ = 0 เพื่อให้ delta มาจากส่วนต่างของพอร์ตล้วน ๆ ไม่ปนกับเงินก้อนใหม่
    """
    holdings = [
        {"symbol": "AAA", "shares": 100.0 - delta_usd},
        {"symbol": "BBB", "shares": 100.0 + delta_usd},
    ]
    actions = rebalance_service._build_actions(
        holdings, HALF_HALF, UNIT_PRICES, 0.0, FX_RATE
    )
    return {a["symbol"]: a for a in actions}


class TestHoldBandConstant:
    """ชั้นที่ 1 — ค่าของเส้นแบ่งเอง."""

    def test_hold_band_is_one_us_cent(self):
        """0.01 USD ≈ 0.35 บาท = ขนาดของเศษปัดทศนิยม ไม่ใช่ส่วนต่างที่มีความหมาย.

        ถ้าวันหนึ่งมีเหตุผลให้ขยายเส้นนี้จริง (เช่นคิดค่าธรรมเนียมขั้นต่ำ) ต้องแก้ที่นี่
        พร้อมเหตุผล ไม่ใช่เลื่อนเงียบ ๆ — และต้องระวังหน่วย: **เป็นดอลลาร์ ไม่ใช่บาท**
        เขียนเป็น 100 โดยคิดว่าเป็นบาทจะได้แบนด์ 3,500 บาท
        """
        assert rebalance_service.HOLD_BAND_USD == 0.01

    def test_hold_band_is_greppable_by_name(self):
        """เกณฑ์นี้ต้องมีชื่อ — เลขลอยกลางฟังก์ชันคือสิ่งที่ทำให้ M30 หลุดมาได้."""
        assert hasattr(rebalance_service, "HOLD_BAND_USD")


class TestHoldBandBehaviour:
    """ชั้นที่ 2 — พฤติกรรมสองฝั่งของเส้น ด้วยตัวเลขที่ตายตัว."""

    def test_delta_below_the_band_is_a_hold_with_no_money_attached(self):
        """ต่ำกว่าเส้น = ไม่ทำอะไร และต้อง **ไม่มีตัวเลขเงินติดมา** สักช่อง.

        ``hold`` ที่ยังพก ``usd_amount``/``fee_thb`` ออกไปด้วยจะถูกบวกเข้า
        ``total_fee_thb`` กลายเป็นค่าธรรมเนียมของธุรกรรมที่ไม่ได้เกิดขึ้น
        """
        actions = _actions_for_delta(BELOW_BAND_USD)

        for sym in ("AAA", "BBB"):
            assert actions[sym]["action"] == "hold", f"{sym}: 0.005 USD ไม่คุ้มทำ"
            assert actions[sym]["usd_amount"] == 0.0
            assert actions[sym]["thb_amount"] == 0.0
            assert actions[sym]["shares"] == 0.0
            assert actions[sym]["fee_thb"] == 0.0

    def test_delta_above_the_band_becomes_a_real_order(self):
        """เหนือเส้นแค่ 0.01 USD ก็ต้องเป็นคำสั่งจริง — ทิศต้องถูกด้วย (ขาด=ซื้อ)."""
        actions = _actions_for_delta(ABOVE_BAND_USD)

        assert actions["AAA"]["action"] == "buy"
        assert actions["AAA"]["usd_amount"] == pytest.approx(ABOVE_BAND_USD, abs=1e-6)
        assert actions["BBB"]["action"] == "sell"
        assert actions["BBB"]["usd_amount"] == pytest.approx(ABOVE_BAND_USD, abs=1e-6)

    def test_real_money_delta_is_never_swallowed_as_a_hold(self):
        """หัวใจของ M30: 30 USD (~1,050 บาท) คือเงินจริง ห้ามรายงานว่า "ไม่ต้องทำอะไร".

        แบนด์ที่ถูกเลื่อนเป็น 100 USD จะกลืน delta ช่วง 0.01–100 ทั้งหมดเป็น hold
        โดยไม่มี error ใด ๆ ให้ผู้ใช้เห็น
        """
        actions = _actions_for_delta(REAL_MONEY_DELTA_USD)

        assert actions["AAA"]["action"] == "buy"
        assert actions["AAA"]["usd_amount"] == pytest.approx(30.0, abs=0.01)
        assert actions["AAA"]["thb_amount"] == pytest.approx(30.0 * FX_RATE, abs=0.01)
        assert actions["BBB"]["action"] == "sell"

    def test_traded_action_carries_the_fee_from_the_single_fee_module(self):
        """ค่าธรรมเนียมของคำสั่งจริงต้อง > 0 และมาจาก ``portfolio/fees.py`` แหล่งเดียว.

        (ที่ไม่ตรวจ ``fee_thb > 0`` ที่ delta 0.02 USD เพราะค่าธรรมเนียมจริงคือ
        0.001 บาท ซึ่งปัด 2 ตำแหน่งแล้วเป็น 0.00 — เป็นผลของการปัด ไม่ใช่การกลืนค่า
        จึงตรวจที่ขนาดคำสั่งที่มีความหมายจริงแทน)
        """
        actions = _actions_for_delta(REAL_MONEY_DELTA_USD)

        expected_fee = dime_fee_thb(30.0, FX_RATE)
        assert expected_fee > 0
        assert actions["AAA"]["fee_thb"] == pytest.approx(expected_fee, abs=0.01)
        assert actions["BBB"]["fee_thb"] == pytest.approx(expected_fee, abs=0.01)


class TestHoldBandWiring:
    """ชั้นที่ 3 — ``_build_actions`` ต้องอ่านค่าคงที่จริง ไม่ใช่เลขที่เขียนซ้ำในตัวเอง."""

    def test_widening_the_constant_changes_the_decision(self, monkeypatch):
        monkeypatch.setattr(rebalance_service, "HOLD_BAND_USD", 50.0)
        actions = _actions_for_delta(REAL_MONEY_DELTA_USD)

        assert actions["AAA"]["action"] == "hold", "ฟังก์ชันไม่ได้อ่าน HOLD_BAND_USD"
        assert actions["AAA"]["usd_amount"] == 0.0

    def test_narrowing_the_constant_changes_the_decision(self, monkeypatch):
        monkeypatch.setattr(rebalance_service, "HOLD_BAND_USD", 1e-9)
        actions = _actions_for_delta(BELOW_BAND_USD)

        assert actions["AAA"]["action"] == "buy", "ฟังก์ชันไม่ได้อ่าน HOLD_BAND_USD"


# ---------------------------------------------------------------------------
# ชั้นที่ 4 — ปลายทางที่ผู้ใช้เห็นจริง (compute_rebalance)
# ---------------------------------------------------------------------------
# สัดส่วนเป้าหมายอ่านจากแหล่งเดียวของระบบเสมอ (portfolio/targets.py)
TARGET = rebalance_service.resolve_target_weights("moderate")
PRICES_FULL = {"VOO": 500.0, "SCHD": 25.0, "QQQM": 200.0, "XLV": 150.0, "GLDM": 64.0}


@pytest.fixture
def stub_env(monkeypatch):
    """แทนราคา/FX/LLM — เทสต์ห้ามแตะเน็ตและห้ามจ่ายค่า LLM."""
    state = {"prices": dict(PRICES_FULL), "llm_calls": 0}

    def _fake_prices(tickers):
        return {t: state["prices"][t] for t in tickers if t in state["prices"]}

    def _fake_chat(system, user, *, user_initiated=False, **kwargs):
        if not user_initiated:
            raise LLMDisabledError("AI ถูกปิดไว้เพื่อคุมค่าใช้จ่าย (จำลอง)")
        state["llm_calls"] += 1
        return "คำอธิบายจำลอง"

    monkeypatch.setattr(rebalance_service, "get_current_prices", _fake_prices)
    monkeypatch.setattr(rebalance_service, "_get_usdthb_rate", lambda: FX_RATE)
    monkeypatch.setattr(rebalance_service, "chat_text", _fake_chat)
    return state


def _holdings_where_xlv_is_short_by(delta_usd: float) -> list[dict]:
    """พอร์ตที่ GLDM เกินเป้ามากพอจะสั่ง rebalance และ XLV ขาดอยู่ ``delta_usd`` พอดี.

    มูลค่าตัวอื่นตรึงไว้ แล้วแก้เฉพาะมูลค่า XLV (= x) จากสมการ
    ``delta = w·(others + x) − x`` ⇒ ``x = (w·others − delta) / (1 − w)``
    จึงได้ delta ตามต้องการเป๊ะ ๆ ไม่ว่าน้ำหนักเป้าหมายใน config จะเป็นเท่าใด
    """
    values = {"VOO": 35000.0, "SCHD": 25000.0, "QQQM": 20000.0, "GLDM": 17000.0}
    w = TARGET["XLV"]
    others = sum(values.values())
    xlv_value = (w * others - delta_usd) / (1.0 - w)
    values["XLV"] = xlv_value
    return [
        {"symbol": sym, "shares": value / PRICES_FULL[sym]}
        for sym, value in values.items()
    ]


class TestHoldBandEndToEnd:
    """ชั้นที่ 4 — แผนที่ผู้ใช้ได้รับจริงต้องไม่กลืนเงินหลักสิบดอลลาร์เป็น hold."""

    def test_thirty_dollar_shortfall_is_reported_as_a_buy(self, stub_env):
        holdings = _holdings_where_xlv_is_short_by(REAL_MONEY_DELTA_USD)
        result = rebalance_service.compute_rebalance(holdings, "moderate", 0.0)

        assert result["needs_rebalance"] is True, "ฉากนี้ต้องเกิน DRIFT_THRESHOLD"
        assert result["missing_prices"] == []

        xlv = next(a for a in result["actions"] if a["symbol"] == "XLV")
        assert xlv["action"] == "buy"
        assert xlv["usd_amount"] == pytest.approx(REAL_MONEY_DELTA_USD, abs=0.01)

    def test_a_cent_of_drift_is_still_reported_as_a_hold(self, stub_env):
        """อีกฝั่งของเส้น: 0.005 USD ต้องไม่กลายเป็นคำสั่งซื้อที่ค่าธรรมเนียมแพงกว่าของ."""
        holdings = _holdings_where_xlv_is_short_by(BELOW_BAND_USD)
        result = rebalance_service.compute_rebalance(holdings, "moderate", 0.0)

        xlv = next(a for a in result["actions"] if a["symbol"] == "XLV")
        assert xlv["action"] == "hold"
        assert xlv["fee_thb"] == 0.0


# ---------------------------------------------------------------------------
# ด่านงบก้อนใหม่ของโหมด "ดึงเข้าเป้าโดยไม่ขาย" (portfolio/cashflow_rebalance.py)
# ---------------------------------------------------------------------------
# พอร์ตจำลอง: VOO 10,000 / SCHD 5,000 บาท เป้า 50/50 → SCHD ขาดอยู่ จึงมีแผนจริงให้ทำ
CASHFLOW_HOLDINGS = {"VOO": 10_000.0, "SCHD": 5_000.0}
CASHFLOW_TARGET = {"VOO": 0.5, "SCHD": 0.5}


class TestNewMoneyBudgetGuard:
    """AUDIT_ROUND2_2026-08-07 — ``nan``/``inf`` ผ่านด่านงบ แล้วไปตายด้วยข้อความอังกฤษ.

    ด่านถูกเขียนใหม่รอบนี้จาก ``budget_thb <= 0`` เป็น ``budget_thb < UNIT_THB``
    แต่ยังเป็นการเปรียบเทียบล้วน ๆ ซึ่ง **``nan`` เทียบกับอะไรก็ False** งบ ``nan``
    จึงรอดไปตายที่ ``int(budget_thb // UNIT_THB)`` ด้วย ``ValueError: cannot convert
    float NaN to integer`` และ ``inf`` ก็ได้ข้อความ **เดียวกัน** (ชี้สาเหตุผิด)

    ปลายทางคือ ``dashboard/app.py`` ที่จับด้วย ``except Exception as exc`` แล้วโชว์
    ``str(exc)`` ตรง ๆ ⇒ ผู้ใช้เห็นภาษาอังกฤษที่บอกว่าเป็น NaN ทั้งที่ค่าจริงเป็น inf
    """

    @pytest.mark.parametrize(
        "bad_budget",
        [float("nan"), float("inf"), float("-inf"), "หมื่นนึง", None],
        ids=["nan", "inf", "neg_inf", "text", "none"],
    )
    def test_unusable_budget_fails_in_thai_before_any_rounding(self, bad_budget):
        with pytest.raises(ValueError) as excinfo:
            rebalance_with_new_money(CASHFLOW_HOLDINGS, CASHFLOW_TARGET, bad_budget)

        message = str(excinfo.value)
        assert "งบ" in message, f"ข้อความต้องเป็นภาษาไทย (ได้: {message})"
        assert "convert" not in message and "float" not in message, (
            f"หลุดไปถึง int()/float() ของ Python แล้ว = ด่านไม่ได้กัน (ได้: {message})"
        )

    @pytest.mark.parametrize("bad_budget", [float("nan"), float("inf"), float("-inf")])
    def test_unusable_budget_is_not_blamed_on_the_minimum(self, bad_budget):
        """"ค่าใช้ไม่ได้" ≠ "งบน้อยกว่าขั้นต่ำ" — สองสาเหตุ ต้องคนละข้อความ.

        ``inf`` ไม่ได้แปลว่างบน้อยเกินไปสักนิด ถ้าตอบว่า "ต้องอย่างน้อย 100 บาท"
        ผู้ใช้จะไปเพิ่มงบซึ่งแก้ไม่ตรงจุด
        """
        with pytest.raises(ValueError) as excinfo:
            rebalance_with_new_money(CASHFLOW_HOLDINGS, CASHFLOW_TARGET, bad_budget)

        assert f"อย่างน้อย {UNIT_THB} บาท" not in str(excinfo.value)

    def test_budget_below_one_unit_still_says_it_is_too_small(self):
        """ตัวคุม: สาเหตุจริง ๆ ที่ "งบน้อยไป" ต้องยังได้ข้อความเดิม ไม่ถูกกลืนเข้าด่านใหม่."""
        for small in (0.0, -100.0, 99.9):
            with pytest.raises(ValueError) as excinfo:
                rebalance_with_new_money(CASHFLOW_HOLDINGS, CASHFLOW_TARGET, small)
            assert f"อย่างน้อย {UNIT_THB} บาท" in str(excinfo.value)

    def test_usable_budget_still_produces_a_plan_that_spends_every_baht(self):
        """ตัวคุมของทั้งกลุ่ม: งบที่ใช้ได้ต้องยังได้แผน และเงินต้องไม่หายระหว่างทาง."""
        plan = rebalance_with_new_money(CASHFLOW_HOLDINGS, CASHFLOW_TARGET, 5_000.0)

        assert plan, "งบ 5,000 บาทต้องได้แผนจริง"
        assert plan.budget_thb == pytest.approx(5_000.0)
        spent = sum(item["amount_thb"] for item in plan.values())
        assert spent + plan.unallocated_thb == pytest.approx(5_000.0), (
            "sum(amount_thb) + unallocated_thb ต้องเท่ากับงบเสมอ — เงินห้ามหายเงียบ"
        )
