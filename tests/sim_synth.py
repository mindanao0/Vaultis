# -*- coding: utf-8 -*-
"""ข้อมูลสังเคราะห์ของ simulation สำหรับเทสต์ (ออฟไลน์) — แผงสอบเทียบ + ข้อมูลดิบ (ราคา/FRED) รูปเดียวกับของจริง."""
from __future__ import annotations

import numpy as np
import pandas as pd

from simulation import data as sim_data
from simulation.universe import CORE_ASSETS

FIVE = [a.ticker for a in CORE_ASSETS]


def frame(rng, months, n, schd_short=0):
    r = rng.normal(0.006, 0.045, size=(months, n)) + rng.normal(0, 0.01, size=(months, 1))
    lv = np.exp(np.cumsum(r, axis=0)) * 50
    cols = FIVE if n == 5 else [f"F{i}" for i in range(n)]
    df = pd.DataFrame(lv, columns=cols, index=pd.date_range(end="2026-09-30", periods=months, freq="ME"))
    if schd_short and n >= 2:
        df.loc[df.index[:schd_short], cols[1]] = np.nan
    return df


def synthetic_panel(n=5, seed=0, hr=200):
    """แผงสังเคราะห์รูปเดียวกับที่ ``data.build_panel`` คืน (ทั้งตัววิจัยและเอนจินอ่านได้)."""
    rng = np.random.default_rng(seed)
    K, D = 4, n + 5
    funds = FIVE if n == 5 else [f"F{i}" for i in range(n)]
    kinds = [a.kind for a in CORE_ASSETS] if n == 5 else (["us_equity", "bond", "gold", "reit", "em_equity", "intl_equity", "us_growth", "other_equity"] * 3)[:n]
    iu = np.triu_indices(n)
    pools, pool_S, pool_days = [], [], []
    for k, m in enumerate((60, 20, 12, 15)):
        rows = rng.normal(0.0, 0.04, size=(m, D))
        rows[:, n] = rng.normal(0, 0.02, m)
        rows[:, n + 1] = 0.002 + rng.normal(0, 0.002, m)
        pools.append(rows)
        S = np.zeros((m, len(iu[0])))
        for i in range(m):
            a = rng.normal(0, 0.01, size=(21, n)); S[i] = (a.T @ a)[iu]
        pool_S.append(S); pool_days.append(np.full(m, 21.0))
    live = np.log(frame(rng, hr, n).to_numpy())
    live[:20, 1 if n > 1 else 0] = np.nan
    X = np.vstack(pools)
    return {
        "funds": funds, "kinds": kinds, "cols": funds + ["fx", "us_infl", "d_ffr", "d_10y", "oil"], "pools": pools,
        "trans": np.full((K, K), 0.1) + np.eye(K) * 0.6, "start_regime": 0, "live_me_logs": live,
        "first_valid": {f: int(np.isfinite(live[:, i]).argmax()) for i, f in enumerate(funds)}, "fx0": 33.5,
        "pool_S": pool_S, "pool_days": pool_days, "live_S": np.tile(((np.eye(n) * 1e-4)[iu])[None], (60, 1)), "live_days": np.full(60, 21.0),
        "th_fit": {"a": 0.0, "b": 1.0, "resid_sd_annual": 0.02},
        "events_measured": {"dotcom": {"VOO": -0.6, "QQQM": -1.7, "XLV": -0.2, "months": 31}, "fx_up_6m": 0.6, "fx_down_24m": -0.3,
                            "fx_up_6m_end": "1997-12-31", "fx_down_24m_end": "2000-01-31"},
        "start_levels": {"ffr": 3.75, "y10": 5.0, "oil": 90.0}, "thb_hist": np.zeros((60, n)),  # ตัววิจัยอ่านคีย์นี้ (ไม่ได้ใช้)
        "chron": {"X": X, "S": np.vstack(pool_S), "days": np.concatenate(pool_days), "reg": np.zeros(len(X), dtype=int)},
        "meta": {"as_of": "2026-09-30", "plan_month": "2026-10", "last_bar": "2026-10-02", "funds": funds, "kinds": kinds,
                 "guessed_kinds": [], "raw_sha256": "x", "fetched_at": "2026-10-05T09:00:00+07:00", "n_pool_months": len(X),
                 "live_months": hr, "dar_ready": hr >= 181, "yield_source": {f: "measured" for f in funds}},
        "stats": {}, "yields": {f: 0.02 for f in funds},
    }


def synthetic_raw(tickers=None, seed=0, extras=("BND", "VXUS")):
    rng = np.random.default_rng(seed)
    tickers = tickers or FIVE
    from simulation.universe import asset_for

    need = sim_data.required_tickers(tickers)
    for e in extras:  # กองเสริม + กองพี่ (เหมือนที่ fetch_raw ดึงไว้เสมอ)
        need += [e] + ([asset_for(e).calib_proxy] if asset_for(e).calib_proxy else [])
    need = list(dict.fromkeys(need))
    days = pd.bdate_range("1993-01-04", "2026-10-02")
    starts = {"VOO": "2010-09-09", "SPY": "1993-01-04", "SCHD": "2011-10-20", "DVY": "2003-11-07", "QQQM": "2020-10-13",
              "QQQ": "1999-03-10", "XLV": "1998-12-22", "GLDM": "2018-06-26", "GLD": "2004-11-18", "THB=X": "2003-12-01",
              "BND": "2007-04-10", "AGG": "2003-09-29", "VXUS": "2011-01-28", "VEU": "2007-03-08"}
    cols = {}
    common = rng.normal(0.0003, 0.008, size=len(days))
    for t in need:
        s0 = pd.Timestamp(starts.get(t, "2003-01-02"))
        r = common * (0.1 if t in ("GLD", "GLDM") else 1.0) + rng.normal(0.0002, 0.006, size=len(days))
        if t == "THB=X":
            r = rng.normal(0, 0.002, size=len(days))
        px = pd.Series(30.0 * np.exp(np.cumsum(r)), index=days)
        cols[t] = px[px.index >= s0]
    daily = pd.DataFrame(cols)
    me = pd.date_range("1947-01-01", "2026-08-01", freq="MS")
    fred = {
        # เงินเฟ้อ 2.4%/ปี ปกติ + ช่วง 2021-03..2023-04 ที่ ~6%/ปี (ให้มีเดือน regime "highinfl" จริงในช่วงสอบเทียบ)
        "CPIAUCSL": pd.Series(np.exp(np.cumsum(np.where((me >= "2021-03-01") & (me <= "2023-04-01"), 0.005, 0.002))) * 21.0, index=me),
        "FEDFUNDS": pd.Series(np.clip(3 + rng.normal(0, 0.2, len(me)).cumsum() * 0.05, 0.1, 9), index=me),
        "USREC": pd.Series(np.where(rng.random(len(me)) < 0.12, 1.0, 0.0), index=me),
    }
    dd = pd.bdate_range("1981-01-02", "2026-10-01")
    fred["DGS10"] = pd.Series(np.clip(4 + rng.normal(0, 0.03, len(dd)).cumsum() * 0.2, 0.5, 12), index=dd)
    fred["DCOILWTICO"] = pd.Series(np.exp(4 + rng.normal(0, 0.02, len(dd)).cumsum() * 0.2), index=dd)
    fred["DEXTHUS"] = pd.Series(np.exp(3.4 + rng.normal(0, 0.004, len(dd)).cumsum()), index=dd)
    vd = pd.bdate_range("1990-01-02", "2026-10-01")
    # VIX แกว่งเป็นคลื่น 12–35 (ไม่สุ่มเดิน): ให้ทุก regime (calm/stress/crisis) มีเดือนจริงในช่วงสอบเทียบแน่นอน ไม่ขึ้นกับ seed
    fred["VIXCLS"] = pd.Series(23.5 + 11.5 * np.sin(np.arange(len(vd)) / 90.0) + rng.normal(0, 0.5, len(vd)), index=vd)
    yrs = pd.to_datetime([f"{y}-01-01" for y in range(1960, 2026)])
    fred["FPCPITOTLZGTHA"] = pd.Series(rng.normal(3.0, 1.5, len(yrs)), index=yrs)
    # ปันผลรายไตรมาส ~0.5% ของราคา (ทอง = ไม่จ่าย → ซีรีส์ว่าง ซึ่งเป็นสถานะที่ถูกต้อง ไม่ใช่ข้อมูลหาย)
    divs = {}
    for t in list(tickers) + list(extras):
        if t in ("GLDM",):
            divs[t] = pd.Series(dtype=float)
            continue
        qd = pd.date_range("2012-03-15", "2026-09-15", freq="3MS") + pd.Timedelta(days=14)
        px = daily[t].dropna()
        qd = qd[qd >= px.index[0]]
        divs[t] = pd.Series([float(px.asof(d)) * 0.005 for d in qd], index=qd)
    return sim_data.RawInputs(daily=daily, fred=fred, tickers=list(tickers), fetched_at="2026-10-05T09:00:00+07:00", dividends=divs)


