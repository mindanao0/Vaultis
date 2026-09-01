# -*- coding: utf-8 -*-
"""FIX_PLAN เฟส 4② — ทุกเลขที่ใช้ตัดสิน "ชนะไหม" ต้องมีช่วงความเชื่อมั่นกำกับ.

**บั๊กที่ปิด: การตัดสินด้วยตัวเลขจุดเดียว** `ab_backtest.py` ตัดสิน edge ด้วย
``final_value_tilt > final_value_plain`` ทั้งที่อนุกรมผลตอบแทนรายเดือนของทั้งสองแขน
อยู่ในมือแล้ว — เลขจุดเดียวไม่บอกว่าส่วนต่างนั้นใหญ่กว่าความผันผวนของตัวมันเองหรือเปล่า

วัดจริงด้วย harness นี้บนข้อมูล 16 ปี (2026-08-08):

    proxy  tilt−plain      +0.078%/ปี  SE 0.055  t=+1.41  CI95 [-0.03,+0.19]  n=178
    real   tilt−plain      -0.006%/ปี  SE 0.118  t=-0.05  CI95 [-0.24,+0.23]  n=69
    proxy  พอร์ต vs VOO    -0.140%/ปี  SE 0.666  t=-0.21  CI95 [-1.45,+1.18]
    real   พอร์ต vs VOO    -0.943%/ปี  SE 1.529  t=-0.62  CI95 [-3.99,+2.11]
                           → ต้องใช้ข้อมูลราว 119 ปี ถึงจะสรุปผลขนาดนี้ได้

**ทุกคู่แยกไม่ออกจากศูนย์** ด่าน edge เดิมบังเอิญตัดสินถูก (ไม่ผ่าน) ด้วยเหตุผลผิด
คือ "Sharpe แพ้" ทั้งที่ความจริงคือทั้งคู่อยู่ในกำแพงเสียงรบกวน

**"แยกไม่ออกจากศูนย์" ≠ "เท่ากัน"** — ข้อนี้คือหัวใจของไฟล์นี้ และเป็นบั๊กชนิดเดียวกับ
"ดึงไม่สำเร็จ ≠ ไม่มีข่าว" ที่ CLAUDE.md ห้ามไว้: ยุบ "ยังตอบไม่ได้" เข้ากับคำตอบ
เป็นการกุข้อสรุปจากความไม่รู้
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats as scipy_stats

from analysis.risk import (
    CONFIDENCE_LEVEL,
    MONTHS_PER_YEAR,
    _MDE_Z_SUM,
    mix_vs_benchmark_test,
    paired_diff_stats,
)

app = pytest.importorskip("dashboard.app")

from test_dashboard_round2_money import FakeSt  # noqa: E402
from test_shadow_symmetry import (  # noqa: E402
    _stub_screen,
    _voo_dividends,
    _voo_holdings,
    _voo_only_ledger,
)


@pytest.fixture()
def fake_st(monkeypatch) -> FakeSt:
    fake = FakeSt()
    monkeypatch.setattr(app, "st", fake)
    return fake


def _months(n: int, start: str = "2015-01-31") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="ME")


def _noise(
    n: int, sd: float = 0.04, seed: int = 11, mean: float = 0.008, start: str = "2015-01-31"
) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mean, sd, n), index=_months(n, start))


# =========================================================================== #
# paired_diff_stats — คณิตศาสตร์
# =========================================================================== #
class TestAnnualisedDifference:
    def test_ส่วนต่างต่อปีคือค่าเฉลี่ย_log_คูณ_12(self):
        """ใช้ log return เพื่อให้ "ต่อปี" เป็นส่วนต่างอัตราทบต้นพอดี ไม่ใช่การประมาณ."""
        base = _noise(120)
        other = (1.0 + base) * 1.002 - 1.0  # เร็วกว่า 0.2% ทุกเดือนแบบทบต้น
        stats = paired_diff_stats(other, base)
        expected = math.log(1.002) * MONTHS_PER_YEAR * 100.0
        assert stats["diff_annual_pct"] == pytest.approx(expected)
        assert stats["se_annual_pct"] == pytest.approx(0.0, abs=1e-9), (
            "ส่วนต่างคงที่ทุกเดือน = ไม่มีความผันผวนของส่วนต่างให้ทดสอบ"
        )

    def test_ทิศทางไม่สลับ(self):
        base = _noise(60)
        slower = (1.0 + base) * 0.997 - 1.0
        assert paired_diff_stats(slower, base)["diff_annual_pct"] < 0
        assert paired_diff_stats(base, slower)["diff_annual_pct"] > 0

    def test_อนุกรมเดียวกันได้ศูนย์เป๊ะ(self):
        base = _noise(48)
        stats = paired_diff_stats(base, base)
        assert stats["diff_annual_pct"] == pytest.approx(0.0)
        assert stats["se_annual_pct"] == pytest.approx(0.0)
        assert stats["t_stat"] is None, "SE=0 ⇒ t หารศูนย์ ห้ามคืนเลขปลอม"
        assert stats["distinguishable_from_zero"] is False


class TestConfidenceInterval:
    def test_ใช้_student_t_ไม่ใช่_1_96(self):
        a, b = _noise(30, seed=3), _noise(30, seed=4)
        stats = paired_diff_stats(a, b)
        n = stats["n_periods"]
        t_crit = float(scipy_stats.t.ppf(0.5 + CONFIDENCE_LEVEL / 2.0, n - 1))
        half = t_crit * stats["se_annual_pct"]
        assert stats["ci95_low_pct"] == pytest.approx(stats["diff_annual_pct"] - half)
        assert stats["ci95_high_pct"] == pytest.approx(stats["diff_annual_pct"] + half)
        assert t_crit > 1.96, "n=30 ⇒ t ต้องกว้างกว่า normal (ตัวคูณคงที่ 1.96 คือการโกงช่วง)"

    def test_ช่วงคร่อมส่วนต่างที่วัดได้เสมอ(self):
        stats = paired_diff_stats(_noise(80, seed=5), _noise(80, seed=6))
        assert stats["ci95_low_pct"] <= stats["diff_annual_pct"] <= stats["ci95_high_pct"]

    def test_t_stat_คือส่วนต่างหารด้วย_SE(self):
        stats = paired_diff_stats(_noise(90, seed=7), _noise(90, seed=8))
        assert stats["t_stat"] == pytest.approx(
            stats["diff_annual_pct"] / stats["se_annual_pct"]
        )


class TestDistinguishableIsNotEqual:
    """ด่านที่สำคัญที่สุด: "ยังตอบไม่ได้" ต้องไม่ถูกยุบเข้ากับ "ไม่ต่างกัน"."""

    def test_เสียงรบกวนล้วนต้องแยกไม่ออกทั้งที่ส่วนต่างไม่ใช่ศูนย์(self):
        stats = paired_diff_stats(_noise(60, seed=21), _noise(60, seed=22))
        assert stats["diff_annual_pct"] != 0.0, "ฉากต้องมีส่วนต่างที่ไม่ใช่ศูนย์"
        assert stats["distinguishable_from_zero"] is False
        assert stats["ci95_low_pct"] < 0 < stats["ci95_high_pct"], "CI ต้องคร่อมศูนย์"

    def test_ส่วนต่างที่ใหญ่จริงต้องแยกออกได้(self):
        base = _noise(120, seed=31)
        better = (1.0 + base) * 1.01 - 1.0
        stats = paired_diff_stats(better, base)
        assert stats["distinguishable_from_zero"] is True
        assert stats["ci95_low_pct"] > 0

    def test_แยกออกได้ทางลบก็ต้องรายงานว่าแยกออก(self):
        base = _noise(120, seed=41)
        worse = (1.0 + base) * 0.99 - 1.0
        stats = paired_diff_stats(worse, base)
        assert stats["distinguishable_from_zero"] is True
        assert stats["ci95_high_pct"] < 0, "มีนัยสำคัญทางลบ = หลักฐานว่าแย่กว่า ไม่ใช่ใบผ่าน"


class TestPowerNumbers:
    def test_MDE_คือ_z_รวมคูณ_SE(self):
        stats = paired_diff_stats(_noise(100, seed=51), _noise(100, seed=52))
        assert stats["mde_annual_pct"] == pytest.approx(_MDE_Z_SUM * stats["se_annual_pct"])
        assert _MDE_Z_SUM == pytest.approx(1.9600 + 0.8416, abs=1e-3)

    def test_จำนวนงวดที่ต้องมีเป็นสัดส่วนกำลังสองผกผัน(self):
        """SE ลดตาม 1/√n ⇒ ผลที่เล็กลงครึ่งหนึ่งต้องใช้ข้อมูล 4 เท่า."""
        stats = paired_diff_stats(_noise(120, seed=61), _noise(120, seed=62))
        n, mde, diff = stats["n_periods"], stats["mde_annual_pct"], abs(stats["diff_annual_pct"])
        assert stats["periods_needed"] == math.ceil(n * (mde / diff) ** 2)
        assert stats["years_needed"] == pytest.approx(stats["periods_needed"] / MONTHS_PER_YEAR)

    def test_ส่วนต่างศูนย์ไม่มีจำนวนปีที่ต้องมี(self):
        base = _noise(50, seed=71)
        stats = paired_diff_stats(base, base)
        assert stats["periods_needed"] is None and stats["years_needed"] is None


class TestAlignmentAndFailLoud:
    def test_เทียบเฉพาะงวดที่ทับกันและรายงานช่วงจริง(self):
        a = _noise(60, start="2015-01-31")
        b = _noise(60, start="2017-01-31", seed=99)
        stats = paired_diff_stats(a, b)
        assert stats["n_periods"] == 36, "ต้องใช้เฉพาะเดือนที่ทั้งสองฝั่งมีข้อมูล"
        assert stats["overlap_start"].startswith("2017-01")
        assert stats["overlap_end"].startswith("2019-12")

    def test_งวดทับกันน้อยเกินไปต้องล้มดัง(self):
        idx = _months(1)
        with pytest.raises(ValueError, match="ทับกัน"):
            paired_diff_stats(pd.Series([0.01], index=idx), pd.Series([0.02], index=idx))

    def test_ไม่ทับกันเลยต้องล้มดัง(self):
        a = _noise(12, start="2015-01-31")
        b = _noise(12, start="2020-01-31")
        with pytest.raises(ValueError):
            paired_diff_stats(a, b)

    def test_ผลตอบแทนต่ำกว่าลบ_100_ต้องล้มดังไม่ใช่_NaN(self):
        idx = _months(4)
        bad = pd.Series([0.01, -1.2, 0.02, 0.0], index=idx)
        ok = pd.Series([0.01, 0.01, 0.01, 0.01], index=idx)
        with pytest.raises(ValueError, match="−100%"):
            paired_diff_stats(bad, ok)

    def test_ค่าที่อ่านไม่ออกถูกตัดออกไม่ใช่กลายเป็นศูนย์(self):
        idx = _months(6)
        a = pd.Series([0.01, None, 0.02, 0.01, 0.0, 0.01], index=idx)
        b = pd.Series([0.00, 0.01, 0.01, 0.00, 0.0, 0.00], index=idx)
        assert paired_diff_stats(a, b)["n_periods"] == 5


# =========================================================================== #
# mix_vs_benchmark_test
# =========================================================================== #
def _price_frame(spec: dict[str, float], periods: int = 400, start: str = "2015-01-01"):
    """ราคาที่โตแบบทบต้นคงที่ต่อวันทำการ — ผลตอบแทนรายเดือนจึงคาดเดาได้."""
    index = pd.bdate_range(start, periods=periods)
    return pd.DataFrame(
        {t: 100.0 * (1.0 + g) ** np.arange(periods) for t, g in spec.items()}, index=index
    )


class TestMixVsBenchmark:
    def test_ส่วนผสมที่เป็น_benchmark_ล้วนไม่มีส่วนต่างให้ทดสอบ(self):
        prices = _price_frame({"VOO": 0.0004})
        stats = mix_vs_benchmark_test(prices, {"VOO": 1000.0})
        assert stats["diff_annual_pct"] == pytest.approx(0.0)
        assert stats["se_annual_pct"] == pytest.approx(0.0)
        assert stats["distinguishable_from_zero"] is False

    def test_ส่วนผสมที่โตเร็วกว่าอย่างสม่ำเสมอต้องแยกออกได้(self):
        prices = _price_frame({"VOO": 0.0002, "QQQM": 0.0008})
        stats = mix_vs_benchmark_test(prices, {"QQQM": 1000.0})
        assert stats["diff_annual_pct"] > 0
        assert stats["distinguishable_from_zero"] is True

    def test_น้ำหนักรับมูลค่าดิบแล้ว_normalize_ให้เอง(self):
        prices = _price_frame({"VOO": 0.0002, "QQQM": 0.0008})
        raw = mix_vs_benchmark_test(prices, {"VOO": 3000.0, "QQQM": 1000.0})
        frac = mix_vs_benchmark_test(prices, {"VOO": 0.75, "QQQM": 0.25})
        assert raw["diff_annual_pct"] == pytest.approx(frac["diff_annual_pct"])

    def test_ใช้เฉพาะเดือนที่ทุกกองมีราคาพร้อมกัน(self):
        """กองที่ลิสต์ทีหลังย่นหน้าต่างลง — ป้ายต้องบอกช่วงจริง (เหมือน QQQM ปี 2020)."""
        prices = _price_frame({"VOO": 0.0003, "LATE": 0.0005})
        prices.loc[prices.index[:200], "LATE"] = float("nan")
        stats = mix_vs_benchmark_test(prices, {"VOO": 500.0, "LATE": 500.0})
        full = mix_vs_benchmark_test(prices[["VOO"]], {"VOO": 1.0})
        assert stats["n_periods"] < full["n_periods"]
        assert stats["overlap_start"] > full["overlap_start"]

    def test_ไม่มี_benchmark_ต้องล้มดัง(self):
        prices = _price_frame({"SCHD": 0.0003})
        with pytest.raises(ValueError, match="benchmark"):
            mix_vs_benchmark_test(prices, {"SCHD": 1.0})

    def test_ไม่มี_ticker_ที่มีทั้งน้ำหนักและราคาต้องล้มดัง(self):
        prices = _price_frame({"VOO": 0.0003})
        with pytest.raises(ValueError, match="ticker"):
            mix_vs_benchmark_test(prices, {"NOPE": 1.0})

    def test_ข้อมูลสั้นเกินไปต้องล้มดังไม่ใช่ตอบมั่ว(self):
        prices = _price_frame({"VOO": 0.0003, "QQQM": 0.0004}, periods=20)
        with pytest.raises(ValueError, match="เดือน"):
            mix_vs_benchmark_test(prices, {"QQQM": 1.0})


# =========================================================================== #
# ด่าน edge ของ harness
# =========================================================================== #
def _ab_prices() -> pd.DataFrame:
    """ราคาสังเคราะห์ 6 ปีของกองที่ window ``real`` ต้องการ (ไม่ยิง network)."""
    return _price_frame(
        {"VOO": 0.00035, "SCHD": 0.00030, "QQQM": 0.00040, "XLV": 0.00025, "GLDM": 0.00020},
        periods=1600,
        start="2020-11-02",
    )


class TestEdgeGateRequiresSignificance:
    TARGETS = {"VOO": 0.35, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.10}

    @pytest.fixture(scope="class")
    @classmethod
    def window(cls):
        from portfolio.ab_backtest import run_ab_backtest

        return run_ab_backtest(
            {"real": _ab_prices()}, monthly_amount=5000.0, target_weights=cls.TARGETS
        )["real"]

    def test_verdict_มีช่องผลทดสอบและ_overall_ต้องการทั้งสามข้อ(self, window):
        v = window["tilt_beats_plain"]
        assert set(v) == {"by_value", "by_sharpe", "by_paired_test", "overall"}
        assert v["overall"] == (v["by_value"] and v["by_sharpe"] and v["by_paired_test"])

    def test_ผลทดสอบเดินทางออกมาพร้อมตัวเลขครบ(self, window):
        stats = window["paired_tilt_vs_plain"]["stats"]
        assert window["paired_tilt_vs_plain"]["error"] is None
        for key in ("diff_annual_pct", "se_annual_pct", "ci95_low_pct", "ci95_high_pct", "n_periods"):
            assert key in stats
        assert window["paired_plain_vs_voo"]["stats"] is not None, "แขน VOO ต้องถูกทดสอบด้วย"

    def test_สรุปไทยพูดถึงนัยสำคัญไม่ใช่แค่ตัวเลขปลายทาง(self, window):
        summary = window["summary_th"]
        assert "paired t-test" in summary, "บรรทัดคำตัดสินต้องบอกว่าผ่าน/ไม่ผ่านการทดสอบด้วย"
        # ต้องมี **ทั้งสองคู่** — เทียบแค่ "มีคำว่า CI95 อยู่ที่ไหนก็ได้" ไม่พอ เพราะบรรทัด
        # หนึ่งหายไปแล้วอีกบรรทัดยังทำให้เงื่อนไขเป็นจริง (จับได้ตอนพิสูจน์ด้วย mutation)
        assert "นัยสำคัญ tilt vs plain:" in summary
        assert "พอร์ตตามเป้า vs VOO:" in summary
        assert summary.count("CI95") >= 2, "ทั้งสองคู่ต้องมีช่วงความเชื่อมั่นกำกับ"

    def test_sharpe_เก็บค่าเต็มไม่ปัด(self, window):
        """ค่านี้ถูกเอาไป **เทียบ** ระหว่างแขน การปัดทำให้แขนที่แพ้ได้ 'เสมอ' ฟรี ๆ."""
        for arm in ("plain", "tilt"):
            sharpe = window["arms"][arm]["sharpe"]
            assert sharpe is not None
            assert sharpe != round(sharpe, 2), (
                f"{arm}: sharpe={sharpe!r} ดูเหมือนถูกปัดไว้แล้ว — ต้องเก็บค่าเต็ม"
            )


class TestScreenNeverAnswersWithOneNumber:
    """หน้าจอ "ชนะ VOO ไหม" ต้องไม่ปล่อยตัวเลขจุดเดียวยืนอยู่คนเดียว."""

    @staticmethod
    def _priced(weights: dict[str, float]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"Ticker": t, "Current Value (USD)": v, "Current Value (THB)": v * 35.0, "Price OK": True}
                for t, v in weights.items()
            ]
        )

    @staticmethod
    def _random_walk(tickers: tuple[str, ...], periods: int = 1500, seed: int = 5) -> pd.DataFrame:
        """เดินสุ่มที่มี drift เท่ากัน — ส่วนต่างจึงเป็นเสียงรบกวนล้วน."""
        rng = np.random.default_rng(seed)
        index = pd.bdate_range("2018-01-01", periods=periods)
        data = {}
        for i, t in enumerate(tickers):
            steps = rng.normal(0.0003, 0.011, periods)
            data[t] = 100.0 * np.cumprod(1.0 + steps)
        return pd.DataFrame(data, index=index)

    def test_ส่วนผสมที่เป็น_VOO_ล้วนบอกว่าไม่มีอะไรให้ทดสอบ(self, fake_st):
        app._render_mix_vs_voo_significance(
            self._priced({"VOO": 3000.0}), _price_frame({"VOO": 0.0003})
        )
        text = fake_st.all_text()
        assert "VOO ล้วน" in text and "เท่ากันทุกเดือนโดยนิยาม" in text

    def test_เสียงรบกวนต้องพิมพ์ว่าแยกไม่ออกจากศูนย์และห้ามอ่านว่าเท่ากัน(self, fake_st):
        prices = self._random_walk(("VOO", "SCHD", "QQQM"))
        app._render_mix_vs_voo_significance(
            self._priced({"VOO": 2000.0, "SCHD": 1000.0, "QQQM": 1000.0}), prices
        )
        warnings = "\n".join(fake_st.texts("warning"))
        assert "แยกไม่ออกจากศูนย์" in warnings
        assert "ห้ามอ่านว่า 'เท่ากัน'" in warnings, (
            "ยุบ 'ยังตอบไม่ได้' เข้ากับ 'เท่ากัน' คือการกุข้อสรุปจากความไม่รู้"
        )
        assert "CI95" in warnings and "%/ปี" in warnings

    def test_ต้องบอกว่าสามช่องข้างบนเป็นผลที่เกิดขึ้นแล้วเส้นทางเดียว(self, fake_st):
        prices = self._random_walk(("VOO", "SCHD"))
        app._render_mix_vs_voo_significance(self._priced({"VOO": 2000.0, "SCHD": 1000.0}), prices)
        assert "เกิดขึ้นแล้ว" in fake_st.all_text()

    def test_ส่วนต่างที่ใหญ่จริงรายงานว่ามีนัยสำคัญ(self, fake_st):
        prices = _price_frame({"VOO": 0.0001, "QQQM": 0.0009}, periods=1500)
        app._render_mix_vs_voo_significance(self._priced({"QQQM": 3000.0}), prices)
        infos = "\n".join(fake_st.texts("info"))
        assert "มีนัยสำคัญ" in infos
        assert "ไม่ใช่การรับประกันอนาคต" in infos

    def test_ทดสอบไม่ได้ต้องบอกเหตุผลไม่ใช่เงียบ(self, fake_st):
        short = _price_frame({"VOO": 0.0003, "QQQM": 0.0004}, periods=20)
        app._render_mix_vs_voo_significance(self._priced({"QQQM": 100.0}), short)
        assert any("ยังทดสอบนัยสำคัญ" in t for t in fake_st.texts("caption"))

    def test_กองที่ไม่มีราคาไม่ทำให้ทั้งกล่องพัง(self, fake_st):
        prices = self._random_walk(("VOO", "SCHD"))
        app._render_mix_vs_voo_significance(
            self._priced({"VOO": 2000.0, "GLDM": 500.0}), prices
        )
        assert fake_st.all_text(), "กองที่ไม่มีคอลัมน์ราคาต้องถูกข้าม ไม่ใช่ทำให้เงียบทั้งกล่อง"

    def test_กล่องเทียบต้องเรียกตัวนี้จริง(self, fake_st, monkeypatch):
        """กันไม่ให้ใครถอดการเรียกออกแล้วเทสต์ยังเขียว."""
        called: list[tuple] = []
        monkeypatch.setattr(
            app,
            "_render_mix_vs_voo_significance",
            lambda priced, prices: called.append((priced, prices)),
        )
        _stub_screen(monkeypatch, _voo_only_ledger(), _voo_dividends())
        app._render_benchmark_section(_voo_holdings())
        assert called, "_render_benchmark_section ต้องเรียกด่านนัยสำคัญเสมอ"


class TestEdgeVerdictAllCombinations:
    """กติกาของด่านต้องถูกตรึง **ทุกกรณี** ไม่ใช่เฉพาะกรณีที่ฉากจำลองพาไปถึง."""

    @staticmethod
    def _paired(diff: float, significant: bool) -> dict:
        return {
            "stats": {"diff_annual_pct": diff, "distinguishable_from_zero": significant},
            "error": None,
        }

    @pytest.mark.parametrize("by_value", [True, False])
    @pytest.mark.parametrize("by_sharpe", [True, False])
    @pytest.mark.parametrize("significant,diff", [(True, 1.0), (True, -1.0), (False, 1.0), (False, -1.0)])
    def test_ต้องผ่านครบสามข้อเท่านั้นจึงผ่านด่าน(self, by_value, by_sharpe, significant, diff):
        from portfolio.ab_backtest import edge_verdict

        v = edge_verdict(by_value, by_sharpe, self._paired(diff, significant))
        expect_test = significant and diff > 0
        assert v["by_paired_test"] is expect_test
        assert v["overall"] is (by_value and by_sharpe and expect_test)

    def test_ผ่านสองข้อแรกแต่แยกไม่ออกจากศูนย์ต้องไม่ผ่านด่าน(self):
        """กรณีที่ mutation ใช้แอบผ่าน: ``overall = by_value and by_sharpe`` จะตอบผ่าน."""
        from portfolio.ab_backtest import edge_verdict

        v = edge_verdict(True, True, self._paired(0.08, False))
        assert v["by_value"] and v["by_sharpe"]
        assert v["by_paired_test"] is False
        assert v["overall"] is False, "ส่วนต่างในกำแพงเสียงรบกวนต้องไม่ได้ใบผ่าน"

    def test_ผ่านครบสามข้อจึงผ่าน(self):
        from portfolio.ab_backtest import edge_verdict

        assert edge_verdict(True, True, self._paired(0.5, True))["overall"] is True

    @pytest.mark.parametrize("paired", [None, {"stats": None, "error": "สั้นเกินไป"}, {}])
    def test_ไม่มีผลทดสอบต้องปิดตัวเอง(self, paired):
        from portfolio.ab_backtest import edge_verdict

        v = edge_verdict(True, True, paired)
        assert v["by_paired_test"] is False and v["overall"] is False


class TestEdgeGateFailsClosed:
    def test_ทดสอบไม่ได้ต้องไม่ผ่านด่าน(self, monkeypatch):
        """ทดสอบไม่ได้ ≠ ผ่าน — ยืนยัน edge ไม่ได้ ต้องปิดตัวเอง."""
        from portfolio import ab_backtest as ab

        monkeypatch.setattr(
            ab, "paired_diff_stats", lambda *a, **k: (_ for _ in ()).throw(ValueError("สั้นเกินไป"))
        )
        window = ab.run_ab_backtest(
            {"real": _ab_prices()},
            monthly_amount=5000.0,
            target_weights=TestEdgeGateRequiresSignificance.TARGETS,
        )["real"]
        assert window["paired_tilt_vs_plain"]["stats"] is None
        assert window["paired_tilt_vs_plain"]["error"] == "สั้นเกินไป"
        assert window["tilt_beats_plain"]["by_paired_test"] is False
        assert window["tilt_beats_plain"]["overall"] is False
        assert "ทดสอบไม่ได้" in window["summary_th"]

    def test_มีนัยสำคัญแต่ติดลบต้องไม่ผ่านด่าน(self, monkeypatch):
        """หลักฐานว่า tilt **แย่กว่า** plain ต้องไม่ถูกอ่านเป็นใบผ่าน."""
        from portfolio import ab_backtest as ab

        monkeypatch.setattr(
            ab,
            "paired_diff_stats",
            lambda *a, **k: {
                "diff_annual_pct": -1.5,
                "se_annual_pct": 0.2,
                "distinguishable_from_zero": True,
                "ci95_low_pct": -1.9,
                "ci95_high_pct": -1.1,
                "n_periods": 60,
                "t_stat": -7.5,
                "mde_annual_pct": 0.56,
                "periods_needed": None,
                "years_needed": None,
                "label_a": "tilt",
                "label_b": "plain",
                "overlap_start": "2021-01-31",
                "overlap_end": "2026-01-31",
            },
        )
        window = ab.run_ab_backtest(
            {"real": _ab_prices()},
            monthly_amount=5000.0,
            target_weights=TestEdgeGateRequiresSignificance.TARGETS,
        )["real"]
        assert window["tilt_beats_plain"]["by_paired_test"] is False
        assert window["tilt_beats_plain"]["overall"] is False
