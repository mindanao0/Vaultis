# -*- coding: utf-8 -*-
"""ล็อกตัวจำลอง research/dar_sim ให้ตรงกับฟังก์ชันจริงของโปรเจกต์ (ออฟไลน์ ข้อมูลสังเคราะห์).

sim.py มีสูตรเวกเตอร์ของ DAR / ปัดหน่วยร้อย / ERC ที่ซ้ำกับ analysis/dar_dca.py และ portfolio/risk_weights.py —
เลขซ้ำไม่ล้มดัง มันแค่ค่อย ๆ เพี้ยน (บทเรียนข้อ "One signal definition" ใน CLAUDE.md) เทสต์นี้ทำให้เพี้ยนไม่ได้เงียบ ๆ
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analysis import dar_dca
from portfolio.risk_weights import erc_weights

_SIM_PATH = Path(__file__).resolve().parents[1] / "research" / "dar_sim" / "sim.py"
_spec = importlib.util.spec_from_file_location("dar_sim_model", _SIM_PATH)
sim = importlib.util.module_from_spec(_spec)
sys.modules["dar_sim_model"] = sim  # dataclass ต้องหาโมดูลของตัวเองเจอ
_spec.loader.exec_module(sim)

FUNDS = list(dar_dca.TICKERS)


def _frame(rng: np.random.Generator, months: int, schd_short: int = 0) -> pd.DataFrame:
    r = rng.normal(0.006, 0.045, size=(months, 5)) + rng.normal(0, 0.01, size=(months, 1))
    lv = np.exp(np.cumsum(r, axis=0)) * 50
    df = pd.DataFrame(lv, columns=FUNDS, index=pd.date_range(end="2026-09-30", periods=months, freq="ME"))
    if schd_short:
        df.loc[df.index[:schd_short], "SCHD"] = np.nan
    return df


def test_constants_are_read_from_the_real_formula():
    # ค่าคงที่ต้องมาจาก analysis.dar_dca ไม่ใช่สำเนา
    assert sim.FUNDS == FUNDS
    assert (sim.AMP_FROM, sim.AMP_TO, sim.DRIFT_COEF, sim.SD_MIN) == (
        dar_dca.AMP_FROM, dar_dca.AMP_TO, dar_dca.DRIFT_COEF, dar_dca.SD_MIN)
    assert (sim.FLOOR_FRAC, sim.CAP_MULT, sim.HISTORY_MONTHS) == (
        dar_dca.FLOOR_FRAC, dar_dca.CAP_MULT, dar_dca.HISTORY_MONTHS)
    assert sim.UNITS * dar_dca.ALLOCATION_UNIT_THB == int(dar_dca.MONTHLY_BUDGET_THB)


@pytest.mark.parametrize("seed,schd_short", [(1, 0), (2, 20), (3, 0), (4, 10)])
def test_dar_weights_vectorised_equals_project_function(seed, schd_short):
    rng = np.random.default_rng(seed)
    months = 215
    df = _frame(rng, months, schd_short=schd_short + (months - 180 if schd_short else 0))
    ref, _ = dar_dca.dar_weights(df)
    LL = np.log(df.to_numpy())[None]
    first_valid = np.array([int(df[c].notna().to_numpy().argmax()) for c in FUNDS])
    n = months - 1
    valid = (n - first_valid) >= dar_dca.HISTORY_MONTHS - 1
    mine = sim.dar_weights_b(LL, n, valid)[0]
    assert np.allclose(ref.to_numpy(), mine, atol=1e-12)


def test_floor_and_cap_projection_equal_project_functions():
    rng = np.random.default_rng(0)
    w = rng.dirichlet(np.ones(5) * 0.3, size=300)
    assert np.allclose(np.array([dar_dca.floor_project(x, 0.04) for x in w]), sim.floor_project_b(w, 0.04), atol=1e-12)
    assert np.allclose(np.array([dar_dca.cap_project(x, 0.3) for x in w]), sim.cap_project_b(w, 0.3), atol=1e-12)


def test_round_units_equals_round_to_units():
    rng = np.random.default_rng(5)
    raw = rng.dirichlet(np.ones(5) * 2.0, size=400)
    w = sim.cap_project_b(sim.floor_project_b(raw, dar_dca.FLOOR_FRAC / 5), dar_dca.CAP_MULT / 5)
    mine = sim.round_units_b(w)
    for p in range(len(w)):
        units = dar_dca.round_to_units(pd.Series(w[p], index=FUNDS), dar_dca.MONTHLY_BUDGET_THB)
        assert np.allclose(np.array([units[t] for t in FUNDS]) / dar_dca.MONTHLY_BUDGET_THB, mine[p], atol=1e-12)
    assert np.allclose(mine.sum(1), 1.0)


def test_erc_batch_equals_project_solver():
    rng = np.random.default_rng(6)
    covs = []
    for _ in range(40):
        a = rng.normal(size=(5, 5)) * 0.15 + np.array([1, 0.9, 0.95, 0.7, 0.1])[:, None] * rng.normal(size=(1, 5)) * 0.3
        covs.append(a @ a.T / 5 + np.diag(rng.uniform(0.01, 0.05, 5)))
    covs = np.array(covs)
    w, _, _ = sim.erc_batch(covs)
    assert np.allclose(np.array([erc_weights(c) for c in covs]), w, atol=1e-7)


# ---------------------------------------------------------------- ตัวจำลองทั้งตัวบนแผงสังเคราะห์
def _panel(seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    K, D = 4, 10
    pools, pool_S, pool_days = [], [], []
    for k, n in enumerate((60, 20, 12, 15)):
        rows = rng.normal(0.0, 0.04, size=(n, D))
        rows[:, 5] = rng.normal(0, 0.02, n)         # fx
        rows[:, 6] = 0.002 + rng.normal(0, 0.002, n)  # us_infl
        pools.append(rows)
        S = np.zeros((n, 15))
        iu = np.triu_indices(5)
        for i in range(n):
            a = rng.normal(0, 0.01, size=(21, 5))
            S[i] = (a.T @ a)[iu]
        pool_S.append(S)
        pool_days.append(np.full(n, 21.0))
    live = np.log(_frame(rng, 200).to_numpy())
    live[:20, 1] = np.nan
    return {
        "cols": FUNDS + ["fx", "us_infl", "d_ffr", "d_10y", "oil"], "pools": pools,
        "trans": np.full((K, K), 0.1) + np.eye(K) * 0.6, "start_regime": 0,
        "live_me_logs": live, "first_valid": {f: int(np.isfinite(live[:, i]).argmax()) for i, f in enumerate(FUNDS)},
        "thb_hist": np.zeros((60, 5)), "fx0": 33.5,
        "pool_S": pool_S, "pool_days": pool_days,
        "live_S": np.tile(np.array([(np.eye(5) * 1e-4)[np.triu_indices(5)]]), (60, 1)), "live_days": np.full(60, 21.0),
        "th_fit": {"a": 0.0, "b": 1.0, "resid_sd_annual": 0.02},
        "events_measured": {"dotcom": {"VOO": -0.6, "QQQM": -1.7, "XLV": -0.2, "months": 31},
                            "fx_up_6m": 0.6, "fx_down_24m": -0.3, "fx_up_6m_end": "1997-12-31", "fx_down_24m_end": "2000-01-31"},
        "start_levels": {"ffr": 3.75, "y10": 5.0, "oil": 90.0},
    }


def test_simulation_money_accounting_matches_a_plain_loop():
    cfg = sim.Config(world="rw", drift="mid", P=6, T=60, seed=11, debug=6, arms=("EQ",), round_units=False)
    panel = _panel()
    r = sim.run_chunk(panel, cfg)
    Hr = panel["live_me_logs"].shape[0]
    for p in range(6):
        hold = np.zeros(5)
        for k in range(60):
            n = Hr - 1 + k
            hold += sim.BUDGET_THB / math.exp(r["LF"][p, k]) * 0.2 * (1 - sim.FEE) / np.exp(r["LL"][p, n])
        ref = float((hold * np.exp(r["LL"][p, Hr - 1 + 60])).sum() * math.exp(r["LF"][p, 60]))
        assert r["H"][60]["arms"]["EQ"]["V"][p] == pytest.approx(ref, rel=1e-9)


def test_events_do_not_shift_the_mean_return():
    """เหตุการณ์ใหญ่เพิ่มความเสี่ยงแต่ต้องไม่เลื่อนค่าเฉลี่ยที่ตั้งไว้ (บั๊กจริงที่เจอ: ผลตอบแทนหุ้นเหลือ ~2% จากที่ตั้ง 7%)."""
    means = {}
    for em in (0.0, 1.0):
        cfg = sim.Config(world="rw", drift="mid", event_mult=em, P=4000, T=240, seed=21, debug=4000, arms=("EQ",))
        r = sim.run_chunk(_panel(), cfg)
        Hr = _panel()["live_me_logs"].shape[0]
        g = (r["LL"][:, Hr - 1 + 240, :] - r["LL"][:, Hr - 1, :]) / 20.0
        means[em] = np.nanmean(g[:, [0, 2, 3, 4]], axis=0)  # VOO QQQM XLV GLDM (SCHD เริ่มสั้นในแผงสังเคราะห์ก็ใช้ได้)
    assert np.allclose(means[0.0], means[1.0], atol=0.004), (means[0.0], means[1.0])


# ---------------------------------------------------------------- สูตรใหม่ (PREREG_NEW)
def test_cashflow_weights_match_the_project_cashflow_rebalance():
    """cf_weights_b ต้องเป็นคณิตเดียวกับ portfolio.cashflow_rebalance (ที่ผู้ใช้เลือกใช้เองใน Scorecard)."""
    rng = np.random.default_rng(9)
    t = rng.dirichlet(np.ones(5) * 3, size=200)
    V = rng.uniform(0, 500, size=(200, 5)) * (rng.random((200, 1)) > 0.1)
    m = np.full(200, 150.0)
    w = sim.cf_weights_b(V, m, t)
    assert np.allclose(w.sum(1), 1.0) and (w >= -1e-12).all()
    # ถือตามเป้าพอดี → ได้น้ำหนักเป้า (DCA ปกติ)
    V0 = t * 1000.0
    assert np.allclose(sim.cf_weights_b(V0, m, t), t)
    # ถืออะไรไม่มี → ได้น้ำหนักเป้า
    assert np.allclose(sim.cf_weights_b(np.zeros_like(V), m, t), t)
    # ไม่มีการขาย: เงินที่ใส่เข้ากองใดต้องไม่ติดลบ และกองที่เกินเป้าอยู่แล้วต้องได้ 0 เมื่อกองอื่นขาดมากพอ
    over = np.array([[900.0, 10.0, 10.0, 10.0, 10.0]])
    tt = np.full((1, 5), 0.2)
    ww = sim.cf_weights_b(over, np.array([100.0]), tt)[0]
    assert ww[0] == 0.0 and np.isclose(ww.sum(), 1.0)


def test_irr_closed_form_matches_explicit_sum():
    h = 120
    V = np.array([5000.0 * h, 800_000.0, 1_500_000.0, 400_000.0])
    irr = sim._irr(V, h)
    for v, r in zip(V, irr):
        m = (1 + r) ** (1 / 12) - 1
        fv = 5000.0 * sum((1 + m) ** (h - j) for j in range(h))
        assert fv == pytest.approx(v, rel=1e-6)


def test_new_arms_run_and_blend_sits_between_erc_and_equal_weight():
    panel = _panel()
    panel["chron"] = {"X": np.vstack(panel["pools"]), "S": np.vstack(panel["pool_S"]),
                      "days": np.concatenate(panel["pool_days"]), "reg": np.zeros(sum(len(p) for p in panel["pools"]), dtype=int)}
    for world in ("rw", "boot"):
        cfg = sim.Config(world=world, drift="mid", P=300, T=60, seed=5, round_units=False,
                         arms=("ERC", "EQ", "BLEND", "CF_ERC", "CF_EQ"), horizons=(60,))
        r = sim.run_chunk(panel, cfg)
        a = r["H"][60]["arms"]
        assert set(a) == {"ERC", "EQ", "BLEND", "CF_ERC", "CF_EQ"}
        for k in a:
            assert np.isfinite(a[k]["V"]).all() and (a[k]["V"] > 0).all()
        # ผสมครึ่งต่อครึ่งอยู่ระหว่างสองข้างเสมอในมูลค่ามัธยฐาน (ไม่ใช่เส้นทางรายตัว)
        lo, hi = sorted([np.median(a["ERC"]["V"]), np.median(a["EQ"]["V"])])
        assert lo * 0.97 <= np.median(a["BLEND"]["V"]) <= hi * 1.03
