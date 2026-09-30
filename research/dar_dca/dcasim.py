"""Monthly no-sell DCA simulator on return matrices (research scratch).

purchase at start of month t using info through end of month t-1, then holdings grow by (1+r_t).
Every arm: contribution 1.0 per month, all of it invested, every asset gets >= floor share.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

# policy(t, H, logp_hist, r_hist, ctx) -> purchase weights (N,) summing to 1
Policy = Callable[..., np.ndarray]


def apply_floor(w: np.ndarray, floor: float) -> np.ndarray:
    """Project nonneg weights onto {sum=1, w_i >= floor} by water-filling (keeps proportions above floor)."""
    n = len(w)
    w = np.maximum(np.asarray(w, dtype=float), 0.0)
    if w.sum() <= 0:
        return np.full(n, 1.0 / n)
    w = w / w.sum()
    if floor <= 0:
        return w
    fixed = np.zeros(n, dtype=bool)
    for _ in range(n):
        free = ~fixed
        budget = 1.0 - floor * fixed.sum()
        wf = w[free]
        wf = wf / wf.sum() * budget if wf.sum() > 0 else np.full(free.sum(), budget / free.sum())
        out = np.where(fixed, floor, 0.0)
        out[free] = wf
        low = (out < floor - 1e-15) & free
        if not low.any():
            return out
        fixed |= low
    return np.full(n, 1.0 / n)


def simulate(r: np.ndarray, start: int, months: int, policy: Policy, ctx: dict) -> dict:
    """r: (T, N) simple monthly returns (no NaN inside [start-warmup, start+months)).
    Returns terminal wealth, invested, and time-weighted monthly portfolio returns."""
    T, N = r.shape
    H = np.zeros(N)
    twr = []
    wlog = []
    for k in range(months):
        t = start + k
        w = policy(t, H.copy(), ctx)
        w = np.asarray(w, dtype=float)
        assert abs(w.sum() - 1) < 1e-9 and (w >= -1e-12).all(), (w, w.sum())
        H = H + w
        wlog.append(w)
        v_before = H.sum()
        H = H * (1.0 + r[t])
        twr.append(H.sum() / v_before - 1.0)
    return {"W": H.sum(), "H": H, "invested": float(months), "twr": np.array(twr), "w": np.array(wlog)}


# ----------------------------------------------------------------------------------- policies
def pol_equal(t, H, ctx):
    n = len(H)
    return np.full(n, 1.0 / n)


def zscore(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    ok = ~np.isnan(x)
    out = np.zeros_like(x)
    if ok.sum() >= 2:
        m = x[ok].mean()
        s = x[ok].std()
        if s > 0:
            out[ok] = (x[ok] - m) / s
    return out


def signal_val(ctx, t, lookback):
    """-(relative log return over the last `lookback` months ending at t-1); NaN if history short."""
    lp = ctx["logp"]  # (T, N) cumulative log price, lp[t] = level at END of month t
    if t - 1 - lookback < 0:
        return np.full(lp.shape[1], np.nan)
    return -(lp[t - 1] - lp[t - 1 - lookback])


def signal_val_amp(ctx, t):
    lp = ctx["logp"]
    if t - 1 - 66 < 0:
        return np.full(lp.shape[1], np.nan)
    old = np.log(np.exp(lp[t - 1 - 66 : t - 1 - 53]).mean(axis=0))
    return old - lp[t - 1]


def signal_mom(ctx, t):
    lp = ctx["logp"]
    if t - 13 < 0:
        return np.full(lp.shape[1], np.nan)
    return lp[t - 2] - lp[t - 13]


def make_tilt(signal_fn, k: float, floor: float):
    def pol(t, H, ctx):
        n = len(H)
        z = zscore(signal_fn(ctx, t))
        w = (1.0 / n) * np.maximum(1.0 + k * z, 0.0)
        return apply_floor(w, floor / n)

    return pol


def make_cf_rebalance(floor: float, target_fn=None):
    """Buy toward target holdings (default equal). floor = min share as fraction of 1/N."""

    def pol(t, H, ctx):
        n = len(H)
        tgt_w = np.full(n, 1.0 / n) if target_fn is None else target_fn(ctx, t, H)
        V = H.sum() + 1.0
        deficit = np.maximum(tgt_w * V - H, 0.0)
        fl = floor / n
        rem = 1.0 - n * fl
        if deficit.sum() <= 1e-15:
            w = np.full(n, fl) + rem * tgt_w
        else:
            # allocate `rem` to deficits without overshooting where possible
            d = deficit / deficit.sum()
            w = np.full(n, fl) + rem * d
        return w / w.sum()

    return pol


def make_value_target(signal_fn, k):
    def tgt(ctx, t, H):
        n = len(H)
        z = zscore(signal_fn(ctx, t))
        w = (1.0 / n) * np.maximum(1.0 + k * z, 0.05)
        return w / w.sum()

    return tgt


def make_winners(floor: float):
    def pol(t, H, ctx):
        n = len(H)
        if H.sum() <= 0:
            return np.full(n, 1.0 / n)
        return apply_floor(H / H.sum(), floor / n)

    return pol
