"""CONFIRMATION on long-history pools — run only after PREREG.md is hashed and shown.

Outcomes use purchases from 1975-01 onward only (signals may read earlier prices: they were known then).
Arms: equal (1/N) vs DAR (locked, dar_formula constants); descriptive extras: AMP-only, MOM 12-1.
(a) rolling 20-year DCAs, starts every 6 months from 1975-01
(b) one continuous DCA per universe 1975-01..2026-08; monthly excess log TWR averaged over universes,
    Newey-West (lag 24) CI of the annualised mean.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

import dar_formula as F
import vsim as vs
from explore6 import tilt_minsd

OUT_START = pd.Period("1975-01", "M")
SEED = 2026


def sig_dar(ctx, t):
    amp = vs.sig_amp(ctx, t)
    return amp + F.DRIFT_COEF * (ctx.lp(t, 61) - ctx.lp(t, 181))


def sig_dar_strict(ctx, t):
    """DAR with the reference implementation's history rule: NaN unless L(180) exists."""
    s = sig_dar(ctx, t)
    return s  # lp(t,181) is NaN when history < 181 month-ends, which makes s NaN -> neutral


def nw_mean_ci(e: np.ndarray, lag: int = 24) -> tuple[float, float, float]:
    e = e[~np.isnan(e)]
    n = len(e)
    m = e.mean()
    u = e - m
    s = (u @ u) / n
    for l in range(1, lag + 1):
        s += 2 * (1 - l / (lag + 1)) * (u[l:] @ u[:-l]) / n
    se = np.sqrt(s / n)
    return m, m - 1.96 * se, m + 1.96 * se


def universes(df: pd.DataFrame, n_univ: int, k: int, force: str | None, rng) -> np.ndarray:
    cols = list(df.columns)
    base = [i for i, c in enumerate(cols) if c != force]
    out, seen = [], set()
    kk = k - (1 if force else 0)
    from math import comb
    n_univ = min(n_univ, comb(len(base), kk))
    while len(out) < n_univ:
        c = tuple(sorted(rng.choice(base, size=kk, replace=False)))
        if force:
            c = tuple(sorted(c + (cols.index(force),)))
        if c not in seen:
            seen.add(c)
            out.append(c)
    return np.array(out)


def make_ctx(df: pd.DataFrame, U: np.ndarray, starts: list[int], months: int) -> vs.Ctx:
    r_all = df.values.astype(float)
    lp_all = np.cumsum(np.log1p(np.nan_to_num(r_all)), axis=0)
    lp_all[np.isnan(r_all)] = np.nan
    # a series that starts late: levels before its first return must be NaN (no fabricated history)
    for j in range(r_all.shape[1]):
        first = np.argmax(~np.isnan(r_all[:, j]))
        lp_all[:first, j] = np.nan
    univ = np.repeat(U, len(starts), axis=0)
    start = np.tile(np.array(starts), len(U))
    ok = np.array([not np.isnan(r_all[s : s + months][:, u]).any() for s, u in zip(start, univ)])
    return vs.Ctx(r_all=np.nan_to_num(r_all), lp_all=lp_all, univ=univ[ok], start=start[ok])


def arms():
    return {
        "equal": vs.pol_equal,
        "DAR": tilt_minsd(sig_dar_strict, F.K, F.FLOOR_FRAC, F.SD_MIN),
        "AMP_only": tilt_minsd(vs.sig_amp, F.K, F.FLOOR_FRAC, F.SD_MIN),
        "MOM_12_1": tilt_minsd(vs.sig_mom, F.K, F.FLOOR_FRAC, F.SD_MIN),
    }


def cross_check(df: pd.DataFrame, U: np.ndarray, t0: int, n: int = 12) -> float:
    """max |w_vsim - w_reference| over a sample of universes and months."""
    r_all = df.values.astype(float)
    worst = 0.0
    for u in U[:n]:
        ctx = make_ctx(df, np.array([u]), [t0], 1)
        for k in range(0, 120, 7):
            t = np.array([t0 + k])
            w_v = tilt_minsd(sig_dar_strict, F.K, F.FLOOR_FRAC, F.SD_MIN)(t, np.zeros((1, len(u))), ctx)[0]
            sub = df.iloc[: t0 + k, list(u)]
            lvl = np.exp(np.log1p(sub).cumsum())
            lvl = lvl.where(sub.notna())
            w_r = F.dar_weights(lvl).values
            worst = max(worst, float(np.abs(w_v - w_r).max()))
    return worst


def run_pool(name: str, n_univ: int = 200, k: int = 5, force: str | None = None) -> dict:
    pools = pd.read_pickle("/scratch/cache/conf_pools.pkl")
    df = pools[name]
    rng = np.random.default_rng(SEED)
    U = universes(df, n_univ, k, force, rng)
    idx = df.index
    s0 = idx.get_loc(OUT_START)
    res: dict = {"pool": name, "n_univ": int(n_univ), "k": k, "force": force}
    res["xcheck_max_abs_diff"] = cross_check(df, U, s0)
    # (a) rolling 20-year windows
    months = 240
    starts = list(range(s0, len(idx) - months + 1, 6))
    ctx = make_ctx(df, U, starts, months)
    out = {a: vs.run(ctx, months, p) for a, p in arms().items()}
    base = out["equal"]["W"]
    roll = {}
    for a in out:
        if a == "equal":
            continue
        x = np.log(out[a]["W"] / base) * 100
        roll[a] = {
            "pairs": int(len(x)),
            "mean": float(x.mean()),
            "median": float(np.median(x)),
            "win": float((x > 0).mean() * 100),
            "p10": float(np.percentile(x, 10)),
            "p90": float(np.percentile(x, 90)),
            "by_start_mean": [float(x[ctx.start == s].mean()) for s in starts if (ctx.start == s).any()],
        }
    res["rolling_20y"] = roll
    res["rolling_starts"] = [str(idx[s]) for s in starts]
    # (b) continuous DCA from 1975-01 to the end
    months_c = len(idx) - s0
    ctx_c = make_ctx(df, U, [s0], months_c)
    out_c = {a: vs.run(ctx_c, months_c, p) for a, p in arms().items()}
    cont = {}
    eq_lr = np.log1p(out_c["equal"]["twr"])
    for a in out_c:
        if a == "equal":
            continue
        ex = np.log1p(out_c[a]["twr"]) - eq_lr  # (U, months)
        e = ex.mean(axis=0)
        m, lo, hi = nw_mean_ci(e)
        tw = np.log(out_c[a]["W"] / out_c["equal"]["W"]) * 100
        cont[a] = {
            "universes": int(len(tw)),
            "ann_excess_pct": float(m * 1200),
            "ci95_low": float(lo * 1200),
            "ci95_high": float(hi * 1200),
            "terminal_mean": float(tw.mean()),
            "terminal_median": float(np.median(tw)),
            "terminal_win": float((tw > 0).mean() * 100),
            "sub_1975_2000_ann": float(e[: 312].mean() * 1200),
            "sub_2001_2026_ann": float(e[312:].mean() * 1200),
            "cum_excess_path": [float(v) for v in np.cumsum(e) * 100],
            "avg_weights": [float(v) for v in out_c[a]["wmean"].mean(axis=0)],
        }
    res["continuous"] = cont
    res["continuous_months"] = int(months_c)
    res["continuous_span"] = [str(idx[s0]), str(idx[-1])]
    return res


def main(which: str = "all") -> None:
    plan = {
        "P1_mixed": ("mixed", 5, None),
        "S1_ind17": ("ind17", 5, None),
        "S2_ind49": ("ind49", 5, None),
        "S3_style": ("style", 5, None),
        "S4_mixed_gold": ("mixed_gold", 5, "GOLD"),
    }
    keys = list(plan) if which == "all" else [which]
    allres = {}
    for key in keys:
        name, k, force = plan[key]
        r = run_pool(name, 200, k, force)
        allres[key] = r
        c = r["continuous"]["DAR"]
        rr = r["rolling_20y"]["DAR"]
        print(f"{key}: xcheck={r['xcheck_max_abs_diff']:.2e} | rolling20y DAR mean {rr['mean']:+.2f}% win {rr['win']:.1f}% "
              f"p10 {rr['p10']:+.2f} | continuous ann {c['ann_excess_pct']:+.3f}%/y CI [{c['ci95_low']:+.3f}, {c['ci95_high']:+.3f}] "
              f"terminal mean {c['terminal_mean']:+.2f}% win {c['terminal_win']:.1f}%")
        for a in ("AMP_only", "MOM_12_1"):
            ca, ra = r["continuous"][a], r["rolling_20y"][a]
            print(f"    {a}: rolling mean {ra['mean']:+.2f}% win {ra['win']:.1f}% | cont ann {ca['ann_excess_pct']:+.3f} CI [{ca['ci95_low']:+.3f},{ca['ci95_high']:+.3f}]")
        with open(f"/scratch/results/conf_{key}.json", "w") as fh:
            json.dump(r, fh)


if __name__ == "__main__":
    import os
    import warnings

    warnings.filterwarnings("ignore")
    os.makedirs("/scratch/results", exist_ok=True)
    main(*sys.argv[1:])
