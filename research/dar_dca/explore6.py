"""Exploration #7 — robustness of DAR z1.0 f0.2 to universe size N, DCA length, and a
minimum-dispersion z (for small N). EXPLORATION ONLY (<=1974-12)."""
from __future__ import annotations

import sys

import numpy as np

import vsim as vs
from explore3 import build, pool_frame
from explore5 import sig_dar, summarize


def tilt_minsd(signal, k: float, floor_frac: float, sd_min: float):
    def pol(t, H, ctx):
        B, N = H.shape
        x = signal(ctx, t)
        ok = ~np.isnan(x)
        cnt = ok.sum(axis=1, keepdims=True)
        mean = np.where(ok, x, 0).sum(axis=1, keepdims=True) / np.maximum(cnt, 1)
        var = np.where(ok, (x - mean) ** 2, 0).sum(axis=1, keepdims=True) / np.maximum(cnt, 1)
        sd = np.maximum(np.sqrt(var), sd_min)
        z = np.where(ok & (cnt >= 2), (x - mean) / sd, 0.0)
        w = np.maximum(1.0 + k * z, 0.0) / N
        return vs.floor_project(w, floor_frac / N)

    return pol


def main(pool: str = "mixed", months: int = 240, n_univ: int = 200) -> None:
    months, n_univ = int(months), int(n_univ)
    df = pool_frame(pool)
    for nsize in (2, 3, 5, 8):
        ctx, starts = build(df, n_univ, months, "1941-07", 6, 19 + nsize, k=nsize)
        arms = {
            "equal": vs.pol_equal,
            "z1.0_f0.2": vs.tilt(sig_dar, 1.0, 0.2),
            "z1.0_f0.2_sdmin0.15": tilt_minsd(sig_dar, 1.0, 0.2, 0.15),
        }
        results = {a: vs.run(ctx, months, p) for a, p in arms.items()}
        rows = summarize(results, ctx, starts, months)
        print(f"\npool={pool} N={nsize} pairs={len(ctx.start)} {months}m DCA (exploration)")
        for a, m, md, w, p10, p90, te, mx in rows:
            print(a.ljust(22), f"mean {m:6.2f}  med {md:6.2f}  win {w:5.1f}  p10 {p10:6.2f}  p90 {p90:6.2f}  TE {te:5.2f}")


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    main(*sys.argv[1:])
