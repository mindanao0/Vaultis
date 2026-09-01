# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 (T5 · M09) — เพดาน RSI ของป้าย ``strong_buy`` ไม่มีเทสต์ตรึง

อาการที่วัดได้ก่อนแก้: เลื่อน ``float(rsi) < 65`` ใน ``overall_signal()`` เป็น ``< 75``
แล้วชุดเทสต์ทั้งชุดยังเขียว 1297 passed ทั้งที่พฤติกรรมเปลี่ยนจริง::

    BASE   : overall_signal(BULLISH, golden_cross=True, rsi=70) -> 'buy'
    MUTANT : overall_signal(BULLISH, golden_cross=True, rsi=70) -> 'strong_buy'

RSI 70 คือ ``RSI_OVERBOUGHT`` เส้น overbought กลางของทั้งระบบ การที่ราคาที่ร้อนเกิน
เส้นนั้นได้ป้าย **strong_buy** ขัดกับนโยบายที่เขียนไว้หัวไฟล์ ``signal_rules.py`` เอง
("Overbought = ระวังไล่ราคา ไม่ใช่คำสั่งซื้อ") ป้ายนี้ออกหน้า ETF analysis และเข้า
prompt ของ AI จริง จึงกระทบพฤติกรรมการซื้อของผู้ใช้โดยตรง

boundary อื่น ๆ ในไฟล์เดียวกันมีเทสต์ครบแล้ว (``tests/test_signal_rules_boundary.py``
ตรึง ``p >= MA200`` และ ``r < RSI_OVERSOLD``) เหลือเลขนี้ตัวเดียวที่ไม่มีใครดู
ไฟล์นี้จึงตรึง **สามอย่าง**: ค่าคงที่, เส้นแบ่งของ ``overall_signal()`` และ
ความสัมพันธ์ ``STRONG_BUY_RSI_CEILING < RSI_OVERBOUGHT`` (ไม่งั้น "ร้อนเกิน" กับ
"ซื้อแรง" ทับกันได้อีก)

ไม่ยิง network ไม่แตะไฟล์ผู้ใช้ — เป็นฟังก์ชันล้วน
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from technical import signal_rules as sr


class TestStrongBuyRsiCeilingConstant:
    """เลข 65 ต้องเป็นค่าคงที่ที่มีชื่อ และต้องอยู่ใต้เส้น overbought เสมอ."""

    def test_ceiling_value_is_pinned(self):
        assert sr.STRONG_BUY_RSI_CEILING == 65.0

    def test_ceiling_stays_below_the_system_wide_overbought_line(self):
        """ถ้าเพดานนี้ ≥ RSI_OVERBOUGHT จะมี RSI ที่เป็นทั้ง 'ร้อนเกิน' และ 'ซื้อแรง' พร้อมกัน."""
        assert sr.STRONG_BUY_RSI_CEILING < sr.RSI_OVERBOUGHT

    def test_no_bare_65_left_in_overall_signal(self):
        """กันการเผลอเขียนเลขลอยกลับเข้าไปอีก — single source of truth ต้องไม่มีเลขลอย."""
        import inspect

        source = inspect.getsource(sr.overall_signal)
        assert "STRONG_BUY_RSI_CEILING" in source
        assert "< 65" not in source


class TestStrongBuyRsiBoundary:
    """เส้นแบ่งจริงของป้าย: ต่ำกว่าเพดาน = strong_buy, ตั้งแต่เพดานขึ้นไป = buy."""

    @pytest.mark.parametrize(
        "rsi, expected",
        [
            (30.0, "strong_buy"),   # เย็นสบาย
            (64.9, "strong_buy"),   # ใต้เพดานเฉียดฉิว
            (65.0, "buy"),          # ที่เพดานพอดี — เทียบ `<` ไม่ใช่ `<=`
            (65.1, "buy"),
            (69.9, "buy"),
            (70.0, "buy"),          # = RSI_OVERBOUGHT: mutation 65→75 ทำให้กลายเป็น strong_buy
            (74.9, "buy"),          # ยังต้องเป็น buy แม้ mutant จะยกเพดานไปถึง 75
            (85.0, "buy"),
        ],
    )
    def test_ceiling_decides_strong_buy_vs_buy(self, rsi, expected):
        assert sr.overall_signal(sr.BULLISH, golden_cross=True, rsi=rsi) == expected

    def test_accumulate_uses_the_same_ceiling(self):
        """ACCUMULATE เดินเส้นทางเดียวกับ BULLISH — เพดานต้องเป็นตัวเดียวกัน."""
        assert sr.overall_signal(sr.ACCUMULATE, golden_cross=True, rsi=64.9) == "strong_buy"
        assert sr.overall_signal(sr.ACCUMULATE, golden_cross=True, rsi=70.0) == "buy"

    def test_overbought_reading_never_becomes_strong_buy(self):
        """ตรึงนโยบายหัวไฟล์: RSI เหนือเส้น overbought ห้ามได้ป้าย 'ซื้อแรง' ไม่ว่าทางไหน."""
        hot = sr.RSI_OVERBOUGHT + 0.1
        for central in (sr.BULLISH, sr.ACCUMULATE, sr.OVERBOUGHT_CAUTION):
            assert (
                sr.overall_signal(central, golden_cross=True, rsi=hot) != "strong_buy"
            ), central


class TestStrongBuyNeedsBothConditions:
    """เพดาน RSI เป็นเงื่อนไข **เพิ่ม** ไม่ใช่ตัวแทน golden cross — และ 'ไม่รู้ RSI' ≠ 'เย็นพอ'."""

    def test_no_golden_cross_is_only_buy(self):
        assert sr.overall_signal(sr.BULLISH, golden_cross=False, rsi=40.0) == "buy"

    @pytest.mark.parametrize("rsi", [None, float("nan")])
    def test_unknown_rsi_is_not_treated_as_cool_enough(self, rsi):
        """RSI ที่คำนวณไม่ได้ต้องตกเป็น buy ไม่ใช่เดาว่าต่ำกว่าเพดาน (C1 — ห้ามกุ)."""
        assert sr.overall_signal(sr.BULLISH, golden_cross=True, rsi=rsi) == "buy"
