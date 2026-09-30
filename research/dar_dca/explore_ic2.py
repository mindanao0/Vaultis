"""Exploration #6 — horizon term structure of AMP vs DAR vs momentum, per pool (<=1974-12 only).

DAR_t = log(mean level over months t-66..t-54) - log level_t + 0.5*(log level_{t-60} - log level_{t-180})
(signal known at END of month t; target = relative cumulative log return over (t, t+h]).
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from explore_ic import fm_slopes, nw_tstat, rank_ic
from explore3 import pool_frame

END = pd.Period("1974-12", "M")
HORIZONS = [1, 6, 12, 24, 36, 60, 84, 120]


def main(pool: str = "mixed") -> None:
    df = pool_frame(pool)
    r = df.loc[:END]
    lr = np.log1p(r)
    logp = lr.cumsum().where(r.notna())
    lvl = np.exp(logp)
    amp = np.log(lvl.shift(54).rolling(13).mean()) - logp
    dar = amp + 0.5 * (logp.shift(60) - logp.shift(180))
    mom = logp.shift(1) - logp.shift(12)
    sigs = {"amp": amp, "dar": dar, "mom_12_1": mom, "older_5to15": logp.shift(60) - logp.shift(180)}
    print(f"\npool={pool} (exploration <=1974)   cell = FM slope (NW t) | rank IC")
    print("signal".ljust(12) + "".join(f"h={h}".rjust(17) for h in HORIZONS))
    for name, sig in sigs.items():
        cells = []
        for h in HORIZONS:
            fwd = lr.rolling(h).sum().shift(-h)
            fwd = fwd.sub(fwd.mean(axis=1), axis=0)
            valid = [t for t in fwd.index if t + h <= END]
            sl = fm_slopes(sig.loc[valid], fwd.loc[valid])
            ic = rank_ic(sig.loc[valid], fwd.loc[valid])
            cells.append(f"{np.nanmean(sl):+.3f}({nw_tstat(sl, h):+.1f})|{np.nanmean(ic):+.2f}")
        print(name.ljust(12) + "".join(c.rjust(17) for c in cells))
    # dispersion of DAR (cross-sectional std), to translate slopes into weights
    print("median cross-sectional std of DAR:", float(np.nanmedian(dar.std(axis=1))), " of amp:", float(np.nanmedian(amp.std(axis=1))))


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    main(*sys.argv[1:])
