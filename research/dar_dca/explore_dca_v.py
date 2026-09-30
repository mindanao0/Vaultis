"""Exploration #2 (vectorised) — terminal wealth of no-sell DCA arms vs 1/N.

EXPLORATION DATA ONLY: FF industries (value-weighted), 1926-07..1974-12.
Batch = every (random 5-industry universe) x (start month, every 6 months) pair.
Metric: log(W_arm / W_equal) in %, over a `months`-long DCA.
"""
from __future__ import annotations

import itertools
import sys

import numpy as np
import pandas as pd

import vsim as vs

END = pd.Period("1974-12", "M")


def build_batch(n_ind: int, n_univ: int, months: int, first: str, step: int, seed: int, end: pd.Period = END):
    ind = pd.read_pickle("/scratch/cache/ff_ind.pkl")[n_ind].loc[:end]
    r_all = ind.values.astype(float)
    lp_all = np.cumsum(np.log1p(np.nan_to_num(r_all)), axis=0)
    lp_all[np.isnan(r_all)] = np.nan
    idx = ind.index
    rng = np.random.default_rng(seed)
    combos = list(itertools.combinations(range(r_all.shape[1]), 5))
    pick = rng.choice(len(combos), size=min(n_univ, len(combos)), replace=False)
    starts = list(range(idx.get_loc(pd.Period(first, "M")), len(idx) - months + 1, step))
    U = np.array([combos[i] for i in pick])
    univ = np.repeat(U, len(starts), axis=0)
    start = np.tile(np.array(starts), len(U))
    # drop pairs with NaN returns inside the DCA window
    ok = np.ones(len(start), dtype=bool)
    for b in range(len(start)):
        seg = r_all[start[b] : start[b] + months][:, univ[b]]
        if np.isnan(seg).any():
            ok[b] = False
    ctx = vs.Ctx(r_all=np.nan_to_num(r_all), lp_all=lp_all, univ=univ[ok], start=start[ok])
    return ctx, idx, starts


def report(results: dict, ctx: vs.Ctx, starts: list[int], title: str) -> None:
    base = results["equal"]["W"]
    print(title)
    print("arm".ljust(22), "mean%".rjust(8), "median%".rjust(8), "win%".rjust(6), "p10%".rjust(8), "p90%".rjust(8), "t(starts)".rjust(10), "TE%/y".rjust(7))
    for a, res in results.items():
        x = np.log(res["W"] / base) * 100
        ps = np.array([x[ctx.start == s].mean() for s in starts if (ctx.start == s).any()])
        sd = ps.std(ddof=1)
        t = ps.mean() / (sd / np.sqrt(len(ps))) if sd > 0 else float("nan")
        te = (np.log1p(res["twr"]) - np.log1p(results["equal"]["twr"])).std(axis=1).mean() * np.sqrt(12) * 100
        print(a.ljust(22), f"{x.mean():8.2f}", f"{np.median(x):8.2f}", f"{(x > 0).mean()*100:6.1f}", f"{np.percentile(x,10):8.2f}", f"{np.percentile(x,90):8.2f}", f"{t:10.1f}", f"{te:7.2f}")


def main(n_ind: int = 17, n_univ: int = 200, months: int = 240) -> None:
    ctx, idx, starts = build_batch(n_ind, n_univ, months, "1936-07", 6, 7)
    v10 = lambda c, t: vs.sig_rev(c, t, 120)
    v5 = lambda c, t: vs.sig_rev(c, t, 60)
    arms = {
        "equal": vs.pol_equal,
        "cf_rebal_f0.2": vs.cf_rebalance(0.2),
        "cf_rebal_f0.5": vs.cf_rebalance(0.5),
        "winners_f0.2": vs.winners(0.2),
        "val10_k0.5": vs.tilt(v10, 0.5, 0.2),
        "val10_k1.0": vs.tilt(v10, 1.0, 0.2),
        "val5_k0.5": vs.tilt(v5, 0.5, 0.2),
        "amp_k0.5": vs.tilt(vs.sig_amp, 0.5, 0.2),
        "amp_k1.0": vs.tilt(vs.sig_amp, 1.0, 0.2),
        "mom_k0.5": vs.tilt(vs.sig_mom, 0.5, 0.2),
        "cf_to_val10_k0.5": vs.cf_rebalance(0.2, vs.value_target(v10, 0.5)),
    }
    results = {a: vs.run(ctx, months, p) for a, p in arms.items()}
    report(results, ctx, starts, f"{n_ind} industries | {len(set(map(tuple, ctx.univ)))} universes x {len(starts)} starts | {months}m DCA | 1936-07..1974-12 (exploration)")


if __name__ == "__main__":
    main(*[int(a) for a in sys.argv[1:]])
