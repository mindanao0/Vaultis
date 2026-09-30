"""ROUND-2 CONFIRMATION — run only after PREREG_v2.md is hashed and shown.

C1 (primary): international country markets (20 countries; Malaysia excluded: < 25 years of data),
    random 5-country universes.
C2 (veto test): 4 random countries + 1 random commodity (World Bank; gold excluded — already spent).
Arms: equal (1/N) | DAR v1 (round-1 locked) | DAR v2 (v1 + ceiling 1.5/N).
(a) rolling 20-year DCAs, starts every 6 months from the first month every pool member can have a signal
(b) one continuous DCA per universe over the whole outcome window; Newey–West (24) CI of annual excess.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

import dar_formula as F
import dar_formula_v2 as F2
import vsim as vs
from conf_ff import make_ctx, nw_mean_ci, sig_dar_strict
from explore6 import tilt_minsd
from posthoc_cap import capped

SEED = 7202
OUT_START = pd.Period("1990-02", "M")  # first month with 181 month-ends for the 1975 countries


def pools():
    d = pd.read_pickle("/scratch/cache/conf2_pools.pkl")
    intl = d["intl"].drop(columns=["Malaysia"])
    cm = d["cmdty"]
    return intl, cm


def arms():
    v1 = tilt_minsd(sig_dar_strict, F.K, F.FLOOR_FRAC, F.SD_MIN)
    return {"equal": vs.pol_equal, "DAR_v1": v1, "DAR_v2": capped(v1, F2.CAP_MULT)}


def sample(cols_a, n_univ, k_a, cols_b, k_b, rng):
    from math import comb

    n_univ = min(n_univ, comb(len(cols_a), k_a) * max(1, comb(len(cols_b), k_b) if k_b else 1))
    out, seen = [], set()
    while len(out) < n_univ:
        a = tuple(sorted(rng.choice(cols_a, size=k_a, replace=False)))
        b = tuple(sorted(rng.choice(cols_b, size=k_b, replace=False))) if k_b else ()
        c = a + b
        if c not in seen:
            seen.add(c)
            out.append(c)
    return np.array(out)


def cross_check(df, U, t0):
    """Returns (max abs diff, universes checked). Universes with a member that has no return somewhere in
    [t0, t0+121) are skipped (make_ctx drops them); fix of a crash found before any result was produced."""
    worst = 0.0
    checked = 0
    pol = arms()["DAR_v2"]
    for u in U:
        if checked >= 12:
            break
        ctx = make_ctx(df, np.array([u]), [t0], 121)
        if len(ctx.start) == 0:
            continue
        checked += 1
        for k in range(0, 120, 7):
            t = np.array([t0 + k])
            w_v = pol(t, np.zeros((1, len(u))), ctx)[0]
            sub = df.iloc[: t0 + k, list(u)]
            lvl = np.exp(np.log1p(sub).cumsum()).where(sub.notna())
            w_r = F2.dar_weights_v2(lvl).values
            worst = max(worst, float(np.abs(w_v - w_r).max()))
    return worst, checked


def run(name, df, U, end_label):
    idx = df.index
    s0 = idx.get_loc(OUT_START)
    xc, xn = cross_check(df, U, s0)
    res = {"pool": name, "universes": int(len(U)), "xcheck_v2": xc, "xcheck_n": xn}
    months = 240
    starts = list(range(s0, len(idx) - months + 1, 6))
    ctx = make_ctx(df, U, starts, months)
    out = {a: vs.run(ctx, months, p) for a, p in arms().items()}
    base = out["equal"]["W"]
    res["rolling_starts"] = [str(idx[s]) for s in starts]
    res["pairs"] = int(len(ctx.start))
    for a in ("DAR_v1", "DAR_v2"):
        x = np.log(out[a]["W"] / base) * 100
        res[a] = {"roll_mean": float(x.mean()), "roll_median": float(np.median(x)), "roll_win": float((x > 0).mean() * 100),
                  "roll_p10": float(np.percentile(x, 10)), "roll_p90": float(np.percentile(x, 90)),
                  "by_start": [float(x[ctx.start == s].mean()) for s in starts if (ctx.start == s).any()]}
    months_c = len(idx) - s0
    ctx_c = make_ctx(df, U, [s0], months_c)
    out_c = {a: vs.run(ctx_c, months_c, p) for a, p in arms().items()}
    eq = np.log1p(out_c["equal"]["twr"])
    for a in ("DAR_v1", "DAR_v2"):
        e = (np.log1p(out_c[a]["twr"]) - eq).mean(axis=0)
        m, lo, hi = nw_mean_ci(e)
        tw = np.log(out_c[a]["W"] / out_c["equal"]["W"]) * 100
        res[a].update({"cont_ann": float(m * 1200), "cont_lo": float(lo * 1200), "cont_hi": float(hi * 1200),
                       "cont_universes": int(len(tw)), "term_mean": float(tw.mean()), "term_win": float((tw > 0).mean() * 100),
                       "avg_max_weight": float(out_c[a]["wmean"].max(axis=1).mean())})
    res["span"] = [str(idx[s0]), end_label]
    return res


def main(which="all"):
    intl, cm = pools()
    rng = np.random.default_rng(SEED)
    out = {}
    if which in ("all", "C1"):
        df = intl.loc[:"2025-12"]
        U = sample(list(range(df.shape[1])), 200, 5, [], 0, rng)
        out["C1_intl"] = run("C1_intl", df, U, "2025-12")
    if which in ("all", "C2"):
        df = pd.concat([intl, cm], axis=1).loc[:"2024-12"]
        nc = intl.shape[1]
        U = sample(list(range(nc)), 200, 4, list(range(nc, df.shape[1])), 1, rng)
        out["C2_intl_cmdty"] = run("C2_intl_cmdty", df, U, "2024-12")
    for k, r in out.items():
        print(f"{k}: universes={r['universes']} pairs={r['pairs']} xcheck_v2={r['xcheck_v2']:.1e} (n={r['xcheck_n']}) span={r['span']}")
        for a in ("DAR_v1", "DAR_v2"):
            d = r[a]
            print(f"   {a}: rolling20y mean {d['roll_mean']:+.2f}% win {d['roll_win']:.1f}% p10 {d['roll_p10']:+.2f} | "
                  f"continuous ann {d['cont_ann']:+.3f}%/y CI [{d['cont_lo']:+.3f}, {d['cont_hi']:+.3f}] terminal {d['term_mean']:+.2f}% win {d['term_win']:.1f}%")
        with open(f"/scratch/results/conf2_{k}.json", "w") as fh:
            json.dump(r, fh)


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    main(*sys.argv[1:])
