# -*- coding: utf-8 -*-
"""ต่อยอด ERC (ผู้ใช้สั่ง "ทำทั้งหมด" 2026-09-30) — สามเรื่อง:

1. วัดความเสี่ยงเป็นเงินบาท (ราคา × USDTHB) — ดึงค่าเงินไม่ได้ต้อง **บอก** ว่าวัดเป็น USD
2. เตือนเซกเตอร์ที่หนักเกิน 2 เท่าของตลาดโลก (VT) **ภายในส่วนหุ้น** — ERC ทำให้หุ้นสุขภาพ
   เป็น ~34% ของส่วนหุ้น เทียบตลาดโลก 8.6% เพราะมันดูแค่ราคา ไม่เห็นหุ้นข้างในกอง
3. วิธี ``erc_sector_cap`` (ทางเลือก ไม่ใช่ค่าเริ่มต้น — backtest ไม่ผ่านเกณฑ์ที่ล็อกไว้ก่อน)

ไม่ยิง network — ราคา/ค่าเงิน/funds_data เป็นสตับทั้งหมด
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

import data.fetcher as fetcher
import portfolio.lookthrough as lookthrough
import portfolio.risk_weights as rw
from data.fetcher import PriceDataUnavailableError
from portfolio import targets

FIVE = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
# สัดส่วนเซกเตอร์จริงที่วัด 2026-09-30 (ย่อเหลือ 3 เซกเตอร์ + ส่วนที่เหลือ)
SECTORS = {
    "VT": {"technology": 0.303, "healthcare": 0.086, "other": 0.611},
    "VOO": {"technology": 0.387, "healthcare": 0.093, "other": 0.520},
    "SCHD": {"technology": 0.129, "healthcare": 0.215, "other": 0.656},
    "QQQM": {"technology": 0.592, "healthcare": 0.040, "other": 0.368},
    "XLV": {"healthcare": 1.0},
}


def _fund_data_stub(symbol):
    if symbol == "GLDM":
        return None, None, ""  # ทอง: ไม่มีเซกเตอร์ แต่ไม่ใช่ความล้มเหลว
    if symbol in SECTORS:
        return None, dict(SECTORS[symbol]), ""
    return None, None, "ไม่รู้จักกองนี้"


# ---------------------------------------------------------------- เพดานเซกเตอร์ (คณิตศาสตร์)
class TestCappedErc:
    COV = np.outer([0.15, 0.12, 0.16], [0.15, 0.12, 0.16]) * np.array(
        [[1.0, 0.8, 0.2], [0.8, 1.0, 0.3], [0.2, 0.3, 1.0]]
    )

    def test_no_binding_cap_gives_plain_erc(self):
        exposures = np.array([[0.1, 0.1, 0.1]])
        w, hit = rw.erc_weights_capped(self.COV, exposures, np.array([0.9]))
        assert hit == []
        assert w == pytest.approx(rw.erc_weights(self.COV), abs=1e-12)

    def test_binding_cap_is_respected_and_everyone_still_gets_money(self):
        exposures = np.array([[0.0, 0.2, 1.0]])  # กองที่ 3 = เซกเตอร์นี้ล้วน (แบบ XLV)
        free = rw.erc_weights(self.COV)
        assert exposures[0] @ free > 0.25
        w, hit = rw.erc_weights_capped(self.COV, exposures, np.array([0.25]))
        assert hit == [0]
        assert exposures[0] @ w == pytest.approx(0.25, abs=1e-6)
        assert np.all(w > 0.01), "เพดานต้องไม่ตัดกองไหนทิ้ง (นโยบายซื้อทุกกองทุกเดือน)"
        assert w[2] < free[2]

    def test_cap_is_measured_inside_the_stock_part(self):
        """กองที่ 3 เป็นทอง (basis 0) — ทองเยอะขึ้นต้องไม่ทำให้เพดานหุ้นหลวมลง."""
        exposures = np.array([[1.0, 0.0, 0.0]])  # กองที่ 1 = เซกเตอร์นี้ล้วน
        basis = np.array([1.0, 1.0, 0.0])
        w, _ = rw.erc_weights_capped(self.COV, exposures, np.array([0.3]), basis)
        assert w[0] / (w[0] + w[1]) == pytest.approx(0.3, abs=1e-6)

    def test_impossible_cap_raises_instead_of_returning_bad_weights(self):
        exposures = np.array([[1.0, 1.0, 1.0]])  # ทุกกองเป็นเซกเตอร์เดียวกันล้วน
        with pytest.raises(ValueError):
            rw.erc_weights_capped(self.COV, exposures, np.array([0.5]))


# ---------------------------------------------------------------- เงินบาท
def _prices(cols, n=600, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    rets = rng.normal(0.0004, 0.01, size=(n, len(cols)))
    return pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=idx, columns=cols)


class TestThb:
    def test_prices_are_converted_with_the_daily_rate(self, monkeypatch):
        usd = _prices(["VOO", "GLDM"], n=5)
        fx = pd.Series([33.0, 34.0, 35.0, 36.0, 37.0], index=usd.index)
        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", lambda tickers, years: fx.to_frame("THB=X"))
        thb = rw.to_thb(usd)
        assert thb.iloc[2, 0] == pytest.approx(usd.iloc[2, 0] * 35.0)

    def test_build_measures_in_thb(self, monkeypatch):
        usd = _prices(FIVE)
        fx = pd.Series(33.0 + np.sin(np.arange(len(usd)) / 20.0), index=usd.index, name="THB=X")

        def _fetch(tickers, years):
            return fx.to_frame() if tickers == ["THB=X"] else usd[tickers]

        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", _fetch)
        result = rw.build_erc(FIVE)
        assert result["meta"]["currency"] == "THB"
        assert sum(result["weights"].values()) == pytest.approx(1.0)

    def test_fx_failure_falls_back_to_usd_and_says_so(self, monkeypatch):
        usd = _prices(FIVE)

        def _fetch(tickers, years):
            if tickers == ["THB=X"]:
                raise PriceDataUnavailableError("THB=X rate limit")
            return usd[tickers]

        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", _fetch)
        result = rw.build_erc(FIVE)
        assert result["meta"]["currency"] == "USD"
        assert "rate limit" in result["meta"]["fx_error"]

    def test_usd_fallback_reaches_the_notes(self, tmp_path, monkeypatch):
        from utils import config as cfg

        path = tmp_path / "config.json"
        path.write_text(json.dumps({"etf": {"tickers": FIVE}, "portfolio": {"weighting_method": "erc"}}))
        monkeypatch.setattr(cfg, "CONFIG_PATH", path)
        monkeypatch.setattr(cfg, "_cache", None)
        monkeypatch.setattr(
            rw,
            "compute_erc_weights",
            lambda t, cap=False: {
                "weights": {x: 0.2 for x in t},
                "risk_share": {x: 0.2 for x in t},
                "meta": {"currency": "USD", "fx_error": "timeout"},
            },
        )
        status = targets.get_target_weights_with_status(FIVE)
        assert any("USD" in n and "timeout" in n for n in status.notes)


# ---------------------------------------------------------------- ความกระจุกตัวเทียบตลาดโลก
class TestSectorConcentration:
    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        monkeypatch.setattr(lookthrough, "_fund_data", _fund_data_stub)

    ERC_TODAY = dict(zip(FIVE, [0.214, 0.269, 0.154, 0.203, 0.160]))

    def test_healthcare_is_flagged_against_the_world(self):
        conc = lookthrough.sector_concentration(self.ERC_TODAY)
        flagged = {f["sector"]: f for f in conc["flags"]}
        assert "healthcare" in flagged
        assert flagged["healthcare"]["portfolio_pct"] == pytest.approx(34.0, abs=0.5)
        assert flagged["healthcare"]["ratio"] > 3.5
        assert "technology" not in flagged

    def test_gold_does_not_dilute_the_stock_percentages(self):
        more_gold = dict(self.ERC_TODAY, GLDM=0.6)
        a = lookthrough.sector_concentration(self.ERC_TODAY)["portfolio"]["healthcare"]
        b = lookthrough.sector_concentration(more_gold)["portfolio"]["healthcare"]
        assert a == pytest.approx(b, abs=0.05)

    def test_world_unavailable_is_an_error_not_an_all_clear(self, monkeypatch):
        monkeypatch.setattr(
            lookthrough, "_fund_data", lambda s: (None, None, "down") if s == "VT" else _fund_data_stub(s)
        )
        with pytest.raises(ValueError, match="VT"):
            lookthrough.sector_concentration(self.ERC_TODAY)

    def test_missing_fund_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            lookthrough, "_fund_data", lambda s: (None, None, "down") if s == "SCHD" else _fund_data_stub(s)
        )
        conc = lookthrough.sector_concentration(self.ERC_TODAY)
        assert "SCHD" in conc["unavailable"]
        assert any("SCHD" in line for line in lookthrough.concentration_lines(conc))

    def test_thai_warning_line(self):
        lines = lookthrough.concentration_lines(lookthrough.sector_concentration(self.ERC_TODAY))
        assert any("สุขภาพ" in line and "ตลาดโลก" in line for line in lines)


class TestSectorCapMethod:
    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        monkeypatch.setattr(lookthrough, "_fund_data", _fund_data_stub)

    def test_inputs_use_twice_the_world_weight_inside_stocks(self):
        inputs = rw.sector_cap_inputs(FIVE)
        hc = inputs["sectors"].index("healthcare")
        assert inputs["caps"][hc] == pytest.approx(2 * 0.086)
        assert inputs["basis"][FIVE.index("GLDM")] == 0.0
        assert inputs["basis"][FIVE.index("VOO")] == pytest.approx(1.0)

    def test_capped_plan_brings_healthcare_under_the_line(self):
        prices = _prices(FIVE, seed=4)
        plain = rw.erc_from_prices(prices, FIVE)
        capped = rw.erc_from_prices(prices, FIVE, rw.sector_cap_inputs(FIVE))
        assert "healthcare" in capped["meta"]["binding_sectors"]
        assert capped["meta"]["binding_sectors_th"] == ["สุขภาพ"]
        assert capped["weights"]["XLV"] < plain["weights"]["XLV"]
        conc_capped = lookthrough.sector_concentration(capped["weights"])
        assert conc_capped["portfolio"]["healthcare"] <= 2 * 8.6 + 0.1

    def test_sector_data_failure_is_loud(self, monkeypatch):
        monkeypatch.setattr(lookthrough, "_fund_data", lambda s: (None, None, "down"))
        with pytest.raises(ValueError, match="เซกเตอร์"):
            rw.sector_cap_inputs(FIVE)

    def test_method_is_selectable_and_passes_the_flag(self, tmp_path, monkeypatch):
        from utils import config as cfg

        path = tmp_path / "config.json"
        path.write_text(json.dumps({"etf": {"tickers": FIVE}, "portfolio": {"weighting_method": "erc_sector_cap"}}))
        monkeypatch.setattr(cfg, "CONFIG_PATH", path)
        monkeypatch.setattr(cfg, "_cache", None)
        seen = []

        def _compute(t, cap=False):
            seen.append(cap)
            return {
                "weights": {x: 0.2 for x in t},
                "risk_share": {x: 0.2 for x in t},
                "meta": {"binding_sectors": ["healthcare"], "binding_sectors_th": ["สุขภาพ"], "cap_multiple": 2.0},
            }

        monkeypatch.setattr(rw, "compute_erc_weights", _compute)
        status = targets.get_target_weights_with_status(FIVE)
        assert seen == [True]
        assert status.method == "erc_sector_cap"
        assert any("สุขภาพ" in n for n in status.notes)

    def test_default_is_blend_and_never_the_sector_cap(self):
        # ค่าเริ่มต้นเป็น blend ตั้งแต่ 2026-10-05 (มติผู้ใช้) · เพดานเซกเตอร์ไม่ผ่าน backtest — ห้ามเป็นค่าเริ่มต้นเด็ดขาด
        assert targets.DEFAULT_WEIGHTING == "blend"
        assert targets.DEFAULT_WEIGHTING != "erc_sector_cap"


# ---------------------------------------------------------------- Discord
class TestMonthlyPlanContext:
    def test_method_notes_and_flags_reach_the_plan_message(self, monkeypatch):
        from analysis import ai_advisor

        monkeypatch.setattr(lookthrough, "_fund_data", _fund_data_stub)
        status = targets.TargetWeights(
            weights=TestSectorConcentration.ERC_TODAY, profile="moderate", method="erc"
        )
        monkeypatch.setattr(targets, "get_target_weights_with_status", lambda *a, **k: status)
        lines = ai_advisor._base_context_lines(["รอบนี้วัดเป็น USD"])
        text = "\n".join(lines)
        assert "ERC" in text
        assert "USD" in text
        assert "สุขภาพ" in text and "ตลาดโลก" in text

    def test_unmeasurable_concentration_is_said_out_loud(self, monkeypatch):
        from analysis import ai_advisor

        status = targets.TargetWeights(weights={"VOO": 1.0}, profile="moderate", method="erc")
        monkeypatch.setattr(targets, "get_target_weights_with_status", lambda *a, **k: status)
        # conftest ทำให้ _fund_data ออฟไลน์ → วัดตลาดโลกไม่ได้
        text = "\n".join(ai_advisor._base_context_lines([]))
        assert "วัดความกระจุกตัวของเซกเตอร์ไม่ได้" in text
        assert "ไม่ได้แปลว่าไม่กระจุกตัว" in text

    def test_summary_lines_carry_the_context(self):
        from analysis import ai_advisor

        lines = ai_advisor._allocation_summary_lines(
            {"VOO": {"amount_thb": 1000, "percent": 20.0, "target_percent": 21.4}},
            5000.0, 0.0, [], base_lines=["ℹ️ ฐานแบบ ERC", "⚠️ หุ้นกลุ่มสุขภาพ"],
        )
        assert "ℹ️ ฐานแบบ ERC" in lines and "⚠️ หุ้นกลุ่มสุขภาพ" in lines
