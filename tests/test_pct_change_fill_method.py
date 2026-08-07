# -*- coding: utf-8 -*-
"""B11 — ``pct_change()`` ต้องระบุ ``fill_method=None`` ทุกจุดบนเส้นทางราคา.

pandas 2.2.3 ตั้งค่าเริ่มต้นเป็น ``fill_method='pad'`` = **forward-fill ราคาก่อนคำนวณ
ผลตอบแทน** วันที่ ETF ตัวหนึ่งไม่มีแท่งจึงกลายเป็นผลตอบแทน **0.00% พอดี** แล้วไหลเข้า
Volatility / Sharpe / Correlation / seasonality — คือการกุตัวเลขบนเส้นทางราคาแบบเดียวกับ
``ffill`` ที่จับได้ใน ``etf_service`` (AUDIT_2026-08-06 H10) และเป็นสิ่งที่ CLAUDE.md ห้ามตรง ๆ

หลักฐานก่อนแก้ (เฟรม 5 ETF 2,511 แท่ง เจาะรู GLDM หาย 50 แท่ง)::

    จำนวนวันที่ผลตอบแทน GLDM = 0.0 พอดี: 50   vs  fill_method=None: 0
    GLDM annual vol 16.1487% vs 16.2136%   corr GLDM-VOO 0.002212 vs 0.000828
    FutureWarning: The default fill_method='pad' in DataFrame.pct_change is deprecated

"หายไป" ต้องเป็น ``NaN`` ให้ ``dropna``/``skipna`` จัดการต่ออย่างซื่อสัตย์ ไม่ใช่ 0.00%
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analysis import correlation as correlation_mod
from analysis.correlation import calculate_correlation, calculate_correlation_matrix
from analysis.returns import monthly_seasonality
from analysis.risk import calculate_daily_returns, calculate_volatility
from utils.cache import clear_all_caches

TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
_HOLE_POSITIONS = [37, 120, 400, 401, 900]  # รวมรูติดกัน 2 วันด้วย


@pytest.fixture(autouse=True)
def _clear_cache():
    """risk/correlation ถูกห่อด้วย ``cache_data_1h`` — ล้างก่อน/หลังทุกเทสต์."""
    clear_all_caches()
    yield
    clear_all_caches()


def _price_frame(n: int = 1000) -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-02", periods=n)
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {t: 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n))) for t in TICKERS},
        index=idx,
    )


def _frame_with_holes() -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """เฟรมที่ GLDM ไม่มีแท่งอยู่ 5 วัน (วันหยุดของตลาดทอง / ข้อมูลขาด)."""
    frame = _price_frame()
    col = frame.columns.get_loc("GLDM")
    frame.iloc[_HOLE_POSITIONS, col] = np.nan
    return frame, frame.index[_HOLE_POSITIONS]


def _honest_returns(frame: pd.DataFrame) -> pd.DataFrame:
    """ค่าอ้างอิง: คำนวณตรง ๆ โดยไม่เติมราคาที่ไม่มีอยู่จริง."""
    return frame.sort_index().pct_change(fill_method=None).dropna(how="all")


# --------------------------------------------------- analysis/risk.py


def test_daily_returns_ไม่กุ_0_เปอร์เซ็นต์ให้วันที่ไม่มีแท่ง():
    frame, hole_days = _frame_with_holes()
    daily = calculate_daily_returns(frame)

    assert daily.loc[hole_days, "GLDM"].isna().all(), "วันที่ไม่มีราคาต้องเป็น NaN ไม่ใช่ตัวเลข"
    assert int((daily["GLDM"] == 0.0).sum()) == 0, "ห้ามมีผลตอบแทน 0.00% ที่ถูกกุจากการ ffill"
    # ETF ตัวอื่นที่มีข้อมูลครบต้องไม่ถูกกระทบ
    assert daily["VOO"].isna().sum() == 0


def test_volatility_เท่ากับคำนวณตรงโดยไม่เติมราคา():
    frame, _ = _frame_with_holes()
    expected = _honest_returns(frame).std() * np.sqrt(252)
    got = calculate_volatility(frame)
    assert float(got["GLDM"]) == pytest.approx(float(expected["GLDM"]), rel=1e-12)


# --------------------------------------------------- analysis/correlation.py


def test_correlation_matrix_เท่ากับคำนวณตรงโดยไม่เติมราคา():
    frame, _ = _frame_with_holes()
    expected = _honest_returns(frame).corr()
    got = calculate_correlation_matrix(frame)
    assert float(got.loc["GLDM", "VOO"]) == pytest.approx(
        float(expected.loc["GLDM", "VOO"]), rel=1e-12
    )


def test_calculate_correlation_ไม่เติมราคาให้วันที่ตลาดไม่มีแท่ง(monkeypatch):
    """เส้นทางที่ดึงจาก yfinance เอง (correlation.py:50) — stub ไว้ ไม่ยิงเน็ตจริง."""
    frame, _ = _frame_with_holes()
    raw = pd.concat({t: frame[[t]].rename(columns={t: "Adj Close"}) for t in TICKERS}, axis=1)

    monkeypatch.setattr(correlation_mod, "get_tickers", lambda: list(TICKERS))
    monkeypatch.setattr(correlation_mod.yf, "download", lambda *a, **k: raw)

    expected = _honest_returns(frame).corr()
    got = calculate_correlation(period="5y")
    assert float(got.loc["GLDM", "VOO"]) == pytest.approx(
        float(expected.loc["GLDM", "VOO"]), rel=1e-12
    )


# --------------------------------------------------- analysis/returns.py


def _monthly_prices_with_gap() -> pd.Series:
    """ราคาสิ้นเดือนที่**ขึ้นทุกเดือน** แล้วเจาะเดือนที่ 14–15 ทิ้ง.

    ผลตอบแทนรายเดือนที่ซื่อสัตย์จึงเป็นบวกทุกค่า — ถ้ามีเดือนไหนได้ 0.00%
    แปลว่าโค้ด pad ราคาเดือนก่อนหน้ามาแทนเดือนที่ไม่มีข้อมูล
    """
    idx = pd.date_range("2020-01-31", periods=36, freq="ME")
    prices = pd.Series(np.linspace(100.0, 200.0, 36), index=idx)
    return prices.drop(prices.index[[13, 14]])


def test_seasonality_ไม่นับเดือนที่ไม่มีข้อมูลเป็นผลตอบแทน_0():
    stats = monthly_seasonality(_monthly_prices_with_gap())

    positive = stats["positive_rate_pct"].dropna()
    assert (positive == 100.0).all(), "ราคาขึ้นทุกเดือน — 0.00% ที่โผล่มาคือเดือนที่ถูกกุ"
    # 36 เดือน → pct_change ทิ้งเดือนแรก, เดือนที่หาย 2 เดือน และเดือนถัดจากรูอีก 1 เดือน
    assert float(stats["n_samples"].sum()) == 32.0


# --------------------------------------------------- FutureWarning / source scan


def test_ไม่มี_FutureWarning_เรื่อง_fill_method():
    frame, _ = _frame_with_holes()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        calculate_daily_returns(frame)
        calculate_correlation_matrix(frame)
        monthly_seasonality(_monthly_prices_with_gap())
    offenders = [str(w.message) for w in caught if "fill_method" in str(w.message)]
    assert offenders == [], f"pandas ยังเตือนเรื่อง fill_method: {offenders}"


_SCANNED_FILES = [
    "analysis/risk.py",
    "analysis/correlation.py",
    "analysis/returns.py",
    "analysis/financial_model.py",
    "portfolio/edge_lab.py",
]


def test_ทุก_pct_change_ในไฟล์ที่เกี่ยวข้องระบุ_fill_method():
    """กันไม่ให้มีใครเผลอเขียน ``pct_change()`` เปล่า ๆ กลับเข้ามาอีก.

    (ขอบเขต B11 — ``portfolio/backtest.py`` ใช้ ``pct_change().fillna(0.0)``
    ซึ่งเป็นคนละประเด็นและอยู่นอกขอบเขตข้อนี้)
    """
    root = Path(__file__).resolve().parent.parent
    offenders: list[str] = []
    for rel in _SCANNED_FILES:
        source = (root / rel).read_text(encoding="utf-8")
        for match in re.finditer(r"\.pct_change\(([^)]*)\)", source):
            if "fill_method" not in match.group(1):
                line = source[: match.start()].count("\n") + 1
                offenders.append(f"{rel}:{line}")
    assert offenders == [], f"pct_change ที่ไม่ระบุ fill_method: {offenders}"
