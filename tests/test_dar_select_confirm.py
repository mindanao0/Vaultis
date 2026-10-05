# -*- coding: utf-8 -*-
"""ท่อคำนวณยืนยันของ research/dar_select/confirm.py — ทดสอบด้วยข้อมูลสังเคราะห์ **ก่อน** ล็อก PREREG และรันบนข้อมูลยืนยันจริง.

ไฟล์นี้ตรวจแค่ว่าโค้ดทำสิ่งที่ PREREG บอก (สัญญาณ รายปี, เพดาน 25%, ตลาดที่ปิด = 0% ไม่ซื้อเพิ่ม, แปลง USD, เกณฑ์ผ่าน)
ไม่ได้แตะข้อมูล JST และไม่ได้ตัดสินว่ากฎผ่านหรือไม่
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PATH = Path(__file__).resolve().parents[1] / "research" / "dar_select" / "confirm.py"
_spec = importlib.util.spec_from_file_location("dar_select_confirm", _PATH)
cf = importlib.util.module_from_spec(_spec)
sys.modules["dar_select_confirm"] = cf
_spec.loader.exec_module(cf)


def _ann(years=60, n=6, seed=0, mu=0.06, sd=0.2):
    rng = np.random.default_rng(seed)
    idx = list(range(1900, 1900 + years))
    return pd.DataFrame(rng.normal(mu, sd, size=(years, n)).clip(-0.9, None), index=idx, columns=[f"M{i}" for i in range(n)])


# ---------------------------------------------------------------- สัญญาณรายปี (M1)
def test_annual_z_matches_a_hand_computation():
    ann = _ann(seed=1)
    lev = cf.levels_from_returns(ann)
    z = cf.annual_z(lev)
    i = 30
    vals = []
    for c in lev.columns:
        p = lev[c].to_numpy()[i - 15: i + 1]
        vals.append((math.log(p[10]) - math.log(p[15])) + 0.5 * (math.log(p[10]) - math.log(p[0])))
    x = np.array(vals)
    expect = (x - x.mean()) / max(x.std(), 0.15)
    assert np.allclose(z.iloc[i].to_numpy(), expect)
    assert z.iloc[:15].isna().all().all(), "ต้องมีประวัติ 16 ปีถึงจะมีสัญญาณ"


def test_a_gap_year_removes_the_signal_until_history_is_contiguous_again():
    ann = _ann(seed=2)
    ann.iloc[20, 0] = np.nan                       # ตลาด M0 ปิดปีที่ index 20
    z = cf.annual_z(cf.levels_from_returns(ann))
    assert z["M0"].iloc[20:36].isna().all() and z["M0"].iloc[36:].notna().all()
    assert z["M1"].iloc[20:36].notna().all()


# ---------------------------------------------------------------- นโยบาย
def test_policy_weights_equal_dar_all_and_topk():
    z = np.array([2.0, 1.0, 0.0, -1.0, -2.0, 0.5])
    avail = np.array([True, True, True, True, True, False])
    H = np.zeros(6)
    w_eq = cf.policy_weights("equal", z, None, avail, H, 5)
    assert w_eq.sum() == pytest.approx(1.0) and w_eq[5] == 0 and np.allclose(w_eq[avail], 0.2)
    w_all = cf.policy_weights("dar_all", z, None, avail, H, 5)
    assert w_all.sum() == pytest.approx(1.0) and (w_all[avail] >= 0.2 / 5 - 1e-12).all() and (w_all[avail] <= 1.5 / 5 + 1e-12).all()
    w_top = cf.policy_weights("dar_topk", z, None, avail, H, 3)
    assert set(np.where(w_top > 0)[0]) == {0, 1, 2}, "ต้องเลือก z สูงสุด 3 ตัวที่ซื้อได้"
    assert w_top.sum() == pytest.approx(1.0) and (w_top[:3] >= 0.2 / 3 - 1e-12).all() and (w_top[:3] <= 1.5 / 3 + 1e-12).all()


def test_concentration_guard_blocks_a_market_already_at_25_percent():
    z = np.array([3.0, 2.0, 1.0, 0.0])
    avail = np.ones(4, dtype=bool)
    H = np.array([30.0, 30.0, 20.0, 20.0])      # ตลาด 0 และ 1 ถือ 30% ≥ 25% แล้ว
    w = cf.policy_weights("dar_topk", z, None, avail, H, 2)
    assert w[0] == 0 and w[1] == 0 and set(np.where(w > 0)[0]) == {2, 3}
    H2 = np.array([20.0, 20.0, 30.0, 30.0])
    w2 = cf.policy_weights("dar_topk", z, None, avail, H2, 2)
    assert set(np.where(w2 > 0)[0]) == {0, 1}


def test_guard_is_lifted_for_a_year_when_every_buyable_market_is_blocked():
    z = np.array([1.0, 0.5, 0.0, -0.5])
    H = np.array([25.0, 25.0, 25.0, 25.0])      # ทุกตลาดถือ 25% พอดี = ติดเพดานหมด
    w = cf.policy_weights("dar_topk", z, None, np.ones(4, dtype=bool), H, 3)
    assert w.sum() == pytest.approx(1.0), "ต้องซื้อได้ ไม่ค้าง"
    w2 = cf.policy_weights("yield_topk", z, np.array([0.01, 0.02, 0.03, 0.04]), np.ones(4, dtype=bool), H, 2)
    assert set(np.where(w2 > 0)[0]) == {2, 3}


def test_yield_rule_ranks_by_yield_and_skips_markets_without_a_yield():
    z = np.zeros(5)
    dp = np.array([0.03, np.nan, 0.07, 0.05, 0.01])
    w = cf.policy_weights("yield_topk", z, dp, np.ones(5, dtype=bool), np.zeros(5), 2)
    assert set(np.where(w > 0)[0]) == {2, 3} and np.allclose(w[[2, 3]], 0.5)


# ---------------------------------------------------------------- DCA ห้ามขาย
def test_zero_returns_give_exactly_the_money_invested():
    A = np.zeros((40, 4)); Z = np.zeros((40, 4))
    for kind in ("equal", "dar_all", "dar_topk"):
        tw, H = cf.simulate_window(A, Z, None, 5, kind, 3)
        assert tw == pytest.approx(cf.HORIZON_YEARS), kind
        assert H.sum() == pytest.approx(tw)


def test_a_closed_market_is_not_bought_and_its_holding_stays_frozen():
    A = np.zeros((40, 3)); Z = np.zeros((40, 3))
    A[10:13, 0] = np.nan                        # ตลาด 0 ปิด 3 ปี
    A[:, 1] = 0.10
    tw, H = cf.simulate_window(A, Z, None, 5, "equal", 3)
    assert H[0] > 0 and tw > cf.HORIZON_YEARS
    # ปีที่ปิด ตลาด 0 ไม่ได้เงินใหม่: เงินใหม่ 3 ก้อนนั้นแบ่งให้ตลาดที่เหลือ
    tw2, H2 = cf.simulate_window(np.nan_to_num(A), Z, None, 5, "equal", 3)
    assert H[0] < H2[0]


def test_identical_markets_make_every_policy_equal():
    base = _ann(years=50, n=1, seed=3)
    ann = pd.concat([base.rename(columns={"M0": f"M{i}"}) for i in range(6)], axis=1)
    out = cf.run_pipeline(ann, None)
    for e in out["rules"].values():
        assert e["vs_equal"]["mean_pct"] == pytest.approx(0.0, abs=1e-9)
    assert out["dar_all_vs_equal"]["mean_pct"] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------- สถิติ + เกณฑ์ผ่าน
def test_newey_west_and_summary_basics():
    x = np.random.default_rng(0).normal(0, 1, 200)
    assert cf.newey_west_se(x, 0) == pytest.approx(x.std() / math.sqrt(len(x)), rel=1e-6)
    s = cf.summarize(np.full(30, 1.02), list(range(1900, 1930)))
    assert s["mean_pct"] == pytest.approx(2.0) and s["win_pct"] == 100.0 and s["p10_pct"] == pytest.approx(2.0)
    assert s["annual_excess_pct"] == pytest.approx((1.02 ** (1 / 20) - 1) * 100)


def test_verdict_requires_every_locked_criterion():
    good = {"vs_equal": {"mean_pct": 1.2, "win_pct": 65.0, "p10_pct": -4.0}, "vs_dar_all": {"mean_pct": 0.3}}
    assert cf.verdict({"rules": {"dar_topk_K5": good}})["dar_topk_K5"]["PASS"]
    for field, bad in (("mean_pct", 0.9), ("win_pct", 59.0), ("p10_pct", -5.1)):
        e = {"vs_equal": {**good["vs_equal"], field: bad}, "vs_dar_all": good["vs_dar_all"]}
        assert not cf.verdict({"rules": {"dar_topk_K5": e}})["dar_topk_K5"]["PASS"], field
    worse = {"vs_equal": good["vs_equal"], "vs_dar_all": {"mean_pct": -0.1}}
    assert not cf.verdict({"rules": {"dar_topk_K5": worse}})["dar_topk_K5"]["PASS"], "แพ้ DAR เอียงทุกกอง = ไม่ผ่าน"
    assert cf.verdict({"rules": {"dar_topk_K8": good}}) == {}, "K=8 เป็นผลรอง ไม่ใช้ตัดสิน"


# ---------------------------------------------------------------- JST loader (ไฟล์เล็กสังเคราะห์ ไม่ใช่ข้อมูลจริง)
def test_jst_loader_converts_to_usd_drops_the_us_and_limits_the_years(tmp_path):
    rows = []
    for c, fx0 in (("UK", 0.2), ("USA", 1.0), ("France", 5.0)):
        for y in range(1868, 1977):
            rows.append({"country": c, "year": y, "eq_tr": 0.10, "eq_dp": 0.04, "xrusd": fx0 * (1.0 + 0.01 * (y - 1868))})
    f = tmp_path / "fake_jst.dta"
    pd.DataFrame(rows).to_stata(f, write_index=False, version=118)
    usd, local, dp = cf.jst_usd_returns(str(f))
    assert list(usd.columns) == ["France", "UK"] and usd.index.min() == 1870 and usd.index.max() == 1974
    y = 1900
    fx = lambda yr: 0.2 * (1.0 + 0.01 * (yr - 1868))
    assert usd.loc[y, "UK"] == pytest.approx(1.10 * fx(y - 1) / fx(y) - 1.0)
    assert local.loc[y, "UK"] == pytest.approx(0.10)
    assert dp.loc[y, "UK"] == pytest.approx(0.04)
    assert not np.isnan(usd.loc[1870, "UK"]), "ปีแรกของช่วงต้องใช้ xrusd ของปี 1869 — ต้องคำนวณก่อนตัดช่วงปี ไม่ใช่หลัง"


# ---------------------------------------------------------------- ค่าคงที่ใน confirm.py ต้องตรงกับ PREREG ที่ล็อก
def test_confirm_constants_equal_the_locked_preregistration():
    import re

    text = (Path(_PATH).parent / "PREREG.md").read_text(encoding="utf-8")
    block = re.search(r"```locked-constants\n(.*?)```", text, re.S).group(1)
    locked = dict(line.split("=") for line in block.strip().splitlines())
    assert set(locked) == {"K_PRIMARY", "K_SECONDARY", "GUARD", "HORIZON_YEARS", "MIN_HISTORY_YEARS", "PASS_MEAN_PCT", "PASS_WIN_PCT", "PASS_P10_PCT"}
    for name, value in locked.items():
        assert getattr(cf, name) == pytest.approx(float(value)), f"{name} ใน confirm.py ไม่ตรงกับ PREREG ที่ล็อกไว้"


def test_the_design_step_never_touches_the_confirmation_data():
    src = (Path(_PATH).parent / "design.py").read_text(encoding="utf-8") + (Path(_PATH).parent / "design_data.py").read_text(encoding="utf-8")
    assert "JST" not in src.replace("ไม่มีการอ่านไฟล์ JST เลย", "").replace("(JST 1870–1974) ยังไม่ถูกเปิดดูค่าใด ๆ ในสคริปต์นี้", "") or "read_stata" not in src
    assert "read_stata" not in src and "macrohistory" not in src.lower()
