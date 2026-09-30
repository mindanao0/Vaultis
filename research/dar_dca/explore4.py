"""Exploration #4 — can reversal be made robust to persistent premia?

EXPLORATION ONLY (<=1974-12). Pools: ind17, ind49, style, mixed. Starts 1941-07 (15y history).
Signals (all cross-sectionally centered within the universe, z-scored in `tilt`):
  amp          : AMP 5y value (baseline)
  amp_dadj10   : amp + 5 * (relative drift over years 5..15 ago)/10      (drift measured BEFORE the 5y window)
  amp_dadj15   : amp + 5 * (relative drift over the whole last 15y)/15
  amp_mom      : z(amp) + z(mom)  (AMP 2013 combo)
  rev5_minus_trend : -(5y rel return) + 0.5*(10y-ago..5y-ago rel return)  == amp_dadj10 in simple-return form
Also IC-by-horizon of amp and mom in the style pool.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import vsim as vs
from explore3 import build, pool_frame, report


def sig_amp_dadj(ctx, t, back_start: int, back_end: int, scale_years: float):
    """amp + scale_years * annual relative drift measured over months (t-1-back_end .. t-1-back_start)."""
    amp = vs.sig_amp(ctx, t)
    drift = (ctx.lp(t, 1 + back_start) - ctx.lp(t, 1 + back_end)) / ((back_end - back_start) / 12.0)
    return amp + scale_years * drift


def sig_combo(ctx, t):
    return vs.zscore_rows(vs.sig_amp(ctx, t)) + vs.zscore_rows(vs.sig_mom(ctx, t))


def main(pool: str = "ind17", months: int = 240, n_univ: int = 200) -> None:
    months = int(months)
    df = pool_frame(pool)
    ctx, starts = build(df, int(n_univ), months, "1941-07", 6, 13)
    arms = {
        "equal": vs.pol_equal,
        "amp_z0.5": vs.tilt(vs.sig_amp, 0.5, 0.2),
        "amp_dadj10_z0.5": vs.tilt(lambda c, t: sig_amp_dadj(c, t, 60, 180, 5.0), 0.5, 0.2),
        "amp_dadj15_z0.5": vs.tilt(lambda c, t: sig_amp_dadj(c, t, 0, 180, 5.0), 0.5, 0.2),
        "amp_mom_z0.5": vs.tilt(sig_combo, 0.5, 0.2),
        "mom_z0.5": vs.tilt(vs.sig_mom, 0.5, 0.2),
        "cf_amp_dadj10": vs.cf_rebalance(0.2, vs.value_target(lambda c, t: sig_amp_dadj(c, t, 60, 180, 5.0), 0.5)),
    }
    results = {a: vs.run(ctx, months, p) for a, p in arms.items()}
    report(results, ctx, starts, months, f"pool={pool} | pairs={len(ctx.start)} | {months}m DCA | starts 1941-07.. | exploration <=1974")


if __name__ == "__main__":
    main(*sys.argv[1:])
