"""POST-HOC (hypothesis generation only — confirmation data already seen): does a per-fund max-weight
cap of 2x the equal share tame the gold-pool tail while keeping the equity-pool gain?"""
import sys

import numpy as np
import pandas as pd

import dar_formula as F
import vsim as vs
from conf_ff import OUT_START, SEED, make_ctx, sig_dar_strict, universes
from explore6 import tilt_minsd


def capped(pol, cap_mult):
    def p(t, H, ctx):
        B, N = H.shape
        w = pol(t, H, ctx)
        cap = cap_mult / N
        for _ in range(N):
            over = w > cap + 1e-15
            if not over.any():
                break
            excess = np.where(over, w - cap, 0).sum(axis=1, keepdims=True)
            w = np.where(over, cap, w)
            room = np.where(~over, 1, 0) * (w < cap - 1e-15)
            share = np.where(room, w, 0)
            ssum = share.sum(axis=1, keepdims=True)
            w = w + np.where(ssum > 0, share / np.where(ssum > 0, ssum, 1) * excess, 0)
        return w / w.sum(axis=1, keepdims=True)

    return p


def main(pool="mixed_gold", force="GOLD"):
    pools = pd.read_pickle("/scratch/cache/conf_pools.pkl")
    df = pools[pool]
    rng = np.random.default_rng(SEED)
    U = universes(df, 200, 5, None if force == "none" else force, rng)
    idx = df.index
    s0 = idx.get_loc(OUT_START)
    starts = list(range(s0, len(idx) - 240 + 1, 6))
    ctx = make_ctx(df, U, starts, 240)
    base = tilt_minsd(sig_dar_strict, F.K, F.FLOOR_FRAC, F.SD_MIN)
    arms = {"equal": vs.pol_equal, "DAR": base, "DAR_cap2": capped(base, 2.0), "DAR_cap1.5": capped(base, 1.5)}
    out = {a: vs.run(ctx, 240, p) for a, p in arms.items()}
    b = out["equal"]["W"]
    print(f"POST-HOC {pool}: rolling 20y from 1975, pairs={len(ctx.start)}")
    for a in arms:
        if a == "equal":
            continue
        x = np.log(out[a]["W"] / b) * 100
        print(f"  {a:10s} mean {x.mean():+6.2f}  med {np.median(x):+6.2f}  win {(x>0).mean()*100:5.1f}  p10 {np.percentile(x,10):+7.2f}  p90 {np.percentile(x,90):+6.2f}  maxW {out[a]['wmean'].max(axis=1).mean()*100:5.1f}")


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    main(*sys.argv[1:])
