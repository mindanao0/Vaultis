"""Exploration #3 — signal shape, lookback, magnitude, holdings-awareness; several asset pools.

EXPLORATION DATA ONLY (<= 1974-12). Pools:
  ind17  : 17 FF industries
  ind49  : 49 FF industries (NaN-free pairs only)
  style  : MKT + D/P quintiles (+zero-div) + BE/ME quintiles + 6 size/BM   (overlapping, like ETFs)
  mixed  : ind17 + style
Metric: log(W_arm/W_equal) in % over the DCA; 'ann' = that / (avg holding years = months/24).
"""
from __future__ import annotations

import itertools
import sys

import numpy as np
import pandas as pd

import vsim as vs

END = pd.Period("1974-12", "M")


def pool_frame(name: str) -> pd.DataFrame:
    ind = pd.read_pickle("/scratch/cache/ff_ind.pkl")
    style = pd.read_pickle("/scratch/cache/ff_style.pkl")
    if name == "ind17":
        df = ind[17]
    elif name == "ind49":
        df = ind[49]
    elif name == "style":
        df = style
    elif name == "mixed":
        df = pd.concat([ind[17].add_prefix("I "), style], axis=1)
    else:
        raise ValueError(name)
    return df.loc[:END]


def build(df: pd.DataFrame, n_univ: int, months: int, first: str, step: int, seed: int, k: int = 5):
    r_all = df.values.astype(float)
    lp_all = np.cumsum(np.log1p(np.nan_to_num(r_all)), axis=0)
    lp_all[np.isnan(r_all)] = np.nan
    idx = df.index
    rng = np.random.default_rng(seed)
    M = r_all.shape[1]
    from math import comb
    n_univ = min(n_univ, comb(M, k))  # fewer possible universes than requested -> take them all
    U = []
    seen = set()
    while len(U) < n_univ:
        c = tuple(sorted(rng.choice(M, size=k, replace=False)))
        if c not in seen:
            seen.add(c)
            U.append(c)
    U = np.array(U)
    starts = list(range(idx.get_loc(pd.Period(first, "M")), len(idx) - months + 1, step))
    univ = np.repeat(U, len(starts), axis=0)
    start = np.tile(np.array(starts), len(U))
    ok = np.array([not np.isnan(r_all[s : s + months][:, u]).any() for s, u in zip(start, univ)])
    return vs.Ctx(r_all=np.nan_to_num(r_all), lp_all=lp_all, univ=univ[ok], start=start[ok]), starts


def centered(x):
    m = np.nanmean(x, axis=1, keepdims=True)
    return np.where(np.isnan(x), 0.0, x - m)


def raw_tilt(signal, lam: float, floor_frac: float):
    def pol(t, H, ctx):
        B, N = H.shape
        s = centered(signal(ctx, t))
        w = np.exp(lam * s) / N
        return vs.floor_project(w, floor_frac / N)

    return pol


def raw_target(signal, lam: float):
    def tgt(t, H, ctx):
        s = centered(signal(ctx, t))
        w = np.exp(lam * s)
        return w / w.sum(axis=1, keepdims=True)

    return tgt


def arms_def():
    amp = vs.sig_amp
    rv = lambda L: (lambda c, t: vs.sig_rev(c, t, L))
    return {
        "equal": vs.pol_equal,
        "cf_rebal": vs.cf_rebalance(0.2),
        "mom_z0.5": vs.tilt(vs.sig_mom, 0.5, 0.2),
        "rev36_z0.5": vs.tilt(rv(36), 0.5, 0.2),
        "rev48_z0.5": vs.tilt(rv(48), 0.5, 0.2),
        "rev60_z0.5": vs.tilt(rv(60), 0.5, 0.2),
        "rev84_z0.5": vs.tilt(rv(84), 0.5, 0.2),
        "amp_z0.5": vs.tilt(amp, 0.5, 0.2),
        "amp_z1.0": vs.tilt(amp, 1.0, 0.2),
        "amp_raw1": raw_tilt(amp, 1.0, 0.2),
        "amp_raw2": raw_tilt(amp, 2.0, 0.2),
        "amp_raw3": raw_tilt(amp, 3.0, 0.2),
        "cf_amp_raw1": vs.cf_rebalance(0.2, raw_target(amp, 1.0)),
        "cf_amp_raw2": vs.cf_rebalance(0.2, raw_target(amp, 2.0)),
        "cf_amp_z0.5": vs.cf_rebalance(0.2, vs.value_target(amp, 0.5)),
    }


def report(results, ctx, starts, months, title):
    base = results["equal"]["W"]
    yrs = months / 24.0
    print("\n" + title)
    print("arm".ljust(14), "mean%".rjust(7), "ann%".rjust(6), "med%".rjust(7), "win%".rjust(6), "p10%".rjust(7), "p90%".rjust(7), "t(st)".rjust(6), "TE%/y".rjust(6))
    for a, res in results.items():
        if a == "equal":
            continue
        x = np.log(res["W"] / base) * 100
        ps = np.array([x[ctx.start == s].mean() for s in starts if (ctx.start == s).any()])
        sd = ps.std(ddof=1)
        t = ps.mean() / (sd / np.sqrt(len(ps))) if sd > 0 else float("nan")
        te = (np.log1p(res["twr"]) - np.log1p(results["equal"]["twr"])).std(axis=1).mean() * np.sqrt(12) * 100
        print(a.ljust(14), f"{x.mean():7.2f}", f"{x.mean()/yrs:6.3f}", f"{np.median(x):7.2f}", f"{(x>0).mean()*100:6.1f}", f"{np.percentile(x,10):7.2f}", f"{np.percentile(x,90):7.2f}", f"{t:6.1f}", f"{te:6.2f}")


def main(pool: str = "ind17", months: int = 240, n_univ: int = 200) -> None:
    months = int(months)
    n_univ = int(n_univ)
    df = pool_frame(pool)
    ctx, starts = build(df, n_univ, months, "1936-07", 6, 11)
    arms = arms_def()
    results = {a: vs.run(ctx, months, p) for a, p in arms.items()}
    report(results, ctx, starts, months, f"pool={pool} | pairs={len(ctx.start)} | {months}m DCA | starts 1936-07.. step 6m | exploration <=1974")


if __name__ == "__main__":
    main(*sys.argv[1:])
