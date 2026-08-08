# -*- coding: utf-8 -*-
"""ทดสอบ ``analysis.risk.portfolio_return_stats()`` — μ สองตัวที่ห้ามสลับกัน + ป้ายช่วงข้อมูลที่ห้ามโกหก.

ที่มา (AUDIT_ROUND2_2026-08-07 · FIX_PLAN เฟส 4①) — ตัวเลขก้อนนี้ป้อน Monte Carlo
ของหน้า Goals ซึ่งเป็นหน้าที่บอกผู้ใช้ว่า "ต้องออมเดือนละเท่าไหร่ถึงจะถึงเป้า" สองบั๊กที่
ฟังก์ชันนี้ปิด และไฟล์นี้มีหน้าที่กันไม่ให้กลับมา:

1. **เดิมมีค่าเฉลี่ยเลขคณิตตัวเดียว แล้วปลายทางเอาไปทบต้น** — ค่าเฉลี่ยเลขคณิตสูงกว่า
   อัตราทบต้นจริงราว σ²/2 ต่อปี (σ 15% ⇒ เกินจริง ~1.1 จุด/ปี) ⇒ ระบบบอกให้ผู้ใช้
   ออมน้อยกว่าที่ต้องออมจริง เทสต์ที่แยกสองสูตรออกจากกันได้เด็ดขาดที่สุดคือ
   อนุกรม +10%/−10% สลับกัน: เลขคณิต = 0 พอดี ขณะที่ทบต้น = 0.99^126 − 1 ≈ −71.8%
   ถ้าใครทำให้ ``mu_geometric`` กลายเป็นค่าเดียวกับ ``mu_arithmetic`` เคสนั้นต้องแดงทันที
2. **ป้ายบอกช่วงข้อมูลโกหก** — ``dropna()`` ตัดอนุกรมเหลือ "ประวัติร่วม" ที่สั้นที่สุดของ
   พอร์ต (QQQM เพิ่งลิสต์ปี 2020 ⇒ ขอมา 10 ปี ได้ใช้จริง ~5.8 ปี) แต่ป้ายยังเขียนว่า
   "ย้อนหลัง 10 ปี" ⇒ ``window_days`` (หลังตัด) กับ ``window_days_available`` (ก่อนตัด)
   ต้องเป็นคนละเลข และ ``window_start`` ต้องเป็นวันของ**ผลตอบแทน**แถวแรกที่ใช้จริง
   ไม่ใช่วันแรกของ ``price_df``

ทุกเคสสร้าง DataFrame เอง — ไม่แตะเครือข่าย ไม่แตะไฟล์ข้อมูลจริงของผู้ใช้
"""

import json
import math

import numpy as np
import pandas as pd
import pytest

from analysis.risk import (
    DEFAULT_RISK_FREE_RATE,
    TRADING_DAYS_PER_YEAR,
    calculate_sharpe_ratio,
    portfolio_mu_sigma,
    portfolio_return_stats,
)


def _frame(columns: dict[str, object], start: str = "2024-01-01") -> pd.DataFrame:
    """ตารางราคาปิดจำลอง (index = วันทำการ) — รูปเดียวกับที่ ``fetch_adjusted_close_data`` คืน."""
    length = len(next(iter(columns.values())))
    return pd.DataFrame(
        {k: np.asarray(v, dtype=float) for k, v in columns.items()},
        index=pd.date_range(start, periods=length, freq="B"),
    )


def _prices_from_returns(returns: list[float], start_price: float = 100.0) -> list[float]:
    """ราคาที่ให้ผลตอบแทนรายวัน **ตามรายการที่กำหนดพอดี** — เพื่อให้คำตอบคำนวณด้วยมือได้.

    คืนราคา ``len(returns) + 1`` จุด (แถวแรกไม่มีผลตอบแทน) ``pct_change`` ของอนุกรมนี้
    คืนค่าเดิมกลับมาที่ระดับ 1e-15 จึงเทียบกับสูตรมือได้ด้วย ``pytest.approx``
    """
    prices = [float(start_price)]
    for r in returns:
        prices.append(prices[-1] * (1.0 + r))
    return prices


def _random_walk(n: int, mu_daily: float, sigma_daily: float, seed: int) -> np.ndarray:
    """ราคาสุ่มแบบกำหนดเมล็ดไว้ (deterministic) — ใช้เมื่อต้องการความผันผวนสมจริง."""
    rng = np.random.default_rng(seed)
    return 100.0 * np.cumprod(1.0 + rng.normal(mu_daily, sigma_daily, n))


# ราคาที่ให้ผลตอบแทน +10%, −10% สลับกัน — เลขคณิต = 0 พอดี, ทบต้น = 0.99^126 − 1
_ALTERNATING_10 = _prices_from_returns([0.1, -0.1, 0.1, -0.1])
# อัตราทบต้นต่อปีของอนุกรมข้างบน คำนวณด้วยมือ: prod(1+r)^(252/n) − 1 = (1.1·0.9)^126 − 1
_ALTERNATING_10_CAGR = 0.99**126 - 1.0  # ≈ −0.718139


class TestGeometricVsArithmetic:
    """สองสูตรต้องเป็นคนละเลข และส่วนต่างต้องเป็น "vol drag" (σ²/2) ไม่ใช่ค่าคงที่เดา ๆ."""

    def test_alternating_10pct_separates_the_two_formulas(self):
        """เคสชี้ขาด: เลขคณิต = 0 แต่ทบต้นติดลบหนัก — สลับสองสูตรกันเมื่อไหร่เห็นทันที.

        ผลตอบแทน [+10%, −10%, +10%, −10%] ⇒ ค่าเฉลี่ยเลขคณิต = 0 ⇒ μ = 0.00%
        แต่เงินจริงเหลือ 98.01 จาก 100 (ทุกคู่วันคูณกันได้ 0.99) ⇒ CAGR = 0.99^126 − 1
        """
        stats = portfolio_return_stats(_frame({"A": _ALTERNATING_10}), {"A": 1.0})

        assert stats["mu_arithmetic"] == pytest.approx(0.0, abs=1e-12)
        assert stats["mu_geometric"] == pytest.approx(_ALTERNATING_10_CAGR, rel=1e-9)
        assert stats["mu_geometric"] == pytest.approx(-0.718139, abs=1e-5)
        # ห้ามยุบเป็นตัวเดียวกัน: ส่วนต่างที่นี่คือ 71.8 จุดเปอร์เซ็นต์ต่อปี
        assert stats["mu_geometric"] < stats["mu_arithmetic"] - 0.5

    def test_geometric_matches_the_documented_product_formula(self):
        """``mu_geometric`` = ``prod(1+r)^(252/n) − 1`` จริง ๆ (สูตรในเอกสารของฟังก์ชัน).

        คำนวณด้วยผลคูณ+ยกกำลัง ซึ่งเป็นคนละทางเดินกับ ``expm1(mean(log1p(...)))``
        ที่โค้ดใช้ — ตรงกันแปลว่าไม่ได้แค่ทวนสูตรตัวเอง
        """
        daily = [0.012, -0.004, 0.007, -0.011, 0.019, 0.003, -0.008, 0.014]
        stats = portfolio_return_stats(_frame({"A": _prices_from_returns(daily)}), {"A": 1.0})

        expected_cagr = float(np.prod([1.0 + r for r in daily])) ** (
            TRADING_DAYS_PER_YEAR / len(daily)
        ) - 1.0
        assert stats["mu_geometric"] == pytest.approx(expected_cagr, rel=1e-9)
        expected_arith = float(np.mean(daily)) * TRADING_DAYS_PER_YEAR
        assert stats["mu_arithmetic"] == pytest.approx(expected_arith, rel=1e-9)

    def test_gap_between_the_two_is_the_variance_drag(self):
        """อนุกรมผันผวนสมจริง (μ ~7.5%, σ ~17.5% ต่อปี — ระดับ VOO): ทบต้น < เลขคณิต เสมอ.

        ความสัมพันธ์ที่แม่นอยู่ใน log space — ``mu_arithmetic − ln(1+mu_geometric)``
        ≈ σ²/2 (พจน์อันดับสองของ ``log1p``) นี่คือ "vol drag" ที่หายไปตอนมีค่าเฉลี่ย
        เลขคณิตตัวเดียว  ส่วนบรรทัดสุดท้ายคือผลกระทบที่เป็นเงินจริง: เอาเลขคณิตไปทบต้น
        20 ปีทำให้เงินปลายทางที่โชว์สูงเกินจริงเกิน 15% ⇒ ผู้ใช้ถูกบอกให้ออมน้อยไป
        """
        prices = _random_walk(1260, mu_daily=0.0003, sigma_daily=0.011, seed=20260808)
        stats = portfolio_return_stats(_frame({"A": prices}), {"A": 1.0})

        assert stats["mu_geometric"] < stats["mu_arithmetic"]
        drag = stats["mu_arithmetic"] - math.log1p(float(stats["mu_geometric"]))
        assert drag == pytest.approx(float(stats["sigma"]) ** 2 / 2.0, rel=0.1)
        # σ ≈ 17.5%/ปี ⇒ vol drag ≈ 1.5 จุด/ปี — ไม่ใช่เศษปัดเศษ
        assert float(stats["sigma"]) == pytest.approx(0.175, abs=0.02)
        assert stats["mu_arithmetic"] - stats["mu_geometric"] > 0.005

        overstatement = (1.0 + float(stats["mu_arithmetic"])) ** 20 / (
            1.0 + float(stats["mu_geometric"])
        ) ** 20
        assert overstatement > 1.15

    def test_near_zero_volatility_makes_the_two_almost_equal(self):
        """σ → 0 (โตเกือบคงที่ทุกวัน) ⇒ ไม่มี vol drag ⇒ สองค่าเกือบเท่ากัน.

        ผลตอบแทนสลับ 0.002%/0.001% ต่อวัน (σ เล็กมากแต่ไม่ใช่ 0 เพราะ σ = 0 ต้อง raise)
        ⇒ μ เลขคณิต = 0.000015 × 252 = 0.378%/ปี ส่วนทบต้น = (1.00002·1.00001)^126 − 1
        ต่างกันไม่ถึง 0.01 จุด/ปี — เทียบกับเคสผันผวนข้างบนที่ต่างกันเกิน 0.5 จุด/ปี
        """
        daily = [0.00002, 0.00001] * 6
        stats = portfolio_return_stats(_frame({"A": _prices_from_returns(daily)}), {"A": 1.0})

        assert stats["mu_arithmetic"] == pytest.approx(0.000015 * TRADING_DAYS_PER_YEAR, rel=1e-9)
        assert stats["mu_geometric"] == pytest.approx((1.00002 * 1.00001) ** 126 - 1.0, rel=1e-9)
        assert stats["mu_geometric"] == pytest.approx(stats["mu_arithmetic"], abs=1e-4)
        drag = stats["mu_arithmetic"] - math.log1p(float(stats["mu_geometric"]))
        assert drag < 1e-6

    @pytest.mark.parametrize(
        "prices",
        [
            pytest.param(_ALTERNATING_10, id="alternating"),
            pytest.param(_prices_from_returns([0.00002, 0.00001] * 6), id="near-flat"),
            pytest.param(_random_walk(600, 0.0006, 0.013, seed=77), id="random-walk"),
            pytest.param(_random_walk(600, -0.0009, 0.02, seed=99), id="losing-walk"),
        ],
    )
    def test_log_of_geometric_never_exceeds_arithmetic(self, prices):
        """ค่าคงที่เชิงคณิตศาสตร์ (Jensen): ``ln(1+CAGR) ≤ μ เลขคณิต`` เสมอ ทุกอนุกรม.

        เพราะ ``log1p(x) ≤ x`` ⇒ ค่าเฉลี่ยของ log ผลตอบแทนไม่มีทางเกินค่าเฉลี่ยเลขคณิต
        ถ้าเคสไหนละเมิดข้อนี้ แปลว่า annualize คนละฐาน (เช่นตัวหนึ่ง ×252 อีกตัว ×12)
        """
        stats = portfolio_return_stats(_frame({"A": prices}), {"A": 1.0})
        assert math.log1p(float(stats["mu_geometric"])) <= float(stats["mu_arithmetic"]) + 1e-12


class TestWindowLabels:
    """ป้ายช่วงข้อมูล: "ขอมา 10 ปี" ≠ "ได้ใช้จริง 10 ปี" — สองเลขนี้ห้ามยุบรวมกัน."""

    # A มีราคาครบ 10 วัน (สลับ ±10%) ส่วน B (กองที่เพิ่งลิสต์) มีราคาแค่ 5 วันหลัง
    # และขยับ "ทางเดียวกับ A" เพื่อไม่ให้ผลตอบแทนพอร์ตหักล้างกันจนกลายเป็น σ = 0
    _A_FULL = _prices_from_returns([0.1, -0.1, 0.1, -0.1, 0.1, -0.1, 0.1, -0.1, 0.1])
    _B_LATE = [float("nan")] * 5 + _prices_from_returns([-0.1, 0.1, -0.1, 0.1], 50.0)

    def _staggered(self) -> pd.DataFrame:
        return _frame({"A": self._A_FULL, "B": self._B_LATE})

    def test_days_used_and_days_available_are_different_numbers(self):
        """นับด้วยมือได้: ผลตอบแทนที่ดึงมาได้ 9 แถว แต่ใช้จริงแค่ 4 แถว.

        แถว d0 ไม่มีผลตอบแทน (ไม่มีวันก่อนหน้า) ⇒ เหลือ d1..d9 = 9 แถว = ``window_days_available``
        แต่ B ยังไม่มีราคาถึง d4 และ d5 ก็ยังคิดผลตอบแทนไม่ได้ (วันก่อนหน้าเป็น NaN)
        ⇒ แถวที่ **มีครบทั้งสองกอง** คือ d6..d9 = 4 แถว = ``window_days``
        """
        stats = portfolio_return_stats(self._staggered(), {"A": 0.5, "B": 0.5})

        assert stats["window_days_available"] == 9
        assert stats["window_days"] == 4
        assert stats["window_days"] < stats["window_days_available"]

    def test_window_start_is_the_first_usable_return_day_not_the_first_price_day(self):
        """``window_start`` ต้องเป็นวันของผลตอบแทนแถวแรกที่ใช้จริง — นี่คือตัวกันป้าย "10 ปี".

        ถ้าใครกลับไปรายงานขอบของ ``price_df`` ป้ายจะบอกช่วงที่ **ไม่ได้ใช้คำนวณ** ซึ่งคือ
        การกุข้อมูลชนิดหนึ่ง (AUDIT_ROUND2_2026-08-07)
        """
        df = self._staggered()
        stats = portfolio_return_stats(df, {"A": 0.5, "B": 0.5})

        assert stats["window_start"] == "2024-01-09"          # = df.index[6]
        assert pd.Timestamp(stats["window_start"]) == df.index[6]
        assert pd.Timestamp(stats["window_end"]) == df.index[-1]
        # ห้ามเป็นวันแรกของราคา และห้ามเป็นวันแรกของผลตอบแทนที่ "ดึงมาได้"
        assert pd.Timestamp(stats["window_start"]) != df.index[0]
        assert pd.Timestamp(stats["window_start"]) != df.index[1]

    def test_stats_of_the_staggered_window_use_only_the_common_history(self):
        """μ/σ ต้องคำนวณจาก 4 แถวที่ใช้จริงเท่านั้น ไม่ใช่ 9 แถวที่ดึงมาได้."""
        stats = portfolio_return_stats(self._staggered(), {"A": 0.5, "B": 0.5})

        # ผลตอบแทนพอร์ต d6..d9 = [−10%, +10%, −10%, +10%] (สองกองขยับทางเดียวกัน)
        assert stats["mu_arithmetic"] == pytest.approx(0.0, abs=1e-12)
        assert stats["mu_geometric"] == pytest.approx(_ALTERNATING_10_CAGR, rel=1e-9)
        assert stats["sigma"] == pytest.approx(0.1154700538379252 * np.sqrt(252), rel=1e-9)
        assert stats["tickers"] == ["A", "B"]

    def test_full_history_leaves_no_gap_between_the_two_counts(self):
        """ทุกกองมีประวัติครบ ⇒ สองเลขต้องเท่ากัน (ไม่ใช่ต่างกันเสมอ)."""
        stats = portfolio_return_stats(_frame({"A": _ALTERNATING_10}), {"A": 1.0})

        assert stats["window_days"] == 4
        assert stats["window_days_available"] == 4
        assert stats["window_start"] == "2024-01-02"
        assert stats["window_end"] == "2024-01-05"

    def test_short_listed_fund_shrinks_the_window_the_way_qqqm_did(self):
        """เคสจริงย่อส่วน: ขอมา ~4 ปี แต่กองใหม่ทำให้ใช้ได้จริงไม่ถึง 40% ของนั้น."""
        voo = _random_walk(1000, 0.0004, 0.009, seed=4001)
        qqqm = _random_walk(1000, 0.0005, 0.012, seed=4002)
        qqqm[:600] = np.nan  # เพิ่งลิสต์กลางทาง
        df = _frame({"VOO": voo, "QQQM": qqqm})

        stats = portfolio_return_stats(df, {"VOO": 60.0, "QQQM": 40.0})

        assert stats["window_days_available"] == 999
        assert stats["window_days"] == 399
        assert pd.Timestamp(stats["window_start"]) == df.index[601]
        # ป้ายที่บอก "ย้อนหลัง N ปี" ตามที่ขอมา จะเกินจริงเกิน 2.5 เท่า
        assert stats["window_years"] < 0.45 * (stats["window_days_available"] / TRADING_DAYS_PER_YEAR)

    def test_window_years_is_days_divided_by_trading_days_per_year(self):
        stats = portfolio_return_stats(_frame({"A": _ALTERNATING_10}), {"A": 1.0})

        assert TRADING_DAYS_PER_YEAR == 252
        assert stats["window_years"] == stats["window_days"] / TRADING_DAYS_PER_YEAR
        assert stats["window_years"] == pytest.approx(4 / 252)

    def test_stats_are_json_serializable_for_the_api(self):
        """ค่านี้ถูกส่งออก ``/api/goals`` ⇒ ต้อง dump เป็น JSON ได้โดยไม่ต้องแปลงอะไรก่อน.

        โดยเฉพาะ ``window_start``/``window_end`` ต้องเป็น **สตริง** ไม่ใช่ ``pd.Timestamp``
        (Timestamp ทำให้ ``json.dumps`` โยน TypeError = หน้า Goals พังทั้งหน้า)
        """
        stats = portfolio_return_stats(self._staggered(), {"A": 0.5, "B": 0.5})

        assert isinstance(stats["window_start"], str)
        assert isinstance(stats["window_end"], str)
        assert isinstance(stats["window_days"], int)
        assert isinstance(stats["window_days_available"], int)
        assert isinstance(stats["window_years"], float)
        assert all(isinstance(t, str) for t in stats["tickers"])

        restored = json.loads(json.dumps(stats))
        assert restored["window_start"] == stats["window_start"]
        assert restored["mu_geometric"] == pytest.approx(stats["mu_geometric"])


class TestShorthandKeepsArithmeticMeaning:
    """``portfolio_mu_sigma()`` ต้องคืน **เลขคณิต** ต่อไป — ผู้เรียกเดิมทั้งหมดพึ่งความหมายนี้."""

    def test_shorthand_returns_arithmetic_not_geometric(self):
        df = _frame({"A": _ALTERNATING_10})
        mu, sigma = portfolio_mu_sigma(df, {"A": 1.0})
        stats = portfolio_return_stats(df, {"A": 1.0})

        assert mu == pytest.approx(float(stats["mu_arithmetic"]))
        assert sigma == pytest.approx(float(stats["sigma"]))
        # เคสนี้ทบต้น = −71.8% ⇒ ถ้าใครสลับไปคืน geometric จะเห็นทันที
        assert mu == pytest.approx(0.0, abs=1e-12)
        assert mu != pytest.approx(float(stats["mu_geometric"]), abs=0.5)

    def test_shorthand_mu_is_the_one_sharpe_uses(self):
        """μ ของรูปย่อต้องประกอบกลับเป็น Sharpe ตัวเดิมได้พอดี — ฐาน annualize เดียวกัน."""
        df = _frame({"A": _prices_from_returns([0.1, -0.05, 0.1, -0.05])})
        mu, sigma = portfolio_mu_sigma(df, {"A": 1.0})

        sharpe = calculate_sharpe_ratio(df)["A"]
        assert (mu - DEFAULT_RISK_FREE_RATE) / sigma == pytest.approx(float(sharpe), rel=1e-9)
        assert mu == pytest.approx(0.025 * 252)


class TestWeightNormalization:
    """น้ำหนัก normalize ภายใน ⇒ ส่งมูลค่าถือครองดิบ ๆ จาก ledger ได้เลย."""

    def test_raw_holdings_match_fractional_weights(self):
        df = _frame(
            {
                "A": _prices_from_returns([0.02, -0.01, 0.015, -0.005, 0.01]),
                "B": _prices_from_returns([-0.01, 0.02, -0.02, 0.01, 0.005], 40.0),
            }
        )
        raw = portfolio_return_stats(df, {"A": 300_000.0, "B": 100_000.0})
        fractional = portfolio_return_stats(df, {"A": 0.75, "B": 0.25})

        for key in ("mu_arithmetic", "mu_geometric", "sigma", "window_years"):
            assert raw[key] == pytest.approx(fractional[key], rel=1e-12), key
        for key in ("tickers", "window_start", "window_end", "window_days", "window_days_available"):
            assert raw[key] == fractional[key], key


class TestFailLoud:
    """ทุกเส้นทางที่คำนวณไม่ได้ต้อง ``ValueError`` — ห้ามคืนเลขเดา (AUDIT.md C1).

    ต้องเป็น ``ValueError`` เท่านั้น ไม่ใช่ ``RuntimeError``: ``goal_service`` ดัก
    ``except ValueError`` เพื่อแปลงเป็นสถานะ ``error`` พร้อมเหตุผลบนหน้าจอ — ถ้าเปลี่ยน
    ชนิดข้อยกเว้น หน้า Goals จะกลายเป็น 500 แทนที่จะบอกผู้ใช้ว่าใช้สมมติฐานสำเร็จรูปอยู่
    """

    def test_no_ticker_with_both_weight_and_price(self):
        df = _frame({"A": _ALTERNATING_10})
        with pytest.raises(ValueError, match="ไม่มี ticker"):
            portfolio_return_stats(df, {"QQQM": 1.0})   # มีน้ำหนัก ไม่มีราคา
        with pytest.raises(ValueError, match="ไม่มี ticker"):
            portfolio_return_stats(df, {"A": 0.0})      # มีราคา ไม่มีน้ำหนัก
        with pytest.raises(ValueError, match="ไม่มี ticker"):
            portfolio_return_stats(df, {})

    def test_disjoint_histories_leave_no_common_row(self):
        """สองกองที่ประวัติไม่ทับกันเลย ⇒ หลัง ``dropna`` ว่าง ⇒ ต้องดัง ไม่ใช่ μ = 0."""
        nan = float("nan")
        df = _frame(
            {
                "OLD": [100.0, 110.0, 99.0, 108.9] + [nan] * 6,
                "NEW": [nan] * 5 + _prices_from_returns([0.1, -0.1, 0.1, -0.1], 50.0),
            }
        )
        with pytest.raises(ValueError, match="ผลตอบแทนรายวันว่าง"):
            portfolio_return_stats(df, {"OLD": 0.5, "NEW": 0.5})

    def test_flat_series_raises_instead_of_feeding_zero_risk(self):
        """σ = 0 ⇒ Monte Carlo ที่ "ไม่มีความเสี่ยง" คือคำโกหก ต้อง raise."""
        with pytest.raises(ValueError, match="μ/σ"):
            portfolio_return_stats(_frame({"FLAT": [100.0] * 6}), {"FLAT": 1.0})

    def test_single_return_row_has_no_sigma_and_raises(self):
        """ราคา 2 จุด ⇒ ผลตอบแทนแถวเดียว ⇒ std ไม่นิยาม (NaN) ต้อง raise ไม่ใช่ปล่อย NaN ออก."""
        with pytest.raises(ValueError, match="μ/σ"):
            portfolio_return_stats(_frame({"A": [100.0, 110.0]}), {"A": 1.0})

    def test_total_loss_day_blocks_the_compound_rate(self):
        """มีวันที่พอร์ตติดลบ ≤ −100% ⇒ ทบต้นไม่ได้ ต้องบอกตรง ๆ ห้ามปัดเป็นเลขสวย."""
        df = _frame({"A": [100.0, 110.0, 99.0, 0.0]})
        with pytest.raises(ValueError, match="CAGR"):
            portfolio_return_stats(df, {"A": 1.0})

    def test_total_loss_of_one_holding_still_computes_for_the_portfolio(self):
        """กองเดียวเป็นศูนย์แต่พอร์ตยังไม่หมดตัว ⇒ ต้องคำนวณได้ — ด่านนี้อยู่ที่ระดับ**พอร์ต**.

        ครึ่งพอร์ตหายในวันเดียว = −45% ของพอร์ต ซึ่งยังทบต้นได้ ถ้าเผลอย้ายด่านไปตรวจ
        รายกอง เคสนี้จะ raise ทั้งที่คำนวณได้จริง (ปิดข้อมูลผู้ใช้โดยไม่จำเป็น)

        หมายเหตุที่ต้องอ่านคู่กัน: ที่ขาดทุนหนักขนาดนี้ CAGR ชนพื้นที่ −100% (เสียหมดภายใน
        ปีเดียว) ขณะที่ค่าเฉลี่ยเลขคณิต × 252 ไหลลงได้ไม่มีพื้น (−3780%) ⇒ ที่หางซ้าย
        "ทบต้น < เลขคณิต" ไม่จริงอีกต่อไป ค่าคงที่ที่ยังจริงเสมอคือ ``ln(1+CAGR) ≤ μ``
        """
        df = _frame({"A": [100.0, 110.0, 99.0, 0.0], "B": [50.0, 55.0, 49.5, 54.45]})
        stats = portfolio_return_stats(df, {"A": 0.5, "B": 0.5})

        # ผลตอบแทนพอร์ตวันสุดท้าย = (−100% + 10%) / 2 = −45%
        assert math.isfinite(float(stats["mu_geometric"]))
        assert -1.0 <= float(stats["mu_geometric"]) <= 0.0   # ทบต้นแย่ที่สุด = เสียหมด ไม่ต่ำกว่านั้น
        assert float(stats["mu_arithmetic"]) < -1.0          # เลขคณิต × 252 ไม่มีพื้น
        assert stats["window_days"] == 3
