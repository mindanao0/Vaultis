# -*- coding: utf-8 -*-
"""FIX_PLAN 1.3 — ราคาที่ดึงไม่ได้ต้องไม่กลายเป็น 0 แล้วพลิกคำสั่งซื้อเป็นขาย.

contract ของ ``alerts.price_alert.get_current_prices()`` คือ ticker ที่ดึงไม่ได้จะ
**หายไปจาก dict** (ไม่ใช่มีค่าเป็น 0) แต่ ``rebalance_service`` เดิมใช้
``prices.get(sym, 0.0)`` ทำให้ของที่ถืออยู่ถูกตีมูลค่า 0 → ตัวหารเล็กลง →
ตัวอื่นกลายเป็น overweight → คำสั่ง "ซื้อ" พลิกเป็น "ขาย" ด้วยเงินจริง
"""

from __future__ import annotations

import ast
import inspect

import pytest

from analysis.llm import LLMDisabledError
from backend.services import rebalance_service

# ---------------------------------------------------------------------------
# ฉากจำลอง: พอร์ต moderate (VOO .35 / SCHD .25 / QQQM .20 / XLV .10 / GLDM .10)
# มูลค่าจริง: VOO 35,000 / SCHD 25,000 / QQQM 20,000 / XLV 10,500 / GLDM 16,000
# → รวม 106,500 USD  → XLV ควร "ซื้อเพิ่ม" (เป้า 10,650)
# ถ้า GLDM หายไปจากตัวส่วน (เหลือ 90,500) → เป้า XLV เหลือ 9,050 → พลิกเป็น "ขาย"
# ---------------------------------------------------------------------------
PRICES_FULL = {"VOO": 500.0, "SCHD": 25.0, "QQQM": 200.0, "XLV": 150.0, "GLDM": 64.0}
PRICES_NO_GLDM = {k: v for k, v in PRICES_FULL.items() if k != "GLDM"}
HOLDINGS = [
    {"symbol": "VOO", "shares": 70.0},
    {"symbol": "SCHD", "shares": 1000.0},
    {"symbol": "QQQM", "shares": 100.0},
    {"symbol": "XLV", "shares": 70.0},
    {"symbol": "GLDM", "shares": 250.0},
]
# AUDIT_2026-08-06 B4.1 — สัดส่วนเป้าหมายอ่านจากแหล่งเดียว (portfolio/targets.py)
# ไม่ใช่ preset ดิบใน rebalance_service อีกต่อไป · ค่าที่ได้กับ config ดีฟอลต์
# (risk_profile=moderate, target_weights ว่าง, 5 ticker) เท่ากับ preset moderate เป๊ะ
# ตัวเลขที่ตรึงไว้ในไฟล์นี้จึงไม่เปลี่ยน
TARGET = rebalance_service.resolve_target_weights("moderate")
FX_RATE = 35.0


@pytest.fixture
def stub_env(monkeypatch):
    """แทนที่ราคา/FX/LLM — เทสต์ห้ามแตะเน็ตและห้ามจ่ายค่า LLM."""

    state = {"prices": dict(PRICES_FULL), "llm_calls": 0, "prompts": []}

    def _fake_prices(tickers):
        return {t: state["prices"][t] for t in tickers if t in state["prices"]}

    def _fake_chat(system, user, *, user_initiated=False, **kwargs):
        """เลียนแบบด่านคุมค่าใช้จ่ายของ ``chat_text`` จริง — ไม่ได้กดขอเอง = ไม่จ่าย.

        ``llm_calls`` จึงนับเฉพาะ "ครั้งที่เสียเงินจริง" ไม่ใช่จำนวนครั้งที่ถูกเรียก
        """
        if not user_initiated:
            raise LLMDisabledError("AI ถูกปิดไว้เพื่อคุมค่าใช้จ่าย (จำลอง)")
        state["llm_calls"] += 1
        state["prompts"].append(user)
        return "คำอธิบายจำลอง"

    monkeypatch.setattr(rebalance_service, "get_current_prices", _fake_prices)
    monkeypatch.setattr(rebalance_service, "_get_usdthb_rate", lambda: FX_RATE)
    monkeypatch.setattr(rebalance_service, "chat_text", _fake_chat)
    return state


def _action(result, symbol):
    for a in result["actions"]:
        if a["symbol"] == symbol:
            return a
    return None


class TestPricesComplete:
    """ราคาครบ = พฤติกรรมเดิมต้องไม่เปลี่ยน."""

    def test_full_prices_still_plan_buy_for_xlv(self, stub_env):
        result = rebalance_service.compute_rebalance(HOLDINGS, "moderate", 0.0)

        assert result["needs_rebalance"] is True
        assert result["missing_prices"] == []
        # GLDM 15.02% เทียบเป้า 10% → drift สูงสุด ~5.02%
        assert result["max_drift_pct"] == pytest.approx(5.02, abs=0.01)

        xlv = _action(result, "XLV")
        assert xlv is not None
        assert xlv["action"] == "buy"
        assert xlv["usd_amount"] == pytest.approx(150.0, abs=0.01)
        assert _action(result, "GLDM")["action"] == "sell"

    def test_full_prices_drift_matches_hand_calculation(self, stub_env):
        drift = rebalance_service.calculate_drift(HOLDINGS, TARGET, PRICES_FULL)
        assert drift == pytest.approx(16000 / 106500 - 0.10, abs=1e-6)


class TestHeldTickerMissingPrice:
    """GLDM ดึงไม่ได้ = ไม่มีแผน ไม่ใช่แผนที่พลิกทิศ."""

    def test_missing_gldm_produces_no_actions(self, stub_env):
        stub_env["prices"] = dict(PRICES_NO_GLDM)
        result = rebalance_service.compute_rebalance(HOLDINGS, "moderate", 0.0)

        assert result["actions"] == []
        assert result["missing_prices"] == ["GLDM"]
        assert result["needs_rebalance"] is None
        assert result["max_drift_pct"] is None
        assert "GLDM" in result["detail"]

    def test_missing_gldm_never_flips_xlv_into_a_sell(self, stub_env):
        """หลักฐานตรงของบั๊ก: เดิม XLV buy 150 → sell 1,450 เพราะ GLDM ถูกตีมูลค่า 0."""
        stub_env["prices"] = dict(PRICES_NO_GLDM)
        result = rebalance_service.compute_rebalance(HOLDINGS, "moderate", 0.0)

        sells = [a["symbol"] for a in result["actions"] if a["action"] == "sell"]
        assert "XLV" not in sells
        assert sells == []

    def test_missing_price_does_not_pay_for_llm(self, stub_env):
        """ไม่มีแผน = ไม่มีอะไรให้ AI อธิบาย ห้ามเผา credit."""
        stub_env["prices"] = dict(PRICES_NO_GLDM)
        result = rebalance_service.compute_rebalance(
            HOLDINGS, "moderate", 0.0, user_initiated=True
        )

        assert stub_env["llm_calls"] == 0
        assert result["ai_comment"] == ""

    def test_target_ticker_without_price_also_fails_closed(self, stub_env):
        """ETF เป้าหมายที่ยังไม่ถือแต่ไม่มีราคา = ตัดออกจากแผนเงียบ ๆ ไม่ได้."""
        holdings = [h for h in HOLDINGS if h["symbol"] != "GLDM"]
        stub_env["prices"] = dict(PRICES_NO_GLDM)
        result = rebalance_service.compute_rebalance(holdings, "moderate", 0.0)

        assert result["missing_prices"] == ["GLDM"]
        assert result["actions"] == []

    def test_zero_share_position_without_price_is_not_a_failure(self, stub_env):
        """ของที่ขายหมดแล้ว (0 หน่วย) ไม่ต้องใช้ราคา — ห้าม fail closed ทิ้งเปล่า ๆ."""
        holdings = [*HOLDINGS, {"symbol": "AAPL", "shares": 0.0}]
        result = rebalance_service.compute_rebalance(holdings, "moderate", 0.0)

        assert result["missing_prices"] == []
        assert result["needs_rebalance"] is True


    def test_symbol_with_whitespace_is_not_reported_as_a_fetch_failure(self, stub_env):
        """" GLDM " ดึงราคาได้จริง — ห้ามรายงานว่า "ดึงไม่สำเร็จ" เพราะคีย์ไม่ตรงกันเอง.

        ``get_current_prices()`` normalize เป็น strip+upper ถ้าฝั่ง rebalance ไม่ strip
        ด้วย จะได้ ``missing_prices=[' GLDM ']`` ซึ่งเป็นความล้มเหลวที่ไม่ได้เกิดขึ้นจริง
        """
        holdings = [dict(h) for h in HOLDINGS]
        holdings[-1]["symbol"] = " gldm "
        result = rebalance_service.compute_rebalance(holdings, "moderate", 0.0)

        assert result["missing_prices"] == []
        assert result["needs_rebalance"] is True
        assert _action(result, "XLV")["action"] == "buy"


class TestAllPricesMissing:
    """ราคาหายหมด = คำนวณ drift ไม่ได้ ห้ามคืน 1.0 (100% ที่ผลิตจากความล้มเหลว).

    ``calculate_drift()`` มี **สองกิ่ง** ที่คำนวณไม่ได้ และทั้งคู่เคยคืน 1.0 มาก่อน
    (FIX_PLAN 1.3) — เทสต์ต้องเดินเข้าทั้งสองกิ่งจริง ไม่ใช่กิ่งเดียวสองครั้ง:

    1. ``missing_holding_prices`` — ของที่ถืออยู่ไม่มีราคา = **ดึงราคาไม่สำเร็จ**
    2. ``total <= 0`` — ราคาครบแต่ยังไม่มีหน่วยลงทุน = **ไม่มีอะไรให้เทียบสัดส่วน**

    เดิมมีเทสต์ตัวเดียวที่ส่ง ``prices={}`` ซึ่งไปโดนกิ่งที่ 1 ตั้งแต่บรรทัดแรก
    กิ่งที่ 2 จึงไม่เคยถูกเดินผ่านเลย: แทรก ``return 1.0`` กลับเข้าไปในกิ่งนั้นแล้ว
    ชุดเทสต์เต็มยังเขียวครบ 1297 ตัว (AUDIT_ROUND2_2026-08-07 mutation M26)
    ทั้งที่มันคือบั๊กเดิมที่ AUDIT ปิดไปแล้ว — พอร์ตที่ขายหมดจะรายงาน "เบี่ยงเบน 100%"
    """

    def test_calculate_drift_raises_instead_of_returning_one(self):
        """กิ่งที่ 1: ของที่ถืออยู่ไม่มีราคา."""
        with pytest.raises(ValueError) as exc:
            rebalance_service.calculate_drift(HOLDINGS, TARGET, {})
        assert "ราคา" in str(exc.value)
        # ต้องเป็นสาเหตุ "ดึงราคาไม่สำเร็จ" ไม่ใช่ "มูลค่ารวมเป็น 0" — คนละเรื่องกัน
        assert "ดึงราคาไม่สำเร็จ" in str(exc.value)

    def test_calculate_drift_raises_when_the_portfolio_is_empty(self):
        """กิ่งที่ 2 (ก): ราคาครบทุกตัว แต่ยังไม่ถืออะไรเลย → 0/0 ไม่ใช่ 100%.

        ต้องส่ง ``PRICES_FULL`` เข้าไป ไม่งั้นจะไปติดกิ่งที่ 1 ก่อนแล้วไม่ได้ทดสอบอะไร
        """
        with pytest.raises(ValueError) as exc:
            rebalance_service.calculate_drift([], TARGET, PRICES_FULL)
        assert "มูลค่าพอร์ตรวมเป็น 0" in str(exc.value)
        assert "ดึงราคาไม่สำเร็จ" not in str(exc.value)

    def test_calculate_drift_raises_when_every_position_is_zero_shares(self):
        """กิ่งที่ 2 (ข): พอร์ตที่ขายหมดแล้ว (ทุกแถว shares=0) ก็ต้องดังแบบเดียวกัน.

        วันนี้ ``compute_rebalance()`` มีด่าน ``if not held:`` กันไว้อีกชั้น ผู้ใช้จึง
        ยังไม่เจอ แต่ ``calculate_drift()`` เป็นฟังก์ชันสาธารณะที่ถูกเรียกตรง ๆ ได้
        — ถ้าใครย้าย/ลบด่านนั้นในอนาคต ตาข่ายต้องอยู่ตรงนี้
        """
        sold_out = [{"symbol": h["symbol"], "shares": 0.0} for h in HOLDINGS]
        with pytest.raises(ValueError) as exc:
            rebalance_service.calculate_drift(sold_out, TARGET, PRICES_FULL)
        assert "มูลค่าพอร์ตรวมเป็น 0" in str(exc.value)
        assert "ดึงราคาไม่สำเร็จ" not in str(exc.value)

    def test_compute_rebalance_reports_every_missing_symbol(self, stub_env):
        stub_env["prices"] = {}
        result = rebalance_service.compute_rebalance(HOLDINGS, "moderate", 50000.0)

        assert result["needs_rebalance"] is None
        assert result["max_drift_pct"] is None
        assert result["actions"] == []
        assert set(result["missing_prices"]) == {"VOO", "SCHD", "QQQM", "XLV", "GLDM"}

    def test_nan_price_counts_as_missing(self, stub_env):
        stub_env["prices"] = {**PRICES_FULL, "GLDM": float("nan")}
        result = rebalance_service.compute_rebalance(HOLDINGS, "moderate", 0.0)

        assert result["missing_prices"] == ["GLDM"]
        assert result["actions"] == []


# ราคาที่ "มีค่าแต่ใช้ไม่ได้" ทุกแบบ — ต้องเดินเส้นทางเดียวกับ "ราคาหายไปจาก dict"
UNUSABLE_PRICES = [0.0, -0.0, -3.0, float("inf"), float("-inf"), float("nan")]


class TestUnusablePriceValues:
    """AUDIT_ROUND2_2026-08-07 — ราคา 0 / ติดลบ ถูกรับเป็นราคาจริง แล้วเทสต์ยังเขียวหมด.

    ตาข่ายเดิมครอบแค่ 2 แบบ: "ticker หายไปจาก dict" (``prices={}``) กับ ``nan``
    ถอดเงื่อนไข ``price <= 0`` ออกจาก ``_usable_price()`` แล้วชุดเทสต์เต็มยังเขียว
    1297 ตัว (mutation M28) ทั้งที่ ``0`` คือสำนวนที่ wrapper yfinance หลายตัวใช้แทน
    "ดึงไม่ได้" — ถ้าแหล่งราคาเปลี่ยน contract มาแบบนั้น บั๊กจะกลับมาโดยไม่มีใครแดง

    ความเสียหายที่วัดได้จริงกับโค้ดที่ถอดด่านออก (พอร์ตในไฟล์นี้ GLDM ราคาเสีย):
    - ราคา ``0``  → GLDM ถูกตีมูลค่า 0 → ``missing_prices=[]`` → ``calculate_drift``
      คืน 0.1 ที่ผลิตจากความล้มเหลวล้วน ๆ แล้ว ``usd_amount / price`` ระเบิดเป็น
      ``ZeroDivisionError`` ดิบ (500 ภาษาอังกฤษ ไม่มี detail ไทย)
    - ราคา ``-3`` → ได้แผนเต็มรูปแบบที่ **สั่งขายทั้งพอร์ต** (VOO 3,587.50 /
      SCHD 2,562.50 / QQQM 2,050 / XLV 1,525 USD) และ "ซื้อ" GLDM -3,241.67 หน่วย
    """

    @pytest.mark.parametrize("bad_price", UNUSABLE_PRICES)
    def test_usable_price_rejects_every_unusable_value(self, bad_price):
        """ด่านเดียวของทั้งไฟล์: ค่าที่ใช้ไม่ได้ต้องกลายเป็น ``None`` เท่านั้น."""
        assert rebalance_service._usable_price("GLDM", {"GLDM": bad_price}) is None

    def test_usable_price_still_accepts_a_real_price(self):
        """กันการแก้ข้อนี้ไปปิดราคาจริงทิ้ง — ``ดึงไม่สำเร็จ`` ต้องไม่กินราคาที่ใช้ได้."""
        assert rebalance_service._usable_price("GLDM", {"GLDM": 64.0}) == 64.0
        assert rebalance_service._usable_price("GLDM", {"GLDM": 0.01}) == 0.01

    @pytest.mark.parametrize("bad_price", UNUSABLE_PRICES)
    def test_held_ticker_with_unusable_price_is_reported_as_missing(self, stub_env, bad_price):
        stub_env["prices"] = {**PRICES_FULL, "GLDM": bad_price}
        result = rebalance_service.compute_rebalance(HOLDINGS, "moderate", 0.0)

        assert result["missing_prices"] == ["GLDM"]
        assert result["actions"] == []
        assert result["needs_rebalance"] is None
        assert result["max_drift_pct"] is None
        assert "GLDM" in result["detail"]

    @pytest.mark.parametrize("bad_price", UNUSABLE_PRICES)
    def test_unusable_price_never_flips_the_portfolio_into_sells(self, stub_env, bad_price):
        """หลักฐานตรงของบั๊ก: ราคาติดลบทำให้ ETF ที่เหลือกลายเป็นคำสั่งขายทั้งพอร์ต."""
        stub_env["prices"] = {**PRICES_FULL, "GLDM": bad_price}
        result = rebalance_service.compute_rebalance(HOLDINGS, "moderate", 0.0)

        assert [a["symbol"] for a in result["actions"] if a["action"] == "sell"] == []

    @pytest.mark.parametrize("bad_price", UNUSABLE_PRICES)
    def test_unusable_price_never_orders_a_negative_share_count(self, stub_env, bad_price):
        """``shares = เงิน ÷ ราคา`` — ราคาติดลบ = จำนวนหน่วยติดลบ ซึ่งไม่มีความหมาย."""
        stub_env["prices"] = {**PRICES_FULL, "GLDM": bad_price}
        result = rebalance_service.compute_rebalance(HOLDINGS, "moderate", 350000.0)

        # มีงบ 10,000 USD อยู่ในมือก็ยังต้อง fail closed — งบไม่ได้ทำให้ราคาที่เสียใช้ได้
        assert result["missing_prices"] == ["GLDM"]
        assert all(a["shares"] >= 0 for a in result["actions"])

    @pytest.mark.parametrize("bad_price", UNUSABLE_PRICES)
    def test_calculate_drift_raises_on_an_unusable_price(self, bad_price):
        """drift ที่คิดจากมูลค่า 0 ของ ETF ที่ถืออยู่จริง = ตัวเลขที่ผลิตจากความล้มเหลว."""
        prices = {**PRICES_FULL, "GLDM": bad_price}
        with pytest.raises(ValueError) as exc:
            rebalance_service.calculate_drift(HOLDINGS, TARGET, prices)
        assert "GLDM" in str(exc.value)

    @pytest.mark.parametrize("bad_price", UNUSABLE_PRICES)
    def test_target_only_ticker_with_unusable_price_also_fails_closed(self, stub_env, bad_price):
        """ราคาเสียของ ETF ที่อยู่ในเป้าแต่ยังไม่ถือ ก็ต้องนับเป็น "ขาด" เหมือนกัน.

        ถ้าปล่อยผ่าน งบส่วนของ GLDM จะถูกหารด้วยราคาที่ใช้ไม่ได้ (0 = ระเบิด,
        ติดลบ = จำนวนหน่วยติดลบ) โดยไม่มีอะไรบอกผู้ใช้
        """
        holdings = [h for h in HOLDINGS if h["symbol"] != "GLDM"]
        prices = {**PRICES_FULL, "GLDM": bad_price}
        stub_env["prices"] = dict(prices)

        assert rebalance_service.missing_plan_prices(holdings, TARGET, prices) == ["GLDM"]

        result = rebalance_service.compute_rebalance(holdings, "moderate", 350000.0)
        assert result["missing_prices"] == ["GLDM"]
        assert result["actions"] == []
        assert result["needs_rebalance"] is None

    @pytest.mark.parametrize("bad_price", [0.0, -3.0])
    def test_unusable_price_does_not_pay_for_llm(self, stub_env, bad_price):
        """ไม่มีแผน = ไม่มีอะไรให้ AI อธิบาย ห้ามเผา credit."""
        stub_env["prices"] = {**PRICES_FULL, "GLDM": bad_price}
        result = rebalance_service.compute_rebalance(
            HOLDINGS, "moderate", 0.0, user_initiated=True
        )

        assert stub_env["llm_calls"] == 0
        assert result["ai_comment"] == ""

    def test_require_price_fails_in_thai_instead_of_a_bare_typeerror(self):
        """ชั้นสุดท้ายก่อนคูณ/หาร: ต้องได้ ValueError ไทยที่บอกชื่อ ticker.

        เดิมเขียน ``float(_usable_price(...))`` ตรง ๆ — ถ้าราคาหลุดด่านมาเป็น ``None``
        จะได้ ``TypeError: float() argument ...`` ภาษาอังกฤษที่ไม่บอกว่า ticker ไหนพัง
        """
        assert rebalance_service._require_price("VOO", PRICES_FULL) == 500.0

        for prices in ({"GLDM": 0.0}, {"GLDM": -3.0}, {"GLDM": float("nan")}, {}):
            with pytest.raises(ValueError) as exc:
                rebalance_service._require_price("GLDM", prices)
            assert "GLDM" in str(exc.value)


class TestEmptyPortfolio:
    """พอร์ตว่าง = คำนวณ drift ไม่ได้ (0/0) แต่ไม่ใช่ความล้มเหลวของข้อมูล."""

    def test_empty_portfolio_with_budget_gets_initial_plan_without_fake_drift(self, stub_env):
        result = rebalance_service.compute_rebalance([], "moderate", 350000.0)  # 10,000 USD

        assert result["max_drift_pct"] is None  # เดิมโผล่เป็น 100.0
        assert result["missing_prices"] == []
        assert all(a["action"] == "buy" for a in result["actions"])
        assert _action(result, "VOO")["usd_amount"] == pytest.approx(3500.0, abs=0.01)

    def test_empty_portfolio_without_budget_has_no_plan(self, stub_env):
        result = rebalance_service.compute_rebalance([], "moderate", 0.0)

        assert result["needs_rebalance"] is None
        assert result["actions"] == []
        assert result["detail"]

    def test_unreadable_share_count_is_not_dropped_silently(self, stub_env):
        """จำนวนหน่วยที่อ่านไม่ได้ก็ทำให้ตัวหารเพี้ยนเหมือนราคาที่หายไป."""
        holdings = [*HOLDINGS, {"symbol": "VTI", "shares": None}]
        with pytest.raises(ValueError) as exc:
            rebalance_service.compute_rebalance(holdings, "moderate", 0.0)
        assert "VTI" in str(exc.value)


class TestMultiLotAggregation:
    """C4(ก) หลายล็อตของ ticker เดียวกันต้อง **รวมกัน** ไม่ใช่แถวหลังทับแถวแรก.

    แหล่งความจริงของพอร์ตคือ ``portfolio/tracker.py`` ซึ่ง ``get_portfolio_summary()``
    ทำ ``groupby("ticker").agg(shares=("shares", "sum"))`` — ซื้อ VOO 40 แล้ว VOO 30
    คือถือ 70 หน่วย ``rebalance_service._held_shares()`` ต้องได้ตัวเลขเดียวกัน ไม่งั้น
    แผน rebalance จะคิดจากพอร์ตที่เล็กกว่าความจริงแล้วสั่งซื้อ/ขายผิดจำนวนด้วยเงินจริง
    (เดิม ``values[sym] = shares * price`` = แถวหลังเขียนทับ → ตีเป็น 30 หน่วย)
    """

    def test_tracker_sums_lots_and_held_shares_agrees(self, tmp_path, monkeypatch):
        """ตรึงคู่กัน: ถ้า tracker เปลี่ยนวิธีรวมล็อต เทสต์นี้ต้องแดงพร้อมกันทั้งสองฝั่ง."""
        from portfolio import tracker

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        csv_path = data_dir / "transactions.csv"
        csv_path.write_text(
            "tx_id,date,ticker,shares,price_usd,fx_rate_thb,amount_thb,fee_thb,note,tx_type\n"
            "l1,2026-01-05,VOO,40,500,35,700000,0,ล็อตแรก,buy\n"
            "l2,2026-02-05,VOO,30,520,35,546000,0,ล็อตสอง,buy\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(tracker, "DATA_DIR", data_dir)
        monkeypatch.setattr(tracker, "TRANSACTIONS_FILE", csv_path)
        monkeypatch.setattr(tracker, "_get_latest_prices", lambda tickers: {t: 500.0 for t in tickers})
        monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: FX_RATE)

        summary = tracker.get_portfolio_summary()
        voo = summary.loc[summary["Ticker"] == "VOO"].iloc[0]
        assert float(voo["Shares"]) == pytest.approx(70.0), "tracker คือแหล่งความจริง: ล็อตต้องรวมกัน"

        # แถวดิบ (ยังไม่ groupby) ที่ส่งเข้า rebalance ต้องให้ผลเท่ากับที่ tracker สรุปได้
        raw_rows = [{"symbol": "VOO", "shares": 40.0}, {"symbol": "VOO", "shares": 30.0}]
        assert rebalance_service._held_shares(raw_rows) == {"VOO": pytest.approx(70.0)}

    def test_split_lots_drift_uses_the_summed_position(self, stub_env):
        """หลักฐานเชิงตัวเลข: ล็อตแยกต้องได้ drift เท่ากับพอร์ตที่รวมล็อตแล้ว.

        VOO 40+30 = 70 หน่วย → มูลค่ารวม 106,500 USD → drift สูงสุด (GLDM) ≈ 5.02%
        ถ้าแถวหลังทับแถวแรก (เหลือ VOO 30) ตัวหารจะเหลือ 86,500 → VOO ดูเป็น 17.3%
        เทียบเป้า 35% → drift 17.66% (พิสูจน์แล้วด้วยการรันจริง) คนละแผนกันคนละโลก
        """
        split = [{"symbol": "VOO", "shares": 40.0}, {"symbol": "VOO", "shares": 30.0}]
        split += [h for h in HOLDINGS if h["symbol"] != "VOO"]

        drift = rebalance_service.calculate_drift(split, TARGET, PRICES_FULL)
        assert drift == pytest.approx(16000 / 106500 - 0.10, abs=1e-9)

    def test_split_lots_produce_the_same_plan_as_one_combined_row(self, stub_env):
        split = [{"symbol": "VOO", "shares": 40.0}, {"symbol": "VOO", "shares": 30.0}]
        split += [h for h in HOLDINGS if h["symbol"] != "VOO"]

        assert (
            rebalance_service.compute_rebalance(split, "moderate", 0.0)["actions"]
            == rebalance_service.compute_rebalance(HOLDINGS, "moderate", 0.0)["actions"]
        )

    def test_case_and_whitespace_variants_are_one_position(self, stub_env):
        """tracker normalize เป็น strip+upper ก่อน groupby — ฝั่งนี้ต้องรวมเหมือนกัน."""
        rows = [{"symbol": " voo ", "shares": 40.0}, {"symbol": "VOO", "shares": 30.0}]
        assert rebalance_service._held_shares(rows) == {"VOO": pytest.approx(70.0)}


class TestEmptyPortfolioAiComment:
    """C4(ข) พอร์ตว่าง + งบ + ผู้ใช้กดขอ AI → ต้องได้คำอธิบาย ไม่ใช่สตริงว่าง.

    เดิมเส้นทางนี้ drift = 1.0 (100% ปลอม) จึงเข้าเส้นทางปกติแล้วได้คำอธิบายไปด้วย
    การแก้ FIX_PLAN 1.3 ตัด drift ปลอมออกถูกแล้ว แต่ทำให้ ``compute_rebalance()``
    คืนก่อนถึง ``_generate_ai_comment()`` = คนที่กดปุ่ม "ให้ AI อธิบาย" บนแผนก้อนแรก
    ไม่ได้อะไรกลับมาเลย (ถดถอย)
    """

    def test_initial_plan_gets_an_explanation_when_the_user_asks(self, stub_env):
        result = rebalance_service.compute_rebalance(
            [], "moderate", 350000.0, user_initiated=True
        )

        assert result["actions"], "แผนก้อนแรกต้องมีรายการซื้อ"
        assert stub_env["llm_calls"] == 1
        assert result["ai_comment"] == "คำอธิบายจำลอง"

    def test_initial_plan_prompt_does_not_claim_a_drift_that_cannot_exist(self, stub_env):
        """พอร์ตว่าง = ไม่มีสัดส่วนปัจจุบันให้เทียบ ห้ามป้อนตัวเลขเบี่ยงเบนให้ AI พูดต่อ."""
        rebalance_service.compute_rebalance([], "moderate", 350000.0, user_initiated=True)
        prompt = stub_env["prompts"][0]

        assert "เบี่ยงเบนสูงสุด" not in prompt
        # ตัวหารปลอม (``or 1.0``) จะผลิตบรรทัด "VOO (0.0% vs เป้า 35%)" ทั้งที่ยังไม่ถืออะไรเลย
        assert "vs เป้า" not in prompt
        assert "0.0%" not in prompt
        assert "ก้อนแรก" in prompt
        assert "VOO" in prompt, "ต้องบอก AI ว่าแผนซื้ออะไรบ้าง"

    def test_initial_plan_does_not_pay_when_the_user_did_not_ask(self, stub_env):
        """ไม่ได้กดขอ = ไม่จ่าย แต่ตัวเลขแผน (ซึ่งเป็นข้อมูลที่ใช้ตัดสินใจ) ต้องครบ."""
        result = rebalance_service.compute_rebalance([], "moderate", 350000.0)

        assert stub_env["llm_calls"] == 0
        assert result["ai_comment"] == ""
        assert result["actions"]
        assert result["detail"], "ต้องมีเหตุผลกำกับว่าทำไมไม่มีค่า drift"

    def test_drift_path_still_gets_an_explanation(self, stub_env):
        """กันการแก้ข้อนี้ไปทำเส้นทางเดิมพัง."""
        result = rebalance_service.compute_rebalance(
            HOLDINGS, "moderate", 0.0, user_initiated=True
        )

        assert result["ai_comment"] == "คำอธิบายจำลอง"
        assert "เบี่ยงเบนสูงสุด" in stub_env["prompts"][0]


class TestBudgetAndFxSanity:
    """งบ/อัตราแลกเปลี่ยนที่ "มีค่าแต่ใช้ไม่ได้" ต้องไม่เดินต่อจนกลายเป็นแผนซื้อขาย.

    รูชนิดเดียวกับราคาที่หายไป: ``budget_usd = available_budget_thb / fx_rate``
    ไม่เคยถูกตรวจเลย ทำให้
    - งบ ``nan`` → ``nan <= 0`` เป็น False → ผ่านด่าน "ไม่มีงบ" → ``delta_usd`` เป็น nan
      → ``nan > 0`` เป็น False → ตกไปช่อง ``else`` = **สั่งขายทุกตัวด้วยจำนวน nan**
    - งบ ``inf`` → แผนซื้อจำนวน inf
    - fx = 0 (ค่าสำรองใน config ไม่เคยถูก sanity check) → ZeroDivisionError ดิบ ๆ
    """

    @pytest.mark.parametrize("bad_budget", [float("nan"), float("inf"), float("-inf")])
    def test_unusable_budget_never_becomes_a_plan(self, stub_env, bad_budget):
        with pytest.raises(ValueError) as exc:
            rebalance_service.compute_rebalance(HOLDINGS, "moderate", bad_budget)
        assert "งบ" in str(exc.value)

    def test_nan_budget_does_not_flip_the_whole_portfolio_into_sells(self, stub_env):
        """หลักฐานตรงของบั๊ก: เดิม nan ทำให้ทุก action เป็น ``sell`` จำนวน nan."""
        with pytest.raises(ValueError):
            rebalance_service.compute_rebalance([], "moderate", float("nan"))

    def test_unusable_fx_rate_fails_loudly(self, stub_env, monkeypatch):
        monkeypatch.setattr(rebalance_service, "_get_usdthb_rate", lambda: 0.0)
        with pytest.raises(ValueError) as exc:
            rebalance_service.compute_rebalance(HOLDINGS, "moderate", 35000.0)
        assert "อัตราแลกเปลี่ยน" in str(exc.value)


class TestNoInventedDenominator:
    """C4(ค) ห้ามมีตัวหารสำรองที่กุขึ้นบนเส้นทางมูลค่า."""

    def test_module_has_no_or_fallback_on_the_value_path(self):
        """``total = sum(values.values()) or 1.0`` เป็นสำนวนที่โปรเจกต์ห้ามบนเส้นทางเงิน.

        ต่อให้วันนี้เป็น dead branch (``calculate_drift`` raise ไปก่อนแล้ว) ก็ต้องถอดทิ้ง
        ไม่ใช่รอให้ใครสักคนย้ายโค้ดแล้วมันกลับมามีชีวิตเงียบ ๆ

        ตรวจด้วย AST ไม่ใช่ค้นข้อความ — จะได้ไม่ไปจับคอมเมนต์ที่อธิบายว่า "ห้ามเขียนแบบนี้"
        """
        tree = ast.parse(inspect.getsource(rebalance_service))
        offenders = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.BoolOp)
            and isinstance(node.op, ast.Or)
            and any(
                isinstance(v, ast.Constant)
                and isinstance(v.value, (int, float))
                and not isinstance(v.value, bool)
                for v in node.values
            )
        ]
        assert offenders == [], f"เจอค่าสำรองที่กุขึ้นบนเส้นทางเงิน: {offenders}"

    def test_value_path_functions_never_return_a_bare_number(self):
        """``return 1.0`` ตรง ๆ ก็เป็นค่าที่กุขึ้นเหมือนกัน — ด่านเดิมจับแต่สำนวน ``x or 1.0``.

        AUDIT_ROUND2_2026-08-07 mutation M26: แทรก ``return 1.0`` ในกิ่ง ``total <= 0``
        ของ ``calculate_drift()`` แล้วเทสต์ AST ด้านบนไม่จับ เพราะมันมองหาเฉพาะ
        ``ast.BoolOp``/``Or`` — ที่นี่จึงตรวจอีกสำนวนหนึ่ง: ฟังก์ชันที่ **ผลลัพธ์ของมัน
        เป็นเงิน/สัดส่วน/อัตรา** ห้ามคืนค่าคงที่ตัวเลข ตัวเลขทุกตัวที่ออกจากฟังก์ชัน
        เหล่านี้ต้องคำนวณจากพอร์ต/ราคา/อัตราจริงเท่านั้น

        ``None`` ยังคืนได้ (= "ไม่มีค่า" ซึ่งเป็นคำตอบที่ซื่อสัตย์ คนละเรื่องกับตัวเลขปลอม)
        และ ``True/False`` ก็ไม่ใช่จำนวนเงิน จึงยกเว้นทั้งคู่
        """
        # ฟังก์ชันที่ค่าที่คืนออกไปถูกเอาไปคูณ/หาร/เทียบเกณฑ์เป็นเงินโดยตรง
        value_path = {
            "calculate_drift",
            "_usable_price",
            "_require_price",
            "_usable_budget_thb",
            "_usable_fx_quote",
            "_get_usdthb_rate",
            "_get_fx_quote",
            "_held_shares",
        }

        def _number_literal(node):
            """คืนโหนดตัวเลขคงที่ (รองรับเครื่องหมายหน้าเลข เช่น ``return -1``)."""
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                node = node.operand
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)
            ):
                return node
            return None

        tree = ast.parse(inspect.getsource(rebalance_service))
        offenders = []
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if func.name not in value_path:
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Return) or node.value is None:
                    continue
                # คืนเป็น tuple ก็นับทีละช่อง (เช่น ``return 1.0, True``)
                parts = (
                    node.value.elts if isinstance(node.value, ast.Tuple) else [node.value]
                )
                if any(_number_literal(p) is not None for p in parts):
                    offenders.append(f"{func.name}: {ast.unparse(node)}")

        assert offenders == [], (
            f"ฟังก์ชันบนเส้นทางมูลค่าคืนค่าคงที่ตัวเลข (ตัวเลขที่ไม่ได้มาจากพอร์ตจริง): {offenders}"
        )

    def test_the_return_constant_check_actually_catches_the_old_bug(self):
        """พิสูจน์ว่าด่านด้านบนไม่ใช่ตาข่ายที่มีรูโบ๋ — ป้อนโค้ดที่มีบั๊กเข้าไปต้องจับได้.

        (ตรวจตรรกะเดียวกันบนซอร์สจำลอง ไม่แตะโมดูลจริง)
        """
        mutated = "def calculate_drift(h, t, p):\n    if total <= 0:\n        return 1.0\n    return max_drift\n"
        tree = ast.parse(mutated)
        found = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, (int, float))
            and not isinstance(node.value.value, bool)
        ]
        assert found, "ตรรกะการตรวจต้องจับ ``return 1.0`` ได้"

    def test_empty_portfolio_never_divides_by_a_made_up_total(self, stub_env):
        """เรียกตรง ๆ ที่ฟังก์ชันสร้าง prompt: พอร์ตว่างต้องไม่ผลิตเปอร์เซ็นต์ใด ๆ."""
        comment = rebalance_service._generate_ai_comment(
            "moderate",
            None,  # พอร์ตว่าง = ไม่มี drift ให้รายงาน (ไม่ใช่ 0.0)
            [{"symbol": "VOO", "action": "buy", "shares": 7.0, "usd_amount": 3500.0,
              "thb_amount": 122500.0, "fee_thb": 183.75}],
            TARGET,
            PRICES_FULL,
            [],
            user_initiated=True,
        )

        assert comment == "คำอธิบายจำลอง"
        prompt = stub_env["prompts"][0]
        # สัดส่วน "เป้าหมาย" เป็นตัวเลขจริงที่คำนวณมาแล้ว บอก AI ได้
        # แต่สัดส่วน "ปัจจุบัน" ของพอร์ตที่ยังว่างไม่มีอยู่จริง ห้ามผลิตจากตัวหารปลอม
        assert "vs เป้า" not in prompt
        assert "0.0%" not in prompt


class TestRebalanceRoute:
    """route ต้องตอบ 200 พร้อมเหตุผล — 500 เปล่า ๆ ทำให้ผู้ใช้ไม่รู้ว่าทำไมไม่มีแผน."""

    def _client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.routers import rebalance as router_mod

        app = FastAPI()
        app.include_router(router_mod.router)
        return TestClient(app, raise_server_exceptions=False)

    def test_missing_price_returns_200_with_missing_prices(self, stub_env, monkeypatch):
        stub_env["prices"] = dict(PRICES_NO_GLDM)
        client = self._client(monkeypatch)

        resp = client.post(
            "/api/portfolio/rebalance",
            json={
                "holdings": [{"symbol": h["symbol"], "shares": h["shares"]} for h in HOLDINGS],
                "risk_profile": "moderate",
                "available_budget_thb": 0.0,
            },
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["missing_prices"] == ["GLDM"]
        assert data["actions"] == []
        assert data["needs_rebalance"] is None
        assert data["detail"]

    @pytest.mark.parametrize("bad_price", [0.0, -3.0])
    def test_unusable_price_returns_200_with_a_reason_not_a_bare_500(
        self, stub_env, monkeypatch, bad_price
    ):
        """ราคา 0 เคยระเบิดเป็น ZeroDivisionError = 500 เปล่า ๆ ไม่มี detail ไทย
        ส่วนราคาติดลบเคยตอบ 200 พร้อม **แผนขายทั้งพอร์ต** ซึ่งแย่กว่า 500 อีก
        """
        stub_env["prices"] = {**PRICES_FULL, "GLDM": bad_price}
        client = self._client(monkeypatch)

        resp = client.post(
            "/api/portfolio/rebalance",
            json={
                "holdings": [{"symbol": h["symbol"], "shares": h["shares"]} for h in HOLDINGS],
                "risk_profile": "moderate",
                "available_budget_thb": 0.0,
            },
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["missing_prices"] == ["GLDM"]
        assert data["actions"] == []
        assert data["needs_rebalance"] is None
        assert "GLDM" in data["detail"]
