# -*- coding: utf-8 -*-
"""ตรรกะของ ``ScreenerEngine.run()`` บนข้อมูลสังเคราะห์ — ไม่ยิง network.

ที่มา (AUDIT_2026-08-06 ข้อ 0-D): mutation testing รอบ R9 เปลี่ยน AND เป็น OR ทุกพรีเซ็ต
และเปลี่ยนสเกล ``signal_strength`` จาก ``* 7`` เป็น ``* 10`` แล้วชุดเทสต์ผ่านหมดทั้ง 568/572
เพราะ **``run()`` ไม่เคยถูกเรียกเลยตลอดชุดเทสต์** (``tests/test_screener.py`` เดิมยิง yfinance จริง
และไม่มี assert สักบรรทัด) ⇒ เอนจินที่ส่งสัญญาณเข้า Telegram ทุกเช้าไม่มีตาข่ายเลย

ทุกเคสสตับ ``_fetch_df`` ด้วย DataFrame ที่รู้คำตอบล่วงหน้า
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import pytest

from backend.screener.engine import ScreenerEngine
from backend.screener.models import ScreenerPreset, ScreenerRule

# --- ข้อมูลสังเคราะห์ ---------------------------------------------------------
# ตัวเลข RSI ด้านล่างวัดจาก analysis.ta_compat.ta.rsi ของจริงบนซีรีส์เหล่านี้
# (ระบุไว้เพื่อให้เห็นว่าเคสไหนได้/ไม่ได้โบนัสของ _compute_signal_strength)


def _frame(values: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(values), freq="B")
    return pd.DataFrame(
        {"Close": values, "Volume": [1_000_000.0] * len(values)},
        index=idx,
    )


def uptrend_frame() -> pd.DataFrame:
    """ขาขึ้นแกว่งเบา ๆ — ราคา 153.30 > MA200 132.65 · RSI ≈ 58.5 (ไม่เข้าเงื่อนไขโบนัส)."""
    return _frame([100 + 0.20 * i + (1.5 if i % 2 else 0.0) for i in range(260)])


def downtrend_frame() -> pd.DataFrame:
    """ขาลง — ราคา 146.70 < MA200 167.35 · RSI ≈ 41.5 (ไม่เข้าเงื่อนไขโบนัส)."""
    return _frame([200 - 0.20 * i - (1.5 if i % 2 else 0.0) for i in range(260)])


def dip_frame() -> pd.DataFrame:
    """ขาขึ้นแล้วย่อแรงช่วงท้าย — RSI ≈ 20.5 (ต่ำกว่า 35)."""
    rise = [100 + 0.20 * i + (1.5 if i % 2 else 0.0) for i in range(245)]
    dip = list(np.linspace(rise[-1], rise[-1] * 0.88, 15))
    return _frame(rise + dip)


PASS_RULE = ScreenerRule("price_vs_ma200", "gt", None, "Price above MA200")
FAIL_RULE = ScreenerRule("price_vs_ma200", "lt", None, "Price below MA200")


def _preset(logic: str, rules: list[ScreenerRule], name: str = "unit") -> ScreenerPreset:
    return ScreenerPreset(name=name, rules=rules, logic=logic, description="เทสต์")


@pytest.fixture
def engine(monkeypatch):
    """เอนจินที่ ``_fetch_df`` ถูกสตับ — ตั้งข้อมูลรายสัญลักษณ์ผ่าน ``.frames``."""
    eng = ScreenerEngine()
    frames: dict[str, pd.DataFrame] = {}

    def _fake_fetch(symbol: str) -> pd.DataFrame:
        if symbol not in frames:
            raise ValueError(f"ดึงข้อมูลราคา {symbol} ไม่สำเร็จ (ผลว่าง)")
        return frames[symbol]

    monkeypatch.setattr(eng, "_fetch_df", _fake_fetch)
    eng.frames = frames  # type: ignore[attr-defined]
    return eng


class TestAndOrLogic:
    """AND ต้องไม่ผ่านเมื่อกฎข้อใดข้อหนึ่งไม่ผ่าน — mutation AND→OR รอดมาได้เพราะไม่มีเทสต์นี้."""

    def test_and_fails_when_one_rule_fails(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO"], _preset("AND", [PASS_RULE, FAIL_RULE]))
        assert results == []

    def test_or_passes_when_one_rule_passes(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO"], _preset("OR", [PASS_RULE, FAIL_RULE]))
        assert [r.symbol for r in results] == ["VOO"]
        assert results[0].matched_rules == ["Price above MA200"]

    def test_and_passes_only_when_every_rule_passes(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO"], _preset("AND", [PASS_RULE, PASS_RULE]))
        assert len(results) == 1
        assert results[0].matched_rules == ["Price above MA200", "Price above MA200"]

    def test_or_fails_when_every_rule_fails(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        assert engine.run(["VOO"], _preset("OR", [FAIL_RULE, FAIL_RULE])) == []

    def test_same_rules_different_logic_give_different_answers(self, engine):
        """ถ้า logic ถูกละเลย ผลของ AND กับ OR จะเท่ากัน — เคสนี้บังคับให้ต่างกัน."""
        engine.frames["VOO"] = uptrend_frame()
        rules = [PASS_RULE, FAIL_RULE]
        assert engine.run(["VOO"], _preset("AND", rules)) == []
        assert len(engine.run(["VOO"], _preset("OR", rules))) == 1


class TestSignalStrengthScale:
    """สเกล "ความแรง x/10": base = (ผ่าน/ทั้งหมด) × 7 แล้วบวกโบนัสจาก RSI."""

    def test_all_rules_matched_without_bonus_is_exactly_seven(self, engine):
        """RSI ≈ 58.5 ไม่เข้าโบนัสทั้งสองข้าง ⇒ ค่าต้องเป็น 7.0 พอดี (mutation ×10 → 10.0)."""
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO"], _preset("AND", [PASS_RULE, PASS_RULE]))
        assert results[0].signal_strength == pytest.approx(7.0)

    def test_half_the_rules_matched_is_half_the_base(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO"], _preset("OR", [PASS_RULE, FAIL_RULE]))
        assert results[0].signal_strength == pytest.approx(3.5)

    def test_strength_never_exceeds_ten(self, engine):
        engine.frames["VOO"] = dip_frame()
        results = engine.run(["VOO"], _preset("AND", [FAIL_RULE]))
        assert 0.0 <= results[0].signal_strength <= 10.0

    def test_oversold_scores_above_the_plain_base(self, engine):
        """RSI ≈ 20.5 ต้องได้มากกว่า base เปล่า ๆ (โบนัสฝั่งซื้อ).

        ตรึงเฉพาะ "ทิศทาง" ไม่ตรึงค่าโบนัส เพราะ AUDIT_2026-08-06 ข้อ B6.3 ตั้งคำถาม
        กับเกณฑ์ 35/65 ที่เขียนซ้ำในเอนจินอยู่แล้ว (สายงาน B6 เป็นผู้ตัดสิน)
        """
        engine.frames["DIP"] = dip_frame()
        engine.frames["UP"] = uptrend_frame()
        dip = engine.run(["DIP"], _preset("AND", [FAIL_RULE]))[0]      # ราคาต่ำกว่า MA200 = ผ่าน
        plain = engine.run(["UP"], _preset("AND", [PASS_RULE]))[0]
        assert dip.signal_strength > plain.signal_strength

    def test_results_are_sorted_by_strength_desc(self, engine):
        """ลำดับที่หน้าจอและ notifier พึ่ง — ตัวที่แรงกว่าต้องมาก่อนแม้ส่งเข้าไปทีหลัง."""
        engine.frames["UP"] = uptrend_frame()    # ผ่าน PASS_RULE, RSI ≈ 58.5 (ไม่มีโบนัส)
        engine.frames["DIP"] = dip_frame()       # ผ่าน FAIL_RULE, RSI ≈ 20.5 (มีโบนัส)

        results = engine.run(["UP", "DIP"], _preset("OR", [PASS_RULE, FAIL_RULE]))
        assert [r.symbol for r in results] == ["DIP", "UP"]
        assert results[0].signal_strength > results[1].signal_strength


class TestRuleEvaluation:
    """กฎแต่ละข้อต้องอ่านค่าจากราคาจริง และ "คำนวณไม่ได้" ต้อง raise ไม่ใช่ False."""

    # `bool(...)` เพราะ pandas/numpy คืน numpy.bool_ ไม่ใช่ bool ของ Python
    def test_price_vs_ma200_reads_the_frame(self, engine):
        up, down = uptrend_frame(), downtrend_frame()
        assert bool(engine._evaluate_rule(PASS_RULE, up)) is True
        assert bool(engine._evaluate_rule(FAIL_RULE, up)) is False
        assert bool(engine._evaluate_rule(PASS_RULE, down)) is False
        assert bool(engine._evaluate_rule(FAIL_RULE, down)) is True

    def test_rsi_rule_compares_against_rule_value(self, engine):
        up = uptrend_frame()  # RSI ≈ 58.5
        assert bool(engine._evaluate_rule(ScreenerRule("rsi", "lt", 35, ""), up)) is False
        assert bool(engine._evaluate_rule(ScreenerRule("rsi", "gt", 35, ""), up)) is True
        assert bool(engine._evaluate_rule(ScreenerRule("rsi", "lt", 70, ""), up)) is True
        assert bool(engine._evaluate_rule(ScreenerRule("rsi", "gt", 70, ""), up)) is False

    def test_ma200_not_computable_raises_instead_of_false(self, engine):
        """ข้อมูลไม่ถึง 200 แท่ง = "ตรวจไม่ได้" ห้ามกลายเป็น "ไม่ผ่านกฎ" (C1)."""
        short = _frame([100.0 + i for i in range(50)])
        with pytest.raises(ValueError):
            engine._evaluate_rule(PASS_RULE, short)

    def test_rsi_not_computable_raises_instead_of_false(self, engine):
        short = _frame([100.0 + i for i in range(5)])
        with pytest.raises(ValueError):
            engine._evaluate_rule(ScreenerRule("rsi", "lt", 35, ""), short)


class TestFetchFailure:
    """ดึงข้อมูลไม่ได้ ≠ ไม่มีสัญญาณ."""

    def test_failed_symbol_does_not_appear_as_a_signal(self, engine):
        engine.frames["VOO"] = uptrend_frame()  # SCHD ไม่มีข้อมูล → _fetch_df โยน
        results = engine.run(["VOO", "SCHD"], _preset("AND", [PASS_RULE]))
        assert [r.symbol for r in results] == ["VOO"]

    def test_one_broken_symbol_does_not_abort_the_run(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        engine.frames["QQQM"] = uptrend_frame()
        results = engine.run(["BROKEN", "VOO", "QQQM"], _preset("AND", [PASS_RULE]))
        assert sorted(r.symbol for r in results) == ["QQQM", "VOO"]

    # เดิม xfail: run() กลืน exception ไว้ใน logger.error เท่านั้น ไม่มีช่องรายงาน
    # สัญลักษณ์ที่ดึงไม่ได้ออกไปให้ผู้เรียก — สายงาน B6 เพิ่ม ``ScreenerRunResults.errors``
    # ให้แล้ว (AUDIT_2026-08-06 ข้อ 0-D ข้อ 3) จึงถอด marker ออกเป็นเทสต์จริง
    def test_failed_symbol_is_reported_to_the_caller(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        results = engine.run(["VOO", "SCHD"], _preset("AND", [PASS_RULE]))
        errors = getattr(results, "errors", None) or getattr(engine, "errors", None)
        assert errors, "ต้องมีช่อง errors ให้ผู้เรียกเห็นว่า SCHD ตรวจไม่ได้"
        assert any("SCHD" in str(item) for item in errors)


class TestUnknownDefinitions:
    """นิยามที่เอนจินไม่รู้จักต้องดังทันที ห้ามเงียบเป็น "ไม่ผ่านกฎ"."""

    @pytest.mark.xfail(
        strict=False,
        reason="AUDIT_2026-08-06 ข้อ 0-D: _evaluate_rule คืน False ให้ field ที่ไม่รู้จัก "
        "⇒ พรีเซ็ต AND ที่พิมพ์ชื่อ field ผิดจะ 'ไม่มีสัญญาณ' ตลอดกาลอย่างเงียบ ๆ",
    )
    def test_unknown_field_raises(self, engine):
        with pytest.raises(ValueError):
            engine._evaluate_rule(ScreenerRule("ไม่มีฟิลด์นี้", "gt", 1.0, ""), uptrend_frame())

    @pytest.mark.xfail(
        strict=False,
        reason="AUDIT_2026-08-06 ข้อ 0-D: operator ที่ไม่รู้จักตกลงมาที่ `return False` เช่นกัน",
    )
    def test_unknown_operator_raises(self, engine):
        with pytest.raises(ValueError):
            engine._evaluate_rule(ScreenerRule("rsi", "ไม่มีตัวดำเนินการนี้", 35, ""), uptrend_frame())

    @pytest.mark.xfail(
        strict=False,
        reason="AUDIT_2026-08-06 ข้อ 0-D: logic ที่ไม่ใช่ 'AND' ถูกตีความเป็น OR เงียบ ๆ "
        "(สะกดผิดครั้งเดียว = พรีเซ็ตเปลี่ยนความหมายทั้งใบ)",
    )
    def test_unknown_logic_raises(self, engine):
        engine.frames["VOO"] = uptrend_frame()
        with pytest.raises(ValueError):
            engine.run(["VOO"], _preset("XOR", [PASS_RULE, FAIL_RULE]))


class TestShippedPresets:
    """พรีเซ็ตที่ใช้งานจริงต้องยังเป็น AND — การเปลี่ยนเป็น OR คือ mutation ที่รอดมา."""

    def test_all_shipped_presets_use_and(self):
        from backend.screener.presets import PRESETS

        assert {p.logic for p in PRESETS.values()} == {"AND"}

    def test_oversold_momentum_requires_all_three_conditions(self, engine):
        """ขาขึ้น + ไม่ oversold ⇒ พรีเซ็ต oversold_momentum ต้องไม่ติด."""
        from backend.screener.presets import get_preset

        engine.frames["VOO"] = uptrend_frame()  # RSI ≈ 58.5 → กฎ RSI < 35 ไม่ผ่าน
        assert engine.run(["VOO"], get_preset("oversold_momentum")) == []
