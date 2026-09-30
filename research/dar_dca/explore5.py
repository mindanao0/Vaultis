"""Exploration #5 — sizing and mapping of the drift-adjusted reversal (DAR) signal.

EXPLORATION ONLY (<=1974-12). Signal: DAR = amp + 0.5 * (rel log return from 15y ago to 5y ago).
Mappings: z-linear (1 + k z)/N with floor; raw-exp exp(lam * centered DAR)/N with floor.
Growth-optimal criterion = mean log(W/W_equal) (that IS E[log W] difference).
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import vsim as vs
from explore3 import build, pool_frame, raw_tilt


def sig_dar(ctx, t):
    amp = vs.sig_amp(ctx, t)
    older = ctx.lp(t, 61) - ctx.lp(t, 181)
    return amp + 0.5 * older


def summarize(results, ctx, starts, months):
    base = results["equal"]["W"]
    rows = []
    for a, res in results.items():
        if a == "equal":
            continue
        x = np.log(res["W"] / base) * 100
        te = (np.log1p(res["twr"]) - np.log1p(results["equal"]["twr"])).std(axis=1).mean() * np.sqrt(12) * 100
        rows.append((a, x.mean(), np.median(x), (x > 0).mean() * 100, np.percentile(x, 10), np.percentile(x, 90), te, res["wmean"].max(axis=1).mean() * 100))
    return rows


def main(pool: str = "mixed", months: int = 240, n_univ: int = 200, nsize: int = 5) -> None:
    months, n_univ, nsize = int(months), int(n_univ), int(nsize)
    df = pool_frame(pool)
    ctx, starts = build(df, n_univ, months, "1941-07", 6, 17, k=nsize)
    arms = {"equal": vs.pol_equal}
    for fl in (0.2, 0.5):
        for k in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0):
            arms[f"z{k}_f{fl}"] = vs.tilt(sig_dar, k, fl)
        for lam in (2.0, 4.0, 8.0):
            arms[f"exp{lam:g}_f{fl}"] = raw_tilt(sig_dar, lam, fl)
    results = {a: vs.run(ctx, months, p) for a, p in arms.items()}
    rows = summarize(results, ctx, starts, months)
    print(f"\npool={pool} N={nsize} pairs={len(ctx.start)} {months}m DCA starts 1941-07.. (exploration)")
    print("arm".ljust(12), "mean%".rjust(7), "med%".rjust(7), "win%".rjust(6), "p10%".rjust(7), "p90%".rjust(7), "TE%/y".rjust(6), "avgMaxW%".rjust(9))
    for a, m, md, w, p10, p90, te, mx in rows:
        print(a.ljust(12), f"{m:7.2f}", f"{md:7.2f}", f"{w:6.1f}", f"{p10:7.2f}", f"{p90:7.2f}", f"{te:6.2f}", f"{mx:9.1f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
