# -*- coding: utf-8 -*-
"""เส้นแบ่ง RSI และป้ายคะแนน — ตรึงค่าคงที่ที่ mutation testing เลื่อนได้โดยไม่มีใครจับ.

ที่มา (AUDIT_2026-08-06 ข้อ 0-D): รอบ R9 เลื่อน ``RSI_OVERSOLD 30 → 40`` และ
``RSI_OVERBOUGHT 70 → 60`` แล้วชุดเทสต์ 568 ตัว **ผ่านหมด** เพราะเทสต์เดิมใช้แต่ค่ากลาง ๆ
(28 / 50 / 75 / 78) ซึ่งอยู่ฝั่งเดียวกันของเส้นทั้งก่อนและหลังการเลื่อน

ทำไมเรื่องนี้แตะเงินจริง: ``financial_model._timing_score()`` อ่านโซนจาก
``signal_rules.rsi_zone()`` → คะแนน 0/10/20/30 จาก 100 → ``calculate_allocation()``
แปลงคะแนนเป็น tilt 0.6–1.4 เท่าของน้ำหนักเป้าหมาย ⇒ เลื่อนเส้น 10 จุด = เงิน DCA
เปลี่ยนทุกเดือน · เช่นเดียวกับ ``_signal_label`` ที่เป็นข้อสรุปบรรทัดแรกของทุกหน้าจอ

ไม่ยิง network ทั้งไฟล์
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from analysis.financial_model import _signal_label, _timing_score
from technical import signal_rules


class TestConstantsArePinned:
    """ค่าคงที่ต้องถูก assert ตรง ๆ — ไม่งั้นการเลื่อนเส้นไม่มีอะไรจับได้เลย."""

    def test_oversold_threshold(self):
        assert signal_rules.RSI_OVERSOLD == 30.0

    def test_overbought_threshold(self):
        assert signal_rules.RSI_OVERBOUGHT == 70.0

    def test_thresholds_are_ordered(self):
        assert signal_rules.RSI_OVERSOLD < signal_rules.RSI_OVERBOUGHT


class TestRsiZoneBoundary:
    """เส้นแบ่งเป็นแบบ "เข้ม": < 30 = oversold, > 70 = overbought, ที่เส้นพอดี = neutral."""

    @pytest.mark.parametrize(
        "rsi,expected",
        [
            (29.9, "oversold"),
            (30.0, "neutral"),   # เท่ากับเส้นพอดี ยังไม่ใช่ oversold (ใช้ < ไม่ใช่ <=)
            (30.1, "neutral"),
            (69.9, "neutral"),
            (70.0, "neutral"),   # เท่ากับเส้นพอดี ยังไม่ใช่ overbought (ใช้ > ไม่ใช่ >=)
            (70.1, "overbought"),
        ],
    )
    def test_zone_at_boundary(self, rsi, expected):
        assert signal_rules.rsi_zone(rsi) == expected


class TestDcaSignalBoundary:
    """สัญญาณกลางต้องพลิกที่เส้นเดียวกับ ``rsi_zone`` ทั้งขาขึ้นและขาลง."""

    UP = dict(price=110.0, ma50=105.0, ma200=100.0)
    DOWN = dict(price=90.0, ma50=95.0, ma200=100.0)

    @pytest.mark.parametrize(
        "rsi,expected",
        [
            (29.9, signal_rules.ACCUMULATE),
            (30.0, signal_rules.BULLISH),
            (69.9, signal_rules.BULLISH),
            (70.0, signal_rules.BULLISH),
            (70.1, signal_rules.OVERBOUGHT_CAUTION),
        ],
    )
    def test_uptrend_boundary(self, rsi, expected):
        assert signal_rules.dca_signal(rsi=rsi, **self.UP) == expected

    @pytest.mark.parametrize(
        "rsi,expected",
        [
            (29.9, signal_rules.DOWNTREND_WATCH),
            (30.0, signal_rules.DOWNTREND),
            (70.0, signal_rules.DOWNTREND),
            (70.1, signal_rules.OVERBOUGHT_CAUTION),
        ],
    )
    def test_downtrend_boundary(self, rsi, expected):
        assert signal_rules.dca_signal(rsi=rsi, **self.DOWN) == expected

    def test_uptrend_boundary_is_ma200_not_ma50(self):
        """ราคาเท่ากับ MA200 พอดี = ยังนับเป็นขาขึ้น (>= ไม่ใช่ >)."""
        assert signal_rules.dca_signal(price=100.0, ma50=105.0, ma200=100.0, rsi=25.0) == signal_rules.ACCUMULATE
        assert signal_rules.dca_signal(price=99.9, ma50=105.0, ma200=100.0, rsi=25.0) == signal_rules.DOWNTREND_WATCH


class TestTimingScoreBoundary:
    """เส้น RSI เดียวกันแปลงเป็นคะแนน 0–30 ที่ไหลเข้าการจัดสรรเงิน DCA."""

    @pytest.mark.parametrize(
        "price,ma200,rsi,expected",
        [
            (110.0, 100.0, 29.9, 30),   # ย่อในขาขึ้น = จังหวะสะสม คะแนนเต็ม
            (110.0, 100.0, 30.0, 20),   # พ้นเส้น oversold แล้ว แต่ยัง < 50
            (110.0, 100.0, 49.9, 20),
            (110.0, 100.0, 50.0, 10),
            (110.0, 100.0, 70.0, 10),
            (110.0, 100.0, 70.1, 0),    # overbought = ไม่ให้คะแนนจังหวะ
            (90.0, 100.0, 29.9, 10),    # ย่อในขาลง = ยังไม่ใช่จังหวะ
            (90.0, 100.0, 30.0, 20),
        ],
    )
    def test_timing_score_at_boundary(self, price, ma200, rsi, expected):
        assert _timing_score(price=price, ma200=ma200, rsi=rsi) == expected

    def test_score_range_is_0_to_30(self):
        for rsi in (0.0, 29.9, 30.0, 50.0, 70.0, 100.0):
            for price, ma200 in ((110.0, 100.0), (90.0, 100.0)):
                assert 0 <= _timing_score(price=price, ma200=ma200, rsi=rsi) <= 30


class TestSignalLabelBoundary:
    """ป้าย Strong Buy / Buy / Neutral / Caution / Avoid ที่เส้น 70 · 55 · 40 · 25."""

    @pytest.mark.parametrize(
        "total_pct,expected",
        [
            (100.0, "Strong Buy"),
            (70.1, "Strong Buy"),
            (70.0, "Strong Buy"),   # เส้นเป็นแบบรวม (>=)
            (69.9, "Buy"),
            (55.1, "Buy"),
            (55.0, "Buy"),
            (54.9, "Neutral"),
            (40.1, "Neutral"),
            (40.0, "Neutral"),
            (39.9, "Caution"),
            (25.1, "Caution"),
            (25.0, "Caution"),
            (24.9, "Avoid"),
            (0.0, "Avoid"),
        ],
    )
    def test_label_at_boundary(self, total_pct, expected):
        assert _signal_label(total_pct) == expected

    def test_label_is_monotonic(self):
        """คะแนนสูงขึ้นห้ามได้ป้ายที่แย่ลง."""
        order = ["Avoid", "Caution", "Neutral", "Buy", "Strong Buy"]
        ranks = [order.index(_signal_label(pct)) for pct in range(0, 101, 5)]
        assert ranks == sorted(ranks)
