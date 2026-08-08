# -*- coding: utf-8 -*-
"""AUDIT_ROUND2_2026-08-07 — "กองที่หายไปจากพอร์ต" ต้องมีชื่อและเหตุผลเสมอ

ไฟล์นี้ตรึงสามอาการที่มีรากเดียวกัน: **ชั้นคำนวณตัดกองออกจากพอร์ตแล้วไม่บอกใคร**
สิ่งที่ผู้ใช้เห็นจึงเป็นผลของพอร์ตอื่นที่ถูกนำเสนอเป็นคำตอบของพอร์ตตัวเอง
("ตัดข้อมูลทิ้งเงียบ ผิดพอกับกุตัวเลข" — กฎข้อ 2 ของโปรเจกต์)

* **G1** — "ดึงราคาไม่สำเร็จ" ถูกรายงานเป็น "คอนฟิกผิด"
* **T7** — ETF ที่เป้าหมายเป็น 0% หายจากแผน DCA เงียบ ๆ
* **T6** — กองที่ถือน้ำหนักอยู่แต่ไม่มีราคา ถูกตัดออกจากการจำลอง DCA แล้ว
  normalize น้ำหนักที่เหลือใหม่

--------------------------------------------------------------------------
G1 — "ดึงราคาไม่สำเร็จ" ถูกรายงานเป็น "คอนฟิกผิด"

อาการที่วัดได้ก่อนแก้ (config ถูกต้องทุกอย่าง: VOO 50 / SCHD 50 / อีกสามตัวตั้งใจตั้ง 0)::

    VOO+SCHD data_ok=False (yfinance ล่ม)
    → calculate_allocation() โยน InvalidTargetWeights
      "portfolio.target_weights ตั้งเป็น 0 ทุก ticker — ... ลบคีย์ที่ไม่ต้องการออก"

ทั้งที่ไม่มีอะไรผิดใน config.json เลย และถ้าผู้ใช้ทำตามข้อความ (ลบคีย์ที่ตั้ง 0)
= ลบเจตนา "ไม่ถือทอง/ไม่ถือ QQQM" ทิ้ง เพราะ yfinance ล่มชั่วคราว

เส้นแบ่งที่ไฟล์นี้ตรึงไว้:
- :class:`InvalidTargetWeights` = **คอนฟิกผิดจริง ๆ** เท่านั้น (แก้ที่ config.json ได้ผล)
- :class:`NoTargetForSubset`    = คอนฟิกถูก แต่ ticker ที่ถือน้ำหนัก **ไม่ได้อยู่ในรอบนี้**
  (เช่น ดึงราคาไม่สำเร็จ) — รอบหน้าที่ข้อมูลครบจะจัดสรรได้ตามปกติ
- ทั้งคู่สืบจาก :class:`TargetWeightsError` สำหรับผู้เรียกที่ไม่ต้องแยกสาเหตุ

--------------------------------------------------------------------------
T7 — ETF ที่เป้าหมายถูกตั้งเป็น 0% หายจากแผน DCA เงียบ ๆ

อาการที่วัดได้ก่อนแก้ (ติดตาม 5 กอง ตั้ง ``target_weights`` ครบ 100% ให้ 3 กอง)::

    [targets] notes = ['น้ำหนักที่ตั้งไว้ใช้ครบ 100% แล้ว — XLV, GLDM จึงได้ 0% ...']
    [calculate_allocation] ได้เงิน   : {'VOO': 2000, 'SCHD': 1500, 'QQQM': 1500}
    [calculate_allocation] หายไป     : ['XLV', 'GLDM']
    [calculate_allocation] ช่องเหตุผล: ไม่มีเลย

``targets.py`` **รู้เหตุผลอยู่แล้ว** และเขียนเป็นภาษาไทยไว้ให้ แต่ ``get_target_weights()``
ทิ้ง ``notes`` แล้ว ``if base <= 0: continue`` ก็ตัด ticker ทิ้งโดยไม่เหลือร่องรอย
ปลายทางบนหน้า Scorecard จึงพิมพ์คำโปรยยืนยันตรงกันข้ามว่า "ไม่มีการตัดตัวไหนออก"

เส้นแบ่งที่ตรึงไว้: ticker ที่ไม่ได้เงินต้องมีชื่ออยู่ใน ``AllocationPlan.excluded``
เสมอ พร้อมเหตุผลที่เครื่องอ่านได้ และสามเหตุผลนี้ **ห้ามยุบรวมกัน**

* ``no_data``         ดึงราคา/คะแนนไม่สำเร็จรอบนี้ — ไม่ใช่คำตัดสินว่าไม่น่าซื้อ
* ``zero_target``     ตั้งใจไม่ถือ (เป้าหมาย 0%) — ไม่ใช่ข้อมูลขาด
* ``rounded_to_zero`` งบไม่พอปัดเป็นก้อนละ 100 บาท — เพิ่มงบแล้วได้

--------------------------------------------------------------------------
T6 — การจำลอง DCA ตัดกองออกจากพอร์ตแล้วปั้นน้ำหนักที่เหลือให้รวมเป็น 100%

``simulate_monthly_dca()`` คัดเฉพาะ ticker ที่มีคอลัมน์ราคา (``valid_assets``)
แล้ว ``_normalize_weights()`` ก็ normalize ที่เหลือ ⇒ เส้นมูลค่าที่ได้เป็นของ
**พอร์ตอื่น** ผลลัพธ์จึงต้องพกรายชื่อกองที่ไม่ได้ถูกจำลองติดไปด้วยเสมอ และ
``describe_coverage()`` ต้องแปลเป็นคำเตือนไทยให้ทั้งหน้าจอและ API ใช้ร่วมกัน

ไม่ยิง network และไม่แตะ ``config.json`` จริง (ทุกเทสต์เขียน config ชั่วคราวใน tmp_path)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import pytest

from analysis.financial_model import (
    EXCLUDED_NO_DATA,
    EXCLUDED_ROUNDED_TO_ZERO,
    EXCLUDED_ZERO_TARGET,
    calculate_allocation,
    calculate_allocation_with_status,
)
from portfolio.dca import (
    COVERAGE_ATTR,
    NO_PRICE_KEY,
    ZERO_WEIGHT_KEY,
    describe_coverage,
    simulate_monthly_dca,
)
from portfolio.targets import (
    InvalidTargetWeights,
    NoTargetForSubset,
    TargetWeightsError,
    get_target_weights,
    get_target_weights_with_status,
)

FIVE = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
# config ที่ "ถูกต้องทุกอย่าง": ถือแค่ VOO+SCHD ตั้งใจไม่ถืออีกสามตัว
HALF_HALF = {"VOO": 0.5, "SCHD": 0.5, "QQQM": 0, "XLV": 0, "GLDM": 0}


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """เขียน config.json ชั่วคราวแล้วชี้ ``utils.config`` มาที่ไฟล์นั้น (ห้ามแตะของจริง)."""

    from utils import config as cfg

    counter = {"n": 0}

    def _write(target_weights, tickers=None, profile="moderate"):
        counter["n"] += 1
        path = tmp_path / f"config_{counter['n']}.json"
        path.write_text(
            json.dumps(
                {
                    "etf": {"tickers": list(tickers or FIVE)},
                    "portfolio": {"risk_profile": profile, "target_weights": target_weights},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(cfg, "CONFIG_PATH", path)
        monkeypatch.setattr(cfg, "_cache", None)

    return _write


def _scores(no_data: tuple[str, ...] = ()) -> dict[str, dict]:
    """คะแนนปลอม — ตัวใน ``no_data`` เลียนแบบ "ดึงราคาไม่สำเร็จ" (data_ok=False)."""
    out: dict[str, dict] = {}
    for i, ticker in enumerate(FIVE):
        if ticker in no_data:
            out[ticker] = {"data_ok": False, "total_pct": None, "error": "rate limited"}
        else:
            out[ticker] = {"data_ok": True, "total_pct": 60.0 - i}
    return out


class TestPriceFailureIsNotAConfigError:
    """G1 — สาเหตุจริงคือ "ดึงราคาไม่ได้" ห้ามรายงานว่าคอนฟิกผิด."""

    def test_calculate_allocation_raises_a_data_error_not_a_config_error(self, configured):
        configured(HALF_HALF)

        with pytest.raises(NoTargetForSubset) as exc:
            calculate_allocation(_scores(no_data=("VOO", "SCHD")), 5000.0)

        assert not isinstance(exc.value, InvalidTargetWeights), (
            "ดึงราคาไม่สำเร็จต้องไม่ถูกจัดเป็น 'คอนฟิกผิด' — หน้าจอต้องแยกสองเคสนี้ออกจากกัน"
        )
        assert isinstance(exc.value, TargetWeightsError)

    def test_message_names_both_groups_and_does_not_blame_config(self, configured):
        configured(HALF_HALF)

        with pytest.raises(NoTargetForSubset) as exc:
            calculate_allocation(_scores(no_data=("VOO", "SCHD")), 5000.0)

        message = str(exc.value)
        for ticker in ("QQQM", "XLV", "GLDM"):  # ตัวที่มีข้อมูลรอบนี้แต่เป้าเป็น 0
            assert ticker in message, message
        for ticker in ("VOO", "SCHD"):  # ตัวที่ถือน้ำหนักแต่หายไปจากรอบนี้
            assert ticker in message, message
        assert "ลบคีย์" not in message, "ห้ามชวนผู้ใช้ลบเจตนา 'ไม่ถือ' ทิ้งเพราะ yfinance ล่ม"
        assert "แก้ที่ config.json" not in message

    def test_exception_carries_the_two_groups_for_the_screen(self, configured):
        """หน้าจอต้องเขียนข้อความเองได้โดยไม่ต้อง parse ข้อความ (AI อธิบาย โค้ดคำนวณ)."""
        configured(HALF_HALF)

        with pytest.raises(NoTargetForSubset) as exc:
            calculate_allocation(_scores(no_data=("VOO", "SCHD")), 5000.0)

        assert exc.value.requested == ["GLDM", "QQQM", "XLV"]
        assert exc.value.missing == ["SCHD", "VOO"]

    def test_the_same_config_works_the_moment_prices_come_back(self, configured):
        """ยืนยันว่าคอนฟิกชุดเดิมไม่ได้ผิด — ข้อมูลครบเมื่อไรก็จัดสรรได้ปกติ."""
        configured(HALF_HALF)

        allocation = calculate_allocation(_scores(), 5000.0)

        assert set(allocation) == {"VOO", "SCHD"}
        assert sum(item["amount_thb"] for item in allocation.values()) == 5000

    def test_unset_tickers_outside_the_round_also_count_as_missing_weight(self, configured):
        """``{"GLDM": 0}`` ตัวเดียว: อีกสี่ตัวได้น้ำหนักจาก preset — ไม่ใช่ "ตั้ง 0 ทุกตัว"."""
        configured({"GLDM": 0})

        with pytest.raises(NoTargetForSubset) as exc:
            calculate_allocation(_scores(no_data=("VOO", "SCHD", "QQQM", "XLV")), 5000.0)

        assert exc.value.requested == ["GLDM"]
        assert exc.value.missing == ["QQQM", "SCHD", "VOO", "XLV"]


class TestGenuineConfigErrorsStillFailLoud:
    """เส้นแบ่งอีกด้าน — คอนฟิกที่ผิดจริงต้องยังดังเหมือนเดิม."""

    def test_every_tracked_ticker_set_to_zero_is_still_a_config_error(self, configured):
        configured({s: 0 for s in FIVE})

        with pytest.raises(InvalidTargetWeights) as exc:
            get_target_weights(FIVE)

        assert "0" in str(exc.value)

    def test_all_zero_config_is_a_config_error_even_when_prices_failed(self, configured):
        """ราคาพังพร้อมกับคอนฟิกที่ผิดจริง — ต้องยังชี้ไปที่คอนฟิก."""
        configured({s: 0 for s in FIVE})

        with pytest.raises(InvalidTargetWeights):
            calculate_allocation(_scores(no_data=("VOO", "SCHD")), 5000.0)

    def test_weight_parked_on_an_untracked_ticker_is_a_config_error(self, configured):
        """น้ำหนักทั้งก้อนอยู่ที่ ticker ที่ไม่ได้ติดตาม = คีย์ค้าง ต้องบอกให้ตรงจุด."""
        configured({**{s: 0 for s in FIVE}, "TSLA": 0.5})

        with pytest.raises(InvalidTargetWeights) as exc:
            get_target_weights(FIVE)

        message = str(exc.value)
        assert "TSLA" in message, message
        assert "ตั้งเป็น 0 ทุก ticker" not in message, (
            "ไม่ได้ตั้ง 0 ทุกตัวจริง ๆ — น้ำหนักไปกองอยู่ที่ ticker ที่ไม่ได้ติดตาม"
        )

    def test_bad_units_are_still_a_config_error_in_partial_mode(self, configured):
        configured({"VOO": 35})

        with pytest.raises(InvalidTargetWeights):
            get_target_weights(["QQQM", "XLV"], partial=True)


class TestPartialFlagDoesNotChangeTheNumbers:
    """``partial`` ห้ามบิดสัดส่วนระหว่างกองที่ผู้ใช้ตั้งไว้เอง.

    (``partial`` เปลี่ยน **ฐานที่ใช้คิด** เป็นจักรวาลเต็มด้วย — ดู
    :class:`TestMissingTickerWeightDoesNotLeakToUnheldEtfs` ซึ่งเป็นกรณีที่ต่างกันจริง
    คือตอนที่มี ticker "ไม่ได้ตั้งไว้" อยู่ในชุดย่อย)
    """

    def test_weights_are_identical_with_and_without_the_flag(self, configured):
        """ตั้งครบทุกตัว = ไม่มี ticker ไหนรอรับส่วนที่เหลือ ผลจึงต้องเท่ากันเป๊ะ."""
        configured({"VOO": 0.5, "SCHD": 0.3, "QQQM": 0.2, "XLV": 0, "GLDM": 0})
        subset = ["SCHD", "QQQM", "XLV", "GLDM"]

        assert get_target_weights(subset, partial=True) == get_target_weights(subset)

    def test_remaining_weight_is_rescaled_on_the_tickers_that_do_have_data(self, configured):
        configured({"VOO": 0.5, "SCHD": 0.3, "QQQM": 0.2, "XLV": 0, "GLDM": 0})

        weights = get_target_weights(["SCHD", "QQQM", "XLV", "GLDM"], partial=True)

        assert weights["SCHD"] == pytest.approx(0.6, abs=1e-9)
        assert weights["QQQM"] == pytest.approx(0.4, abs=1e-9)
        assert weights["XLV"] == 0.0 and weights["GLDM"] == 0.0

    def test_default_is_full_universe_so_old_callers_are_untouched(self, configured):
        configured({})
        assert get_target_weights(FIVE) == get_target_weights(FIVE, partial=False)


class TestNotesSayWhyWeightIsMissing:
    """หมายเหตุที่หน้า Settings อ่านต้องแยก "ไม่ได้อยู่ในรอบนี้" ออกจาก "ตั้งไว้ผิด"."""

    def test_partial_round_note_does_not_read_like_a_config_mistake(self, configured):
        configured({"VOO": 0.5, "SCHD": 0.3, "QQQM": 0.2, "XLV": 0, "GLDM": 0})

        status = get_target_weights_with_status(["SCHD", "QQQM", "XLV", "GLDM"], partial=True)

        joined = " | ".join(status.notes)
        assert "VOO" in joined, joined
        assert "ค่าที่ตั้งไม่ถูกใช้" not in joined, (
            "ค่าที่ตั้งของ VOO ยังใช้ได้ปกติ — รอบนี้แค่ไม่มีข้อมูลของมัน"
        )

    def test_full_round_note_still_flags_a_stale_key(self, configured):
        """ไม่ใช่ partial = ticker ที่ตั้งไว้แต่ไม่อยู่ในรายการ คือคีย์ค้างจริง ๆ."""
        configured({"TSLA": 0.2})

        status = get_target_weights_with_status(FIVE)

        assert any("TSLA" in note for note in status.notes)


# ---------------------------------------------------------------------------
# T7 — แผน DCA ต้องบอกว่าใครไม่ได้เงินและเพราะอะไร
# ---------------------------------------------------------------------------

# สถานการณ์จากรายงาน: ตั้งน้ำหนักครบ 100% ให้ 3 กอง อีก 2 กองจึงได้ 0% โดยตั้งใจ
FULL_ON_THREE = {"VOO": 0.4, "SCHD": 0.3, "QQQM": 0.3}


class TestMissingTickerWeightDoesNotLeakToUnheldEtfs:
    """น้ำหนักของกองที่ดึงราคาไม่สำเร็จ ต้องกระจายให้ **ตัวที่ผู้ใช้ถืออยู่** เท่านั้น.

    ถ้าคิดน้ำหนักบนชุดย่อยตรง ๆ ส่วนที่เหลือจะถูกยกให้ ticker ที่ไม่ได้ตั้งไว้ตาม preset
    ⇒ yfinance ล่มหนึ่งวัน แผน DCA ไปซื้อกองที่ผู้ใช้เลือกจะไม่ถือ (T7)
    """

    def test_zero_target_etfs_stay_at_zero_when_another_ticker_has_no_price(self, configured):
        configured(FULL_ON_THREE)  # XLV/GLDM ไม่ได้ตั้งไว้ → ได้ 0% เพราะน้ำหนักถูกใช้ครบ 100%

        weights = get_target_weights(["VOO", "QQQM", "XLV", "GLDM"], partial=True)

        assert weights["XLV"] == 0.0 and weights["GLDM"] == 0.0, (
            "SCHD ดึงราคาไม่สำเร็จ ไม่ใช่เหตุผลให้เริ่มถือ XLV/GLDM"
        )
        assert weights["VOO"] == pytest.approx(4 / 7, abs=1e-9)
        assert weights["QQQM"] == pytest.approx(3 / 7, abs=1e-9)

    def test_omitting_a_ticker_and_setting_it_to_zero_behave_the_same(self, configured):
        """สองคอนฟิกที่ให้น้ำหนักเต็มจักรวาลเท่ากัน ต้องให้ชุดย่อยเท่ากันด้วย."""
        subset = ["VOO", "QQQM", "XLV", "GLDM"]

        configured(FULL_ON_THREE)
        by_omission = get_target_weights(subset, partial=True)

        configured({**FULL_ON_THREE, "XLV": 0, "GLDM": 0})
        by_explicit_zero = get_target_weights(subset, partial=True)

        assert by_omission == by_explicit_zero

    def test_the_note_explains_the_missing_ticker_without_blaming_config(self, configured):
        configured(FULL_ON_THREE)

        status = get_target_weights_with_status(
            ["VOO", "QQQM", "XLV", "GLDM"], partial=True
        )

        joined = " | ".join(status.notes)
        assert "SCHD" in joined, joined
        assert "ไม่ใช่ปัญหาที่ config.json" in joined, joined


class TestZeroTargetEtfIsNamedNotDropped:
    """T7 — เป้าหมาย 0% = เจตนาของผู้ใช้ ต้องรายงาน ไม่ใช่หายไปเฉย ๆ."""

    def test_zero_target_tickers_are_listed_with_a_reason(self, configured):
        configured(FULL_ON_THREE)

        plan = calculate_allocation_with_status(_scores(), 5000.0)

        assert set(plan.allocation) == {"VOO", "SCHD", "QQQM"}
        assert plan.tickers_excluded_by(EXCLUDED_ZERO_TARGET) == ["XLV", "GLDM"]

    def test_the_reason_text_says_intentional_not_missing_data(self, configured):
        """ผู้ใช้ต้องแยกออกว่า "ตั้งใจไม่ถือ" ไม่ใช่ "ดึงข้อมูลไม่ได้"."""
        configured(FULL_ON_THREE)

        plan = calculate_allocation_with_status(_scores(), 5000.0)
        detail = next(i.detail for i in plan.excluded if i.ticker == "XLV")

        assert "0%" in detail, detail
        assert "ไม่ใช่ข้อมูลขาด" in detail, detail

    def test_the_thai_note_from_targets_is_carried_through(self, configured):
        """เหตุผลที่ ``targets.py`` เขียนไว้แล้วต้องไม่ถูกทิ้งกลางทาง."""
        configured(FULL_ON_THREE)

        plan = calculate_allocation_with_status(_scores(), 5000.0)

        joined = " | ".join(plan.notes)
        assert "XLV" in joined and "GLDM" in joined, joined
        assert "0%" in joined, joined

    def test_every_ticker_is_either_funded_or_explained(self, configured):
        """ค่าคงที่ที่ตรึงจริง ๆ: ไม่มี ticker ไหนหายไปโดยไม่มีเหตุผล."""
        configured(FULL_ON_THREE)
        scores = _scores(no_data=("SCHD",))

        plan = calculate_allocation_with_status(scores, 5000.0)

        accounted = set(plan.allocation) | {item.ticker for item in plan.excluded}
        assert accounted == set(scores)

    def test_price_failure_and_zero_target_are_different_reasons(self, configured):
        """สองสาเหตุนี้ห้ามยุบรวม — ผู้ใช้แก้คนละวิธี (รอข้อมูล vs แก้สัดส่วน)."""
        configured(FULL_ON_THREE)

        plan = calculate_allocation_with_status(_scores(no_data=("SCHD",)), 5000.0)

        assert plan.tickers_excluded_by(EXCLUDED_NO_DATA) == ["SCHD"]
        assert plan.tickers_excluded_by(EXCLUDED_ZERO_TARGET) == ["XLV", "GLDM"]
        no_data_detail = next(i.detail for i in plan.excluded if i.ticker == "SCHD")
        assert "rate limited" in no_data_detail, "ต้องยกเหตุผลจริงของการดึงข้อมูลมาด้วย"
        assert "ไม่ใช่การตัดสินว่าไม่น่าซื้อ" in no_data_detail

    def test_budget_too_small_for_a_slice_is_its_own_reason(self, configured):
        """งบไม่พอปัดเป็นก้อนละ 100 บาท ≠ ไม่ถือ ≠ ไม่มีข้อมูล."""
        configured({"VOO": 0.99, "SCHD": 0.01, "QQQM": 0, "XLV": 0, "GLDM": 0})

        plan = calculate_allocation_with_status(_scores(), 100.0)

        assert plan.tickers_excluded_by(EXCLUDED_ROUNDED_TO_ZERO) == ["SCHD"]
        detail = next(i.detail for i in plan.excluded if i.ticker == "SCHD")
        assert "เพิ่มงบ" in detail, detail

    def test_plain_helper_still_returns_just_the_money(self, configured):
        """ผู้เรียกเดิมต้องไม่พัง — ``calculate_allocation()`` ยังคืน dict รูปแบบเดิม."""
        configured(FULL_ON_THREE)

        allocation = calculate_allocation(_scores(), 5000.0)
        plan = calculate_allocation_with_status(_scores(), 5000.0)

        assert allocation == plan.allocation
        assert sum(item["amount_thb"] for item in allocation.values()) == 5000

    def test_nothing_usable_still_names_every_ticker(self, configured):
        """ไม่มีข้อมูลสักตัว: แผนว่างได้ แต่ต้องยังบอกว่าใครหายและเพราะอะไร."""
        configured(FULL_ON_THREE)

        plan = calculate_allocation_with_status(_scores(no_data=tuple(FIVE)), 5000.0)

        assert plan.allocation == {}
        assert plan.tickers_excluded_by(EXCLUDED_NO_DATA) == FIVE


# ---------------------------------------------------------------------------
# T6 — การจำลอง DCA ต้องบอกว่ากองไหนไม่ได้ถูกจำลอง
# ---------------------------------------------------------------------------


def _dca_prices(tickers=("VOO", "SCHD")) -> pd.DataFrame:
    """ราคารายวัน 2 ปีของกองที่ระบุ (ค่าจริงไม่สำคัญ — ทดสอบการรายงาน ไม่ใช่ตัวเลข)."""
    index = pd.bdate_range("2019-01-01", "2020-12-31")
    frame = pd.DataFrame(index=index)
    for i, ticker in enumerate(tickers):
        frame[ticker] = np.linspace(100.0 + i * 10, 150.0 + i * 10, len(index))
    return frame


class TestDcaSimulationNamesWhoWasLeftOut:
    """T6 — ตัดกองออกจากการจำลองได้ แต่ต้องติดชื่อกลับมากับผลลัพธ์เสมอ."""

    def test_ticker_without_prices_is_reported_not_silently_dropped(self):
        result = simulate_monthly_dca(
            _dca_prices(), {"VOO": 0.5, "SCHD": 0.3, "QQQM": 0.2}, monthly_investment=1000.0
        )

        coverage = result.attrs[COVERAGE_ATTR]
        assert coverage[NO_PRICE_KEY] == ["QQQM"], (
            "QQQM ถือน้ำหนัก 20% แต่ถูกตัดออกแล้ว normalize ที่เหลือใหม่ — ห้ามเงียบ"
        )

    def test_the_warning_says_the_curve_is_not_the_portfolio_that_was_asked_for(self):
        result = simulate_monthly_dca(
            _dca_prices(), {"VOO": 0.5, "SCHD": 0.3, "QQQM": 0.2}, monthly_investment=1000.0
        )

        message = describe_coverage(result.attrs[COVERAGE_ATTR])
        assert message and "QQQM" in message, message
        assert "ไม่ใช่พอร์ตตามสัดส่วนที่กรอกมา" in message, message

    def test_zero_weight_is_reported_separately_from_missing_prices(self):
        """ตั้ง 0 เอง = เจตนา · ไม่มีราคา = ข้อมูลขาด — คนละคีย์ คนละข้อความ."""
        result = simulate_monthly_dca(
            _dca_prices(), {"VOO": 1.0, "SCHD": 0.0}, monthly_investment=1000.0
        )

        coverage = result.attrs[COVERAGE_ATTR]
        assert coverage[ZERO_WEIGHT_KEY] == ["SCHD"]
        assert coverage[NO_PRICE_KEY] == []

        message = describe_coverage(coverage)
        assert message and "SCHD" in message, message
        assert "ตั้งใจไม่ถือ" in message, message

    def test_zero_weight_ticker_does_not_shrink_the_simulated_window(self):
        """กองที่ไม่ได้ซื้อสักบาทต้องไม่ทำให้เดือนที่มันยังไม่มีราคาถูกตัดทั้งเดือน."""
        prices = _dca_prices()
        prices.loc[prices.index < "2020-01-01", "SCHD"] = np.nan

        held_only = simulate_monthly_dca(prices, {"VOO": 1.0}, monthly_investment=1000.0)
        with_zero = simulate_monthly_dca(
            prices, {"VOO": 1.0, "SCHD": 0.0}, monthly_investment=1000.0
        )

        assert len(with_zero) == len(held_only)
        assert with_zero.attrs[COVERAGE_ATTR]["months_dropped"] == 0

    def test_the_keys_are_always_present_so_absence_is_readable(self):
        """คีย์หาย ≠ ลิสต์ว่าง — ปลายทางต้องแยก "ไม่มีใครถูกตัด" ออกจาก "ยังไม่มีฟิลด์นี้"."""
        result = simulate_monthly_dca(
            _dca_prices(), {"VOO": 0.6, "SCHD": 0.4}, monthly_investment=1000.0
        )

        coverage = result.attrs[COVERAGE_ATTR]
        assert coverage[ZERO_WEIGHT_KEY] == [] and coverage[NO_PRICE_KEY] == []
        assert describe_coverage(coverage) is None, "ไม่มีอะไรถูกตัด = ห้ามมีคำเตือนหลอก"

    def test_no_ticker_with_prices_names_the_missing_ones(self):
        with pytest.raises(RuntimeError) as exc:
            simulate_monthly_dca(_dca_prices(), {"QQQM": 1.0}, monthly_investment=1000.0)

        assert "QQQM" in str(exc.value), str(exc.value)


# ---------------------------------------------------------------------------
# รอบเก็บตก — เอกสารในโค้ดต้องพูดตรงกับสิ่งที่โค้ดทำ
#
# ``partial=True`` ของรอบนี้ทำให้กติกาข้อ 1 ใน docstring ของ ``portfolio/targets.py``
# ("ticker ที่ตั้งไว้ได้ค่านั้นเป๊ะ ๆ ไม่ถูก normalize บิด") **ไม่จริงอีกต่อไปในโหมดนั้น**
# — คิดบนจักรวาลเต็มก่อนแล้วตัด+normalize ใหม่ ค่าที่ตั้งไว้จึงถูกขยาย (โดยตั้งใจและถูกต้อง)
#
# docstring ที่ขัดกับโค้ดคือทางที่คนถัดไปจะ "แก้ให้ตรงเอกสาร" แล้วใส่บั๊กเดิมกลับเข้ามา
# (น้ำหนักของกองที่ราคาพังไหลไปให้กองที่ผู้ใช้ตั้งใจไม่ถือ) เทสต์กลุ่มนี้จึงผูกเอกสารกับ
# ตัวเลขจริงเข้าด้วยกัน — ตัวเลขในเอกสารต้องเป็นตัวเลขที่โค้ดคำนวณได้จริง
# ---------------------------------------------------------------------------


class TestDocstringMatchesWhatPartialActuallyDoes:
    """AUDIT_ROUND2_2026-08-07 — สัญญาใน docstring ของ targets.py ต้องเป็นสัญญาที่โค้ดทำจริง."""

    @staticmethod
    def _module_doc() -> str:
        import portfolio.targets as targets

        assert targets.__doc__, "targets.py ต้องมี docstring อธิบายกติกา"
        return targets.__doc__

    def test_rule_one_is_scoped_to_the_full_universe(self):
        """ประโยค "ได้ค่านั้นเป๊ะ ๆ" ต้องบอกในที่เดียวกันว่าเป็นจริงเฉพาะ ``partial=False``."""
        doc = self._module_doc()
        at = doc.find("ได้ค่านั้นเป๊ะ ๆ")

        assert at >= 0, "กติกาข้อ 1 หายไปจาก docstring"
        nearby = doc[at : at + 400]
        assert "partial=False" in nearby, (
            "กติกาข้อ 1 ยังเขียนเป็นคำสัญญาไร้เงื่อนไข ทั้งที่ partial=True ขยายค่าที่ตั้งไว้: "
            + nearby[:200]
        )

    def test_the_partial_section_states_both_what_is_and_is_not_guaranteed(self):
        doc = self._module_doc()

        assert "ไม่รับประกัน" in doc, "ต้องเขียนให้ชัดว่าอะไรที่ partial ไม่รับประกัน"
        assert "รับประกัน" in doc
        assert "อัตราส่วน" in doc, "สิ่งที่ยังรับประกันคืออัตราส่วนระหว่างกองที่อยู่ในรอบ"

    def test_the_number_in_the_docstring_is_the_number_the_code_produces(self, configured):
        """เอกสารยกตัวอย่าง VOO 57.1% — ถ้าโค้ดเปลี่ยนแล้วเอกสารไม่เปลี่ยน เทสต์นี้ต้องแดง."""
        configured(FULL_ON_THREE)  # {VOO: .4, SCHD: .3, QQQM: .3}

        weights = get_target_weights(["VOO", "QQQM", "XLV", "GLDM"], partial=True)

        assert f"{weights['VOO'] * 100:.1f}%" == "57.1%"
        assert "57.1%" in self._module_doc(), (
            "ตัวอย่างในเอกสารต้องเป็นตัวเลขชุดเดียวกับที่โค้ดคำนวณได้"
        )
        assert weights["XLV"] == 0.0 and weights["GLDM"] == 0.0, "0 ต้องยังเป็น 0 ตามที่เอกสารสัญญา"

    def test_the_configured_weight_really_is_rescaled_in_partial_mode(self, configured):
        """พิสูจน์ว่าคำสัญญาเดิม ("ไม่ถูก normalize บิด") ใช้ไม่ได้กับโหมดนี้จริง ๆ."""
        configured(FULL_ON_THREE)

        status = get_target_weights_with_status(["VOO", "QQQM", "XLV", "GLDM"], partial=True)

        assert status.configured["VOO"] == 0.4, "ค่าที่ผู้ใช้ตั้งต้องยังรายงานตามที่ตั้ง"
        assert status.weights["VOO"] != 0.4, "แต่ค่าที่ใช้จริงรอบนี้ถูกขยาย — เอกสารต้องพูดตรงนี้"
        assert status.weights["VOO"] / status.weights["QQQM"] == pytest.approx(4 / 3, abs=1e-9)
        assert status.adjusted is True, "ต้องชูธงว่าตัวเลขต่างจากที่ตั้ง ไม่ใช่เงียบ"
        assert any("SCHD" in note for note in status.notes), status.notes

    def test_the_promise_still_holds_on_the_full_universe(self, configured):
        """อีกด้าน — ``partial=False`` ยังต้องให้ค่าที่ตั้งไว้เป๊ะ ๆ ตามกติกาข้อ 1."""
        configured({"VOO": 0.4})

        status = get_target_weights_with_status(FIVE)

        assert status.weights["VOO"] == pytest.approx(0.4, abs=1e-12)
        assert status.adjusted is False


# ---------------------------------------------------------------------------
# รอบเก็บตก — หน่วยจัดสรร 100 บาท มีนิยามเดียว
#
# ``portfolio/cashflow_rebalance.py`` เขียน ``UNIT_THB = 100`` ไว้เป็นสำเนาที่สองของ
# ``financial_model.ALLOCATION_UNIT_THB`` ทั้งที่หน้าจอเสนอให้ผู้ใช้สลับสองโหมดนี้ได้
# (แผน DCA ปกติ ↔ ดึงเข้าเป้าด้วยเงินใหม่) — วันที่หน่วยเปลี่ยน สองโหมดจะปัดเงินคนละหน่วย
# และเกณฑ์ "งบขั้นต่ำ" จะพูดคนละเลขโดยไม่มีใครรู้
# ---------------------------------------------------------------------------


class TestAllocationUnitHasOneDefinition:
    """AUDIT_ROUND2_2026-08-07 — เลื่อนค่าคงที่ตัวเดียว ทั้งสองโหมดต้องเลื่อนตาม."""

    def test_cashflow_rebalance_follows_the_canonical_constant(self):
        """เปลี่ยนค่าที่ต้นทางแล้ว import ใหม่ — ถ้าไฟล์นั้นถือ literal ของตัวเอง ค่าจะไม่ขยับ."""
        import importlib

        import analysis.financial_model as fm
        import portfolio.cashflow_rebalance as cr

        original = fm.ALLOCATION_UNIT_THB
        try:
            fm.ALLOCATION_UNIT_THB = 500
            importlib.reload(cr)

            assert cr.UNIT_THB == 500, "UNIT_THB ยังเป็นสำเนาที่สอง ไม่ได้อ่านจากต้นทาง"
            with pytest.raises(ValueError) as exc:
                cr.rebalance_with_new_money({"VOO": 10_000.0}, {"VOO": 1.0}, 300.0)
            assert "500" in str(exc.value), str(exc.value)
        finally:
            fm.ALLOCATION_UNIT_THB = original
            importlib.reload(cr)

        assert cr.UNIT_THB == fm.ALLOCATION_UNIT_THB == 100

    def test_the_module_does_not_redefine_the_literal(self):
        """ตาข่ายอ่านง่าย: ห้ามมี ``UNIT_THB = <ตัวเลข>`` กลับเข้ามาในไฟล์นั้นอีก."""
        import re

        import portfolio.cashflow_rebalance as cr

        source = Path(cr.__file__).read_text(encoding="utf-8")

        assert "from analysis.financial_model import ALLOCATION_UNIT_THB" in source
        assert not re.search(r"^UNIT_THB\s*=\s*[0-9]", source, re.MULTILINE), (
            "หน่วยจัดสรรต้องมีนิยามเดียวที่ financial_model.ALLOCATION_UNIT_THB"
        )

    def test_both_plans_round_money_to_the_same_unit(self, configured):
        """ผลลัพธ์จริงของสองโหมดต้องปัดด้วยหน่วยเดียวกัน (ผู้ใช้สลับโหมดได้ในหน้าเดียว)."""
        from analysis.financial_model import ALLOCATION_UNIT_THB
        from portfolio.cashflow_rebalance import rebalance_with_new_money

        configured({"VOO": 0.6, "SCHD": 0.4, "QQQM": 0, "XLV": 0, "GLDM": 0})

        dca = calculate_allocation(_scores(), 5000.0)
        cashflow = rebalance_with_new_money(
            {"VOO": 30_000.0, "SCHD": 10_000.0}, {"VOO": 0.6, "SCHD": 0.4}, 5000.0
        )

        for item in dca.values():
            assert item["amount_thb"] % ALLOCATION_UNIT_THB == 0
        for item in cashflow.values():
            assert item["amount_thb"] % ALLOCATION_UNIT_THB == 0
