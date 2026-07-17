# -*- coding: utf-8 -*-
"""เทสต์ backtest A/B harness (Phase 0 ข้อ 1) — ข้อมูลสังเคราะห์ทั้งหมด ไม่ยิง network."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import math

import numpy as np
import pandas as pd
import pytest

from portfolio.ab_backtest import (
    PROXY_MAP,
    fixed_weights_fn,
    run_ab_backtest,
    score_tilt_weights_fn,
    simulate_dca_dynamic,
)


def _constant_prices(start: str, periods: int, values: dict[str, float]) -> pd.DataFrame:
    index = pd.bdate_range(start, periods=periods)
    return pd.DataFrame({t: [v] * periods for t, v in values.items()}, index=index)


# ---------------------------------------------------------------------------
# (ก) fixed weights — ตรวจเลขกับการคำนวณมือ
# ---------------------------------------------------------------------------


def test_fixed_weights_matches_hand_calculation():
    prices = _constant_prices("2024-01-01", 65, {"A": 10.0, "B": 20.0})  # ~3 เดือน
    result = simulate_dca_dynamic(prices, 1000.0, fixed_weights_fn({"A": 0.5, "B": 0.5}))

    assert result["n_months"] == 3
    assert result["total_invested"] == pytest.approx(3000.0)
    # เดือนละ 500/10 = 50 หุ้น A และ 500/20 = 25 หุ้น B
    assert result["shares"]["A"] == pytest.approx(150.0)
    assert result["shares"]["B"] == pytest.approx(75.0)
    # ราคาคงที่ → มูลค่าพอร์ต = เงินที่ลงไป กำไร 0
    assert result["final_value"] == pytest.approx(3000.0)
    assert result["pl_pct"] == pytest.approx(0.0)
    assert (result["monthly_returns"] == 0.0).all()


def test_invalid_weights_fail_loud():
    prices = _constant_prices("2024-01-01", 40, {"A": 10.0})
    with pytest.raises(ValueError):
        simulate_dca_dynamic(prices, 1000.0, lambda d, h: {"A": 0.0})
    with pytest.raises(ValueError):
        simulate_dca_dynamic(prices, -5.0, fixed_weights_fn({"A": 1.0}))


# ---------------------------------------------------------------------------
# (ข) no look-ahead — weights_fn ต้องเห็นเฉพาะราคาก่อนวันซื้อ
# ---------------------------------------------------------------------------


def test_weights_fn_never_sees_future_prices():
    prices = _constant_prices("2023-01-01", 260, {"A": 10.0, "B": 20.0})
    seen: list[tuple[pd.Timestamp, pd.DataFrame]] = []

    def spy(buy_date: pd.Timestamp, history: pd.DataFrame) -> dict[str, float]:
        seen.append((buy_date, history))
        return {"A": 0.5, "B": 0.5}

    result = simulate_dca_dynamic(prices, 1000.0, spy)

    assert len(seen) == result["n_months"] > 0
    for buy_date, history in seen:
        if len(history):
            assert history.index.max() < buy_date
            assert (history.index < buy_date).all()


# ---------------------------------------------------------------------------
# (ค) weights แปรตามเวลา + normalize ให้เองทุกเดือน
# ---------------------------------------------------------------------------


def test_time_varying_weights_are_normalized_each_month():
    prices = _constant_prices("2024-01-01", 130, {"A": 10.0, "B": 20.0})  # ~6 เดือน

    def alternating(buy_date: pd.Timestamp, history: pd.DataFrame) -> dict[str, float]:
        # เดือนคู่ทุ่ม A ล้วน, เดือนคี่ส่งน้ำหนักไม่ normalize มาให้ (รวม = 4)
        if buy_date.month % 2 == 0:
            return {"A": 2.0}
        return {"A": 1.0, "B": 3.0}

    result = simulate_dca_dynamic(prices, 1000.0, alternating)

    assert result["n_months"] == 6
    assert result["total_invested"] == pytest.approx(6000.0)
    # ราคาคงที่ → เงินทุกบาทกลายเป็นมูลค่าพอร์ตพอดี แม้น้ำหนักส่งมาไม่ normalize
    assert result["final_value"] == pytest.approx(6000.0)
    # เดือนคี่ B ได้ 3/4 ของงบ: 3 เดือนคี่ × 750 / ราคา 20 = 112.5 หุ้น
    assert result["shares"]["B"] == pytest.approx(112.5)


# ---------------------------------------------------------------------------
# (ง) PROXY_MAP + label ของช่วง proxy
# ---------------------------------------------------------------------------


def test_run_ab_backtest_maps_proxies_and_labels_window():
    tickers = ["VOO", "SCHD", "QQQ", "XLV", "GLD"]
    index = pd.bdate_range("2011-10-03", periods=260)
    rng = np.linspace(0.0, 1.0, len(index))
    prices = pd.DataFrame(
        {t: 100.0 + 10.0 * (i + 1) * rng for i, t in enumerate(tickers)}, index=index
    )
    targets = {"VOO": 0.35, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.10}

    results = run_ab_backtest({"proxy": prices}, monthly_amount=5000.0, target_weights=targets)
    window = results["proxy"]

    assert "proxy" in window["window_label"]
    assert set(window["weights_base"]) == set(tickers)  # QQQM→QQQ, GLDM→GLD แล้ว
    assert "QQQM" not in window["weights_base"] and "GLDM" not in window["weights_base"]
    assert PROXY_MAP == {"QQQM": "QQQ", "GLDM": "GLD"}

    arms = window["arms"]
    for arm in ("plain", "tilt", "voo_only"):
        assert arms[arm]["total_invested"] == pytest.approx(
            arms[arm]["n_months"] * 5000.0
        )
    # ช่วงต้นประวัติ < 200 วันเทรด → tilt ต้องมีเดือนที่เป็นกลาง (ไม่เดาคะแนน)
    assert arms["tilt"]["months_neutral"] >= 1
    assert isinstance(window["tilt_beats_plain"]["overall"], bool)
    assert "tilt" in window["summary_th"]


def test_run_ab_backtest_rejects_unknown_window_and_missing_columns():
    prices = _constant_prices("2020-11-02", 40, {"VOO": 10.0})
    with pytest.raises(ValueError):
        run_ab_backtest({"nonsense": prices}, target_weights={"VOO": 1.0})
    with pytest.raises(ValueError):
        run_ab_backtest({"real": prices}, target_weights={"VOO": 0.5, "SCHD": 0.5})


# ---------------------------------------------------------------------------
# (จ) ticker ที่ประวัติไม่พอ → tilt กลาง 1.0 แต่ยังถูกซื้อ + มี flag
# ---------------------------------------------------------------------------


def test_insufficient_history_ticker_stays_bought_at_neutral_tilt():
    index = pd.bdate_range("2023-01-02", periods=300)
    steps = np.arange(len(index), dtype=float)
    frame = pd.DataFrame(
        {
            "A": 100.0 + 0.05 * steps + 2.0 * np.sin(steps / 9.0),
            "B": 50.0 + 0.02 * steps,
        },
        index=index,
    )
    frame.loc[index[:150], "B"] = np.nan  # B เพิ่งมีราคา ~150 วันหลัง → ประวัติไม่ถึง 200

    fn = score_tilt_weights_fn({"A": 0.5, "B": 0.5})
    buy_date = index[260]
    weights = fn(buy_date, frame.loc[frame.index < buy_date])

    assert weights["B"] == pytest.approx(0.5)  # tilt กลาง 1.0 — ยังอยู่ในแผนซื้อ
    tilt_a = weights["A"] / 0.5
    assert 0.6 <= tilt_a <= 1.4
    assert fn.neutral_log and fn.neutral_log[0][1] == ("B",)

    sim = simulate_dca_dynamic(frame, 1000.0, fn, start=index[260])
    assert sim["shares"]["B"] > 0  # ถูกซื้อจริง ไม่ถูกตัดทิ้ง


# ---------------------------------------------------------------------------
# (ฉ) integration เบา ๆ — tilt จาก pipeline จริงอยู่ในกรอบ 0.6–1.4 เสมอ
# ---------------------------------------------------------------------------


def test_score_tilt_weights_within_bounds_on_realistic_history():
    index = pd.bdate_range("2023-01-02", periods=320)
    steps = np.arange(len(index), dtype=float)
    frame = pd.DataFrame(
        {
            "UP": 100.0 + 0.12 * steps + 3.0 * np.sin(steps / 7.0),
            "DOWN": 120.0 - 0.05 * steps + 2.0 * np.sin(steps / 11.0),
        },
        index=index,
    )
    base = {"UP": 0.6, "DOWN": 0.4}
    fn = score_tilt_weights_fn(base)

    buy_date = index[-1] + pd.Timedelta(days=1)
    weights = fn(buy_date, frame)

    assert not fn.neutral_log  # ประวัติ 320 วัน > 200 → ทุกตัวถูกให้คะแนนจริง
    for ticker, target in base.items():
        tilt = weights[ticker] / target
        assert 0.6 - 1e-9 <= tilt <= 1.4 + 1e-9
        assert math.isfinite(weights[ticker])
