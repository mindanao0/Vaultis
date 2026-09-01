# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 (T5 · M12 + M16) — ค่าคงที่บนเส้นทางเงินที่ไม่มีเทสต์ตรึง

ตาข่ายเดิมจับ "ค่าคงที่ถูกแก้" ได้ (เปลี่ยน ``TILT_MIN``/``TILT_MAX`` แล้วแดง 3 ตัว)
แต่ **ไม่จับ "guard ที่บังคับใช้ค่าคงที่นั้นหายไป"** สองจุดนี้จึงเลื่อนได้เงียบ ๆ:

M12 — ``analysis/financial_model._score_tilt()``::

    เปลี่ยน `clamped = max(0.0, min(100.0, total_pct))` เป็น `clamped = total_pct`
    → ชุดเทสต์เต็มยังเขียว 1297 passed
    BASE  : _score_tilt(150) = 1.4   · _score_tilt(-40) = 0.6
    MUTANT: _score_tilt(150) = 1.8   · _score_tilt(-40) = 0.28  ← เกือบไม่ซื้อ

กรอบ ``TILT_MIN``–``TILT_MAX`` (0.6–1.4) คือ **นโยบายจัดสรร DCA ที่ CLAUDE.md ประกาศไว้**
("target weight เป็นฐาน คะแนนแค่เอียงน้ำหนัก · ทุก ETF ที่มีข้อมูลได้เงินทุกเดือน")
tilt 0.28 = market timing ที่นโยบายห้าม ``calculate_allocation()`` รับ dict คะแนนจาก
ผู้เรียกภายนอกได้ (``ai_advisor.py``, ``dashboard/app.py``) ด่าน clamp จึงต้องอยู่ต่อไป

M16 — ``analysis/financial_model.score_from_prices()``::

    เปลี่ยน `if len(closes) < 200:` เป็น `< 100` → ชุดเทสต์เต็มยังเขียว
    BASE  : 150 แท่ง -> ValueError "ข้อมูลราคาน้อยกว่า 200 วันเทรด"
    MUTANT: 150 แท่ง -> ValueError "คำนวณตัวชี้วัด MA/RSI ไม่ได้"

ทั้งคู่ยัง fail loud แต่ **สาเหตุที่ผู้ใช้เห็นเปลี่ยน** จาก "ข้อมูลย้อนหลังไม่พอ"
(รอ/ขยายช่วงดึงแล้วหาย) เป็น "คำนวณตัวชี้วัดไม่ได้" (ฟังเหมือนระบบพัง) ข้อความนี้ถูก
ส่งต่อเป็น ``_no_data(ticker, str(exc))`` ขึ้นหน้าจอและเข้า prompt ของ AI จริง —
"บอกสาเหตุผิด" อยู่ในตระกูลเดียวกับกฎ "ห้ามกุ" ของโปรเจกต์

ไม่ยิง network — ทุกเทสต์ป้อนอนุกรมราคาที่สร้างเอง
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import pytest

from analysis.financial_model import (
    TILT_MAX,
    TILT_MIN,
    _score_tilt,
    calculate_allocation,
    score_from_prices,
)


def _closes(n: int) -> pd.Series:
    """อนุกรมราคาปิด ``n`` แท่ง ที่ไต่ขึ้นช้า ๆ พร้อมคลื่นเล็ก ๆ (RSI คำนวณได้จริง)."""
    index = pd.bdate_range("2020-01-01", periods=n)
    values = 100.0 + np.arange(n) * 0.1 + np.sin(np.arange(n) / 7.0) * 1.5
    return pd.Series(values, index=index)


class TestScoreTiltStaysInsideThePolicyBand:
    """M12 — clamp 0–100 คือสิ่งที่บังคับใช้กรอบ 0.6–1.4 ถอดออกแล้วต้องแดง."""

    @pytest.mark.parametrize(
        "total_pct",
        [-1e9, -100.0, -40.0, -0.1, 0.0, 50.0, 100.0, 100.1, 150.0, 1e9],
    )
    def test_tilt_never_leaves_the_band(self, total_pct):
        tilt = _score_tilt(total_pct)
        assert TILT_MIN <= tilt <= TILT_MAX, f"คะแนน {total_pct} ให้ tilt {tilt} หลุดกรอบนโยบาย"

    @pytest.mark.parametrize(
        "total_pct, expected",
        [
            (-40.0, TILT_MIN),   # ต่ำกว่า 0 ต้องถูกยกขึ้นเป็น 0 ก่อน
            (0.0, TILT_MIN),
            (50.0, 1.0),         # คะแนนกลาง = ไม่เอียง
            (100.0, TILT_MAX),
            (150.0, TILT_MAX),   # เกิน 100 ต้องถูกกดลงเป็น 100 ก่อน
        ],
    )
    def test_clamped_ends_map_to_the_band_edges(self, total_pct, expected):
        assert _score_tilt(total_pct) == pytest.approx(expected, abs=1e-9)

    def test_out_of_range_score_cannot_starve_an_etf_of_money(self):
        """ปลายทางจริง: คะแนนนอกกรอบต้องไม่ทำให้กองใดเกือบไม่ได้เงิน (นโยบาย DCA)."""
        scores = {
            "AAA": {"data_ok": True, "total_pct": 150.0},
            "BBB": {"data_ok": True, "total_pct": 50.0},
        }
        targets = {"AAA": 0.5, "BBB": 0.5}

        allocation = calculate_allocation(scores, 10000.0, target_weights=targets)

        assert set(allocation) == {"AAA", "BBB"}
        assert allocation["AAA"]["tilt"] <= TILT_MAX
        assert allocation["BBB"]["tilt"] >= TILT_MIN
        # 1.4 : 1.0 ⇒ 5800 / 4200 (ปัดหลักร้อย) — ไม่ใช่ 6400 / 3600 ของ mutant
        assert allocation["AAA"]["amount_thb"] == 5800
        assert allocation["BBB"]["amount_thb"] == 4200


class TestUnknownScoreIsNotAWeight:
    """``NaN``/``None`` = "ไม่รู้คะแนน" ไม่ใช่คะแนน — ห้ามไหลต่อเป็นน้ำหนักเงินจริง.

    ``max(0.0, min(100.0, nan))`` คืน ``nan`` เงียบ ๆ (เพราะการเปรียบเทียบกับ NaN
    เป็นเท็จทุกทาง) แล้ว ``base * nan`` ก็เป็น ``nan`` ต่อ — เป็นรูรั่วแบบเดียวกับ
    ``fillna(0)`` บนเส้นทางราคาที่กฎข้อ 2 ของโปรเจกต์ห้ามไว้
    """

    @pytest.mark.parametrize("bad", [float("nan"), np.nan])
    def test_nan_score_raises_instead_of_returning_nan(self, bad):
        with pytest.raises(ValueError) as exc:
            _score_tilt(bad)
        assert "NaN" in str(exc.value)

    @pytest.mark.parametrize("bad", [None, "ไม่มีข้อมูล", object(), [60.0]])
    def test_non_numeric_score_raises_with_the_offending_value(self, bad):
        """ค่าที่ไม่ใช่ตัวเลขต้องตายพร้อมของกลาง ไม่ใช่ ``TypeError`` ดิบของ ``float()``."""
        with pytest.raises(ValueError) as exc:
            _score_tilt(bad)
        assert "คะแนน" in str(exc.value)

    def test_tilt_result_is_never_nan_for_any_finite_input(self):
        for total_pct in (-1e9, -1.0, 0.0, 33.3, 100.0, 1e9):
            assert not math.isnan(_score_tilt(total_pct))

    def test_scores_that_cannot_be_computed_are_filtered_before_allocation(self):
        """ตาข่ายฝั่งผู้เรียก: total_pct=NaN ต้องถูกคัดเป็น "ไม่มีข้อมูล" ไม่ใช่ระเบิดกลางแผน."""
        scores = {
            "AAA": {"data_ok": True, "total_pct": 60.0},
            "BBB": {"data_ok": True, "total_pct": float("nan")},
        }
        targets = {"AAA": 0.5, "BBB": 0.5}

        allocation = calculate_allocation(scores, 5000.0, target_weights=targets)

        assert set(allocation) == {"AAA"}
        assert allocation["AAA"]["amount_thb"] == 5000


class TestMinimumHistoryIsTwoHundredBars:
    """M16 — เกณฑ์ 200 วันเทรด: ตรึงทั้งตัวเลขและ **ข้อความสาเหตุ** ที่ผู้ใช้จะเห็น."""

    def test_one_bar_short_says_history_is_too_short(self):
        with pytest.raises(ValueError) as exc:
            score_from_prices("XXX", _closes(199))

        message = str(exc.value)
        assert "200" in message, message
        assert "น้อยกว่า 200 วันเทรด" in message, message
        assert "MA/RSI" not in message, (
            "สาเหตุจริงคือ 'ข้อมูลย้อนหลังไม่พอ' (รอแล้วหาย) ไม่ใช่ 'คำนวณตัวชี้วัดไม่ได้' "
            "ซึ่งฟังเหมือนระบบพัง — ข้อความนี้ขึ้นหน้าจอและเข้า prompt ของ AI จริง"
        )

    @pytest.mark.parametrize("n", [0, 1, 100, 150, 199])
    def test_anything_below_the_threshold_names_the_same_cause(self, n):
        with pytest.raises(ValueError) as exc:
            score_from_prices("XXX", _closes(n))
        assert "200" in str(exc.value)

    def test_exactly_two_hundred_bars_is_enough_to_score(self):
        """ขอบอีกด้าน: 200 แท่งพอดีต้องคำนวณผ่าน ไม่ใช่ปฏิเสธเผื่อเหนียว."""
        result = score_from_prices("XXX", _closes(200))

        assert result["data_ok"] is True
        assert isinstance(result["total_pct"], float)
        assert 0.0 <= result["total_pct"] <= 100.0
        assert result["ma200"] is not None

    def test_scores_produced_by_the_model_always_land_inside_the_clamp(self):
        """เหตุผลที่ M12 severity ต่ำ "วันนี้": คะแนนจากโมเดลอยู่ใน [0,100] เสมอ — ตรึงไว้ด้วย."""
        for n in (200, 260, 400):
            total_pct = score_from_prices("XXX", _closes(n))["total_pct"]
            assert 0.0 <= total_pct <= 100.0
            assert TILT_MIN <= _score_tilt(total_pct) <= TILT_MAX
