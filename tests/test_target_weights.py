# -*- coding: utf-8 -*-
"""AUDIT_2026-08-06 B10 — ``portfolio/targets.py`` ทิ้ง/บิดน้ำหนักที่ผู้ใช้ตั้งเองเงียบ ๆ

อาการที่วัดได้ก่อนแก้ (preset moderate = VOO 35 / SCHD 25 / QQQM 20 / XLV 10 / GLDM 10)::

    {"GLDM":0}    → {'VOO':35.0,...,'GLDM':10.0}   ค่าที่ตั้งถูกทิ้งเงียบ
    {"GLDM":0.20} → GLDM 18.18%                    ขอ 20 ได้ 18.18
    {"VOO":35}    → VOO 98.18%                     ไม่ตรวจหน่วย
    {"VOO":-0.35} → เหมือน preset ทุกตัว            ค่าลบถูกทิ้งเงียบ
    {"VOO":"abc"} → เหมือน preset ทุกตัว            ค่าผิดชนิดถูกทิ้งเงียบ

เทสต์ชุดเดิม (``tests/test_phase3.py::TestTargetWeights``) assert แค่ "รวมเป็น 1"
กับ "ทุกตัว > 0" — mutation ที่ถอด normalize และเปลี่ยนสูตร leftover รอดทั้งคู่
ไฟล์นี้จึงตรึง **ค่าจริงทุกช่อง** ไม่ใช่แค่ผลรวม

ไม่ยิง network และไม่แตะ ``config.json`` จริง (ทุกเทสต์เขียน config ชั่วคราวใน tmp_path)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from portfolio.targets import (
    InvalidTargetWeights,
    get_target_weights,
    get_target_weights_with_status,
)

FIVE = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
PRESET_MODERATE = {"VOO": 0.35, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.10}
EXACT = 1e-9


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """เขียน config.json ชั่วคราวแล้วชี้ ``utils.config`` มาที่ไฟล์นั้น."""

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


def _assert_weights(actual: dict[str, float], expected: dict[str, float]) -> None:
    assert set(actual) == set(expected)
    for symbol, want in expected.items():
        assert actual[symbol] == pytest.approx(want, abs=EXACT), symbol
    assert sum(actual.values()) == pytest.approx(1.0, abs=1e-9)


class TestPresetIsUnchanged:
    """ไม่ได้ตั้งอะไรเลย = preset เป๊ะ ๆ (ตรึงค่าจริง ไม่ใช่แค่ผลรวม)."""

    def test_empty_custom_gives_the_preset_verbatim(self, configured):
        configured({})
        _assert_weights(get_target_weights(FIVE), PRESET_MODERATE)

    def test_conservative_profile_gives_its_own_preset(self, configured):
        configured({}, profile="conservative")
        _assert_weights(
            get_target_weights(FIVE),
            {"VOO": 0.30, "SCHD": 0.30, "QQQM": 0.10, "XLV": 0.20, "GLDM": 0.10},
        )


class TestUserWeightsAreHonoured:
    """B10 — ค่าที่ผู้ใช้ตั้งต้องถูกใช้ตามที่ตั้ง ห้ามถูก normalize บิด."""

    def test_zero_means_do_not_hold_it(self, configured):
        """``{"GLDM": 0}`` = ตั้งใจไม่ถือ — เดิมถูกทิ้งเงียบแล้วคืน 10% ตาม preset."""
        configured({"GLDM": 0})
        weights = get_target_weights(FIVE)

        assert weights["GLDM"] == 0.0
        # ที่เหลือรับส่วนแบ่งของ GLDM ตามอัตราส่วน preset เดิม (0.9 → 1.0)
        _assert_weights(
            weights,
            {
                "VOO": 0.35 / 0.9,
                "SCHD": 0.25 / 0.9,
                "QQQM": 0.20 / 0.9,
                "XLV": 0.10 / 0.9,
                "GLDM": 0.0,
            },
        )

    def test_partial_weight_is_used_exactly_as_configured(self, configured):
        """ขอ GLDM 20% ต้องได้ 20% พอดี — เดิมได้ 18.18%."""
        configured({"GLDM": 0.20})
        weights = get_target_weights(FIVE)

        assert weights["GLDM"] == pytest.approx(0.20, abs=EXACT)
        _assert_weights(
            weights,
            {
                "VOO": 0.35 * 0.8 / 0.9,
                "SCHD": 0.25 * 0.8 / 0.9,
                "QQQM": 0.20 * 0.8 / 0.9,
                "XLV": 0.10 * 0.8 / 0.9,
                "GLDM": 0.20,
            },
        )

    def test_unset_tickers_keep_the_preset_ratio_between_themselves(self, configured):
        """ตัวที่ไม่ได้ตั้งย่อ/ขยายพร้อมกัน อัตราส่วนเดิมของ preset ต้องไม่เพี้ยน."""
        configured({"GLDM": 0.20})
        weights = get_target_weights(FIVE)

        assert weights["VOO"] / weights["SCHD"] == pytest.approx(0.35 / 0.25, abs=1e-9)
        assert weights["QQQM"] / weights["XLV"] == pytest.approx(0.20 / 0.10, abs=1e-9)

    def test_full_fraction_set_is_passed_through(self, configured):
        configured({"VOO": 0.4, "SCHD": 0.2, "QQQM": 0.2, "XLV": 0.1, "GLDM": 0.1})
        _assert_weights(
            get_target_weights(FIVE),
            {"VOO": 0.4, "SCHD": 0.2, "QQQM": 0.2, "XLV": 0.1, "GLDM": 0.1},
        )

    def test_full_percent_set_is_read_as_percent(self, configured):
        """ผลรวม 100 = เขียนเป็นเปอร์เซ็นต์ — ต้องอ่านถูกและบอกผู้ใช้ว่าอ่านแบบไหน."""
        configured({"VOO": 35, "SCHD": 25, "QQQM": 20, "XLV": 10, "GLDM": 10})
        status = get_target_weights_with_status(FIVE)

        _assert_weights(status.weights, PRESET_MODERATE)
        assert status.adjusted is False
        assert any("เปอร์เซ็นต์" in note for note in status.notes)

    def test_setting_every_ticker_to_hundred_percent_leaves_nothing_for_the_rest(self, configured):
        """ตั้งครบ 100% แล้ว ticker ที่เหลือได้ 0 — ต้องบอก ไม่ใช่เงียบ."""
        configured(
            {"VOO": 0.4, "SCHD": 0.2, "QQQM": 0.2, "XLV": 0.1, "GLDM": 0.1},
            tickers=FIVE + ["VT"],
        )
        status = get_target_weights_with_status(FIVE + ["VT"])

        assert status.weights["VT"] == 0.0
        assert any("VT" in note for note in status.notes)


class TestBadInputFailsLoud:
    """ค่าที่ตั้งผิดต้องดัง ไม่ใช่ถูกทิ้งเงียบแล้วแอบใช้ preset แทน."""

    def test_negative_weight_raises(self, configured):
        configured({"VOO": -0.35})
        with pytest.raises(InvalidTargetWeights) as exc:
            get_target_weights(FIVE)
        assert "VOO" in str(exc.value)
        assert "ติดลบ" in str(exc.value)

    def test_non_numeric_weight_raises(self, configured):
        configured({"VOO": "abc"})
        with pytest.raises(InvalidTargetWeights) as exc:
            get_target_weights(FIVE)
        assert "VOO" in str(exc.value)
        assert "ตัวเลข" in str(exc.value)

    def test_percent_looking_partial_set_raises_instead_of_guessing(self, configured):
        """``{"VOO": 35}`` กำกวม (35% หรือ 3500%?) เดิมเดาเป็น 98.18% เงียบ ๆ."""
        configured({"VOO": 35})
        with pytest.raises(InvalidTargetWeights) as exc:
            get_target_weights(FIVE)
        message = str(exc.value)
        assert "35" in message
        assert "100" in message  # ต้องบอกรูปแบบที่รับได้

    def test_over_allocated_set_raises(self, configured):
        configured({"VOO": 0.8, "SCHD": 0.8})
        with pytest.raises(InvalidTargetWeights):
            get_target_weights(FIVE)

    def test_everything_set_to_zero_raises(self, configured):
        configured({s: 0 for s in FIVE})
        with pytest.raises(InvalidTargetWeights) as exc:
            get_target_weights(FIVE)
        assert "0" in str(exc.value)

    def test_target_weights_that_is_not_a_mapping_raises(self, configured):
        configured(["VOO", 0.4])
        with pytest.raises(InvalidTargetWeights):
            get_target_weights(FIVE)

    def test_boolean_is_not_a_weight(self, configured):
        configured({"VOO": True})
        with pytest.raises(InvalidTargetWeights):
            get_target_weights(FIVE)


class TestStatusFlag:
    """แนวแก้ B10: "ถ้าค่าที่ใช้จริงต่างจากที่ตั้ง ต้องคืนธงให้หน้า Settings แสดง"."""

    def test_no_flag_when_everything_is_used_as_configured(self, configured):
        configured({"GLDM": 0.20})
        status = get_target_weights_with_status(FIVE)

        assert status.adjusted is False
        assert status.configured == {"GLDM": 0.20}
        assert status.source["GLDM"] == "custom"
        assert status.source["VOO"] == "preset"

    def test_scaled_up_set_is_flagged_with_the_numbers(self, configured):
        """ตั้งครบทุกตัวแต่รวมได้ 90% → ระบบขยายเป็น 100% ต้องบอกว่าใช้จริงเท่าไร."""
        configured({"VOO": 0.3, "SCHD": 0.2, "QQQM": 0.2, "XLV": 0.1, "GLDM": 0.1})
        status = get_target_weights_with_status(FIVE)

        assert status.adjusted is True
        assert status.notes
        _assert_weights(
            status.weights,
            {
                "VOO": 0.3 / 0.9,
                "SCHD": 0.2 / 0.9,
                "QQQM": 0.2 / 0.9,
                "XLV": 0.1 / 0.9,
                "GLDM": 0.1 / 0.9,
            },
        )

    def test_weight_for_a_ticker_outside_the_list_is_reported(self, configured):
        configured({"TSLA": 0.2})
        status = get_target_weights_with_status(FIVE)

        assert "TSLA" not in status.weights
        assert any("TSLA" in note for note in status.notes)

    def test_plain_helper_returns_the_same_weights(self, configured):
        configured({"GLDM": 0.20})
        assert get_target_weights(FIVE) == get_target_weights_with_status(FIVE).weights


class TestUnknownTicker:
    """ETF ที่เพิ่งเพิ่มและ preset ไม่รู้จัก ต้องได้ส่วนแบ่ง — และต้องเป็นค่าที่ทำนายได้."""

    def test_new_ticker_gets_an_average_sized_slice(self, configured):
        """ฐานของ ticker ที่ preset ไม่รู้จัก = 1/จำนวน preset (= 0.20 ของ moderate)."""
        configured({}, tickers=["VOO", "SCHD", "VTI"])
        # ฐาน 0.35 / 0.25 / 0.20 รวม 0.80 → ขยายให้เต็ม 1.0 (คูณ 1.25)
        _assert_weights(
            get_target_weights(["VOO", "SCHD", "VTI"]),
            {"VOO": 0.4375, "SCHD": 0.3125, "VTI": 0.25},
        )

    def test_new_ticker_alongside_the_full_preset(self, configured):
        configured({}, tickers=FIVE + ["VT"])
        weights = get_target_weights(FIVE + ["VT"])
        total_base = 1.0 + 0.20
        _assert_weights(
            weights,
            {
                "VOO": 0.35 / total_base,
                "SCHD": 0.25 / total_base,
                "QQQM": 0.20 / total_base,
                "XLV": 0.10 / total_base,
                "GLDM": 0.10 / total_base,
                "VT": 0.20 / total_base,
            },
        )

    def test_no_ticker_list_falls_back_to_the_configured_universe(self, configured):
        """สัญญาเดิมที่ไม่ได้เปลี่ยนในรอบนี้: ``None``/``[]`` = ใช้ ticker จาก config."""
        configured({}, tickers=["VOO", "SCHD", "VTI"])
        expected = {"VOO": 0.4375, "SCHD": 0.3125, "VTI": 0.25}
        _assert_weights(get_target_weights(None), expected)
        _assert_weights(get_target_weights([]), expected)
