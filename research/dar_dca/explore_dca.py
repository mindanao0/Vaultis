"""Exploration #2 — terminal wealth of no-sell DCA arms vs 1/N on random 5-industry universes.

EXPLORATION DATA ONLY: 17 FF industries, 1926-07..1974-12.
Output per arm: log(W_arm / W_equal) across (universe, start) pairs.
"""
from __future__ import annotations

import itertools
import sys

import numpy as np
import pandas as pd

import dcasim as ds

END = pd.Period("1974-12", "M")


def main(n_ind: int = 17, n_univ: int = 200, months: int = 240, seed: int = 7) -> None:
    ind = pd.read_pickle("/scratch/cache/ff_ind.pkl")[n_ind].loc[:END]
    cols = list(ind.columns)
    r_all = ind.values
    idx = ind.index
    rng = np.random.default_rng(seed)
    combos = list(itertools.combinations(range(len(cols)), 5))
    pick = rng.choice(len(combos), size=min(n_univ, len(combos)), replace=False)
    first_start = idx.get_loc(pd.Period("1936-07", "M"))
    last_start = len(idx) - months
    starts = list(range(first_start, last_start + 1, 6))
    arms = {
        "equal": ds.pol_equal,
        "cf_rebal_f0.2": ds.make_cf_rebalance(0.2),
        "winners_f0.2": ds.make_winners(0.2),
        "val10_k0.5": ds.make_tilt(lambda c, t: ds.signal_val(c, t, 120), 0.5, 0.2),
        "val10_k1.0": ds.make_tilt(lambda c, t: ds.signal_val(c, t, 120), 1.0, 0.2),
        "valamp_k0.5": ds.make_tilt(ds.signal_val_amp, 0.5, 0.2),
        "valamp_k1.0": ds.make_tilt(ds.signal_val_amp, 1.0, 0.2),
        "mom_k0.5": ds.make_tilt(ds.signal_mom, 0.5, 0.2),
        "cf_to_val10_k0.5": ds.make_cf_rebalance(0.2, ds.make_value_target(lambda c, t: ds.signal_val(c, t, 120), 0.5)),
    }
    res = {a: [] for a in arms}
    per_start = {a: {s: [] for s in starts} for a in arms}
    for ci in pick:
        cidx = list(combos[ci])
        r = r_all[:, cidx]
        lp = np.cumsum(np.log1p(r), axis=0)
        ctx = {"logp": lp}
        for s in starts:
            base = None
            for a, pol in arms.items():
                out = ds.simulate(r, s, months, pol, ctx)
                if a == "equal":
                    base = out["W"]
                lr = np.log(out["W"] / base) if base else 0.0
                res[a].append(lr)
                per_start[a][s].append(lr)
    print(f"{n_ind} industries, {len(pick)} universes x {len(starts)} starts, {months}m DCA, 1936-07..1974-12")
    print("arm".ljust(20), "mean%".rjust(8), "median%".rjust(8), "win%".rjust(6), "p10%".rjust(8), "p90%".rjust(8), "t(starts)".rjust(10))
    for a in arms:
        x = np.array(res[a]) * 100
        ps = np.array([np.mean(per_start[a][s]) for s in starts]) * 100
        # crude t across start-averages (overlapping windows => optimistic); report anyway
        t = ps.mean() / (ps.std(ddof=1) / np.sqrt(len(ps))) if ps.std(ddof=1) > 0 else float("nan")
        print(a.ljust(20), f"{x.mean():8.2f}", f"{np.median(x):8.2f}", f"{(x > 0).mean()*100:6.1f}", f"{np.percentile(x,10):8.2f}", f"{np.percentile(x,90):8.2f}", f"{t:10.1f}")
    print("(terminal-wealth log ratio vs equal, in %; 20y DCA)")


if __name__ == "__main__":
    main(*[int(a) for a in sys.argv[1:]])
