# -*- coding: utf-8 -*-
"""ทดสอบตัวชี้วัดความเสี่ยง: underwater / drawdown (Roadmap A3) + volatility / Sharpe / μ-σ.

ส่วน ``TestAnnualizedMetrics`` และ ``TestPortfolioMuSigma`` เพิ่มตาม AUDIT_2026-08-06 ข้อ 0-D:
mutation testing รอบ R9 เปลี่ยนตัวคูณ annualize จาก ``√252`` เป็น ``252`` (และ μ จาก
``×252`` เป็น ``×12``) แล้วชุดเทสต์ผ่านหมด ทั้งที่ σ ที่เพี้ยนไป 15.9 เท่าเป็นตัวป้อน
Monte Carlo ของหน้า Goals และ Volatility เป็นคอลัมน์บนหน้า Risk
"""

import numpy as np
import pandas as pd
import pytest

from analysis.risk import (
    DEFAULT_RISK_FREE_RATE,
    calculate_max_drawdown,
    calculate_sharpe_ratio,
    calculate_volatility,
    drawdown_episodes,
    portfolio_mu_sigma,
    underwater_series,
)


def _prices(values: list[float], start: str = "2024-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype=float)


def _frame(columns: dict[str, list[float]]) -> pd.DataFrame:
    length = len(next(iter(columns.values())))
    return pd.DataFrame(columns, index=pd.date_range("2024-01-01", periods=length, freq="B"))


# ราคาที่ให้ผลตอบแทนรายวัน +10%, −10%, +10%, −10% พอดี (คำนวณมือได้ทั้งชุด)
_PLUS_MINUS_10 = [100.0, 110.0, 99.0, 108.9, 98.01]
# ผลตอบแทน +10%, −5%, +10%, −5%
_PLUS10_MINUS5 = [100.0, 110.0, 104.5, 114.95, 109.2025]


def test_underwater_series_matches_ath_distance():
    uw = underwater_series(_prices([100, 110, 99, 110, 121]))
    assert uw.iloc[0] == 0.0
    assert uw.iloc[1] == 0.0
    assert uw.iloc[2] == pytest.approx(-0.1)
    assert uw.iloc[3] == 0.0
    assert uw.iloc[4] == 0.0


def test_max_drawdown_is_underwater_minimum():
    df = pd.DataFrame(
        {"A": [100.0, 80.0, 120.0], "B": [50.0, 55.0, 60.0]},
        index=pd.date_range("2024-01-01", periods=3),
    )
    mdd = calculate_max_drawdown(df)
    assert mdd["A"] == pytest.approx(-0.2)
    assert mdd["B"] == pytest.approx(0.0)


def test_drawdown_episodes_split_recovery_and_open_round():
    # 110→88 (-20%) ฟื้นที่ 110, ทำ ATH ใหม่ 120 → ร่วงเหลือ 96 (-20%) ยังไม่ฟื้น
    s = _prices([100, 110, 99, 88, 110, 120, 96])
    episodes = drawdown_episodes(s, min_depth=0.15)
    assert len(episodes) == 2

    recovered = [e for e in episodes if e["recovery_date"] is not None]
    still_open = [e for e in episodes if e["recovery_date"] is None]
    assert len(recovered) == 1 and len(still_open) == 1

    rec = recovered[0]
    assert rec["depth_pct"] == pytest.approx(-20.0)
    assert pd.Timestamp(rec["peak_date"]) == pd.Timestamp("2024-01-02")
    assert pd.Timestamp(rec["trough_date"]) == pd.Timestamp("2024-01-04")
    assert pd.Timestamp(rec["recovery_date"]) == pd.Timestamp("2024-01-05")
    assert rec["months_to_recover"] is not None and rec["months_to_recover"] > 0

    cur = still_open[0]
    assert pd.Timestamp(cur["peak_date"]) == pd.Timestamp("2024-01-06")
    assert cur["months_to_recover"] is None


def test_min_depth_filters_shallow_episodes():
    s = _prices([100, 95, 100, 50, 100])  # -5% ตื้นเกิน, -50% ลึกพอ
    episodes = drawdown_episodes(s, min_depth=0.10)
    assert len(episodes) == 1
    assert episodes[0]["depth_pct"] == pytest.approx(-50.0)


def test_empty_prices_fail_loud():
    with pytest.raises(ValueError):
        underwater_series(pd.Series(dtype=float))
    with pytest.raises(ValueError):
        drawdown_episodes(pd.Series(dtype=float))


class TestAnnualizedMetrics:
    """ค่าจริงจากซีรีส์ที่รู้คำตอบ — ตัวคูณ annualize ต้องเป็น ``√252`` ไม่ใช่ ``252``."""

    # rets = [0.1, −0.1, 0.1, −0.1] → mean = 0, std(ddof=1) = √(0.04/3) = 0.1154700538
    EXPECTED_VOL = 0.1154700538379252 * np.sqrt(252)          # = 1.8330302779823366
    EXPECTED_SHARPE = (0.0 - DEFAULT_RISK_FREE_RATE) / EXPECTED_VOL  # = −0.0109108945

    def test_volatility_is_daily_std_times_sqrt_252(self):
        vol = calculate_volatility(_frame({"A": _PLUS_MINUS_10}))
        assert vol["A"] == pytest.approx(1.8330302779823366)
        assert vol["A"] == pytest.approx(self.EXPECTED_VOL)
        # กันการสลับเป็น × 252: ค่าที่ผิดจะเป็น 29.10 (สูงเกินจริง 15.87 เท่า)
        assert vol["A"] < 5.0

    def test_volatility_is_computed_per_column(self):
        vol = calculate_volatility(_frame({"A": _PLUS_MINUS_10, "FLAT": [100.0] * 5}))
        assert vol["A"] == pytest.approx(1.8330302779823366)
        assert vol["FLAT"] == pytest.approx(0.0)

    def test_volatility_respects_annualization_argument(self):
        df = _frame({"A": _PLUS_MINUS_10})
        assert calculate_volatility(df, annualization=1)["A"] == pytest.approx(0.1154700538379252)
        assert calculate_volatility(df, annualization=4)["A"] == pytest.approx(0.1154700538379252 * 2)

    def test_sharpe_uses_the_same_annualization_and_the_shared_risk_free_rate(self):
        sharpe = calculate_sharpe_ratio(_frame({"A": _PLUS_MINUS_10}))
        assert sharpe["A"] == pytest.approx(-0.010910894511791985)
        assert sharpe["A"] == pytest.approx(self.EXPECTED_SHARPE)

    def test_sharpe_of_a_positive_drift_series(self):
        # rets = [0.1, −0.05, 0.1, −0.05] → mean = 0.025 → μ = 6.30
        # std(ddof=1) = 0.0866025404 → σ = 1.3747727085
        sharpe = calculate_sharpe_ratio(_frame({"A": _PLUS10_MINUS5}))
        assert sharpe["A"] == pytest.approx((0.025 * 252 - 0.02) / (0.0866025403784439 * np.sqrt(252)))
        assert sharpe["A"] == pytest.approx(4.5680, abs=1e-4)

    def test_flat_series_gives_nan_sharpe_not_zero(self):
        """σ = 0 → Sharpe ไม่นิยาม ต้องเป็น NaN ห้ามกลายเป็น 0.0 ที่อ่านว่า "กลาง ๆ" (C1)."""
        sharpe = calculate_sharpe_ratio(_frame({"FLAT": [100.0] * 5}))
        assert pd.isna(sharpe["FLAT"])

    def test_risk_free_rate_is_the_shared_constant(self):
        assert DEFAULT_RISK_FREE_RATE == pytest.approx(0.02)


class TestPortfolioMuSigma:
    """μ/σ ที่ป้อน Monte Carlo หน้า Goals — ตัวเลขนี้ตัดสินว่าผู้ใช้ "ถึงเป้าไหม"."""

    def test_mu_and_sigma_of_a_known_series(self):
        df = _frame({"A": _PLUS10_MINUS5})
        mu, sigma = portfolio_mu_sigma(df, {"A": 1.0})
        # μ = ค่าเฉลี่ยรายวัน × 252 (ไม่ใช่ × 12 — ผลตอบแทนเป็นรายวันไม่ใช่รายเดือน)
        assert mu == pytest.approx(0.025 * 252)
        assert mu == pytest.approx(6.3)
        # σ = std รายวัน × √252
        assert sigma == pytest.approx(0.0866025403784439 * np.sqrt(252))
        assert sigma == pytest.approx(1.3747727084867518)

    def test_weights_are_normalized_so_raw_holdings_work(self):
        """ส่งมูลค่าถือครองดิบ ๆ ต้องได้ผลเท่ากับส่งสัดส่วนที่รวมเป็น 1.0."""
        df = _frame({"A": _PLUS10_MINUS5, "FLAT": [100.0] * 5})
        raw = portfolio_mu_sigma(df, {"A": 300_000.0, "FLAT": 100_000.0})
        normalized = portfolio_mu_sigma(df, {"A": 0.75, "FLAT": 0.25})
        assert raw == pytest.approx(normalized)
        # 75% ของสินทรัพย์เดียวที่ขยับ ⇒ σ เหลือ 0.75 เท่าของ σ เดี่ยว
        assert raw[1] == pytest.approx(0.75 * 1.3747727084867518)
        assert raw[0] == pytest.approx(0.75 * 6.3)

    def test_ticker_without_weight_or_price_is_excluded(self):
        df = _frame({"A": _PLUS10_MINUS5, "B": _PLUS_MINUS_10})
        only_a = portfolio_mu_sigma(df, {"A": 1.0, "B": 0.0})
        assert only_a == pytest.approx(portfolio_mu_sigma(df[["A"]], {"A": 1.0}))

    def test_no_usable_ticker_raises_instead_of_returning_a_default(self):
        df = _frame({"A": _PLUS10_MINUS5})
        with pytest.raises(ValueError):
            portfolio_mu_sigma(df, {"QQQM": 1.0})   # ไม่มีราคา
        with pytest.raises(ValueError):
            portfolio_mu_sigma(df, {"A": 0.0})      # ไม่มีน้ำหนัก

    def test_zero_volatility_raises_instead_of_feeding_monte_carlo(self):
        """σ = 0 (ราคาข้อมูลนิ่ง) ต้อง raise — ไม่ใช่ป้อน MC ด้วยความเสี่ยงศูนย์."""
        with pytest.raises(ValueError):
            portfolio_mu_sigma(_frame({"FLAT": [100.0] * 5}), {"FLAT": 1.0})
