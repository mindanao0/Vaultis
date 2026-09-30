"""Vectorised no-sell DCA simulator: a batch of B (universe, start) pairs advances together.

Conventions
- r_all: (T, M) simple monthly returns of M candidate assets (NaN allowed outside used windows)
- lp_all: (T, M) cumulative log level at END of month t
- batch: univ (B, N) asset indices, start (B,) month index of the first purchase
- purchase at the start of month t (info through end of t-1); holdings then grow by (1 + r[t])
- contribution = 1.0 per month; every policy returns (B, N) weights, rows sum to 1, >= floor
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


def floor_project(w: np.ndarray, floor: float) -> np.ndarray:
    """Rows: nonneg -> sum 1 with each entry >= floor, keeping proportions among entries above floor."""
    w = np.maximum(w, 0.0)
    s = w.sum(axis=1, keepdims=True)
    n = w.shape[1]
    w = np.where(s > 0, w / np.where(s > 0, s, 1), 1.0 / n)
    if floor <= 0:
        return w
    fixed = np.zeros_like(w, dtype=bool)
    out = w
    for _ in range(n):
        budget = 1.0 - floor * fixed.sum(axis=1, keepdims=True)
        free_w = np.where(fixed, 0.0, w)
        fs = free_w.sum(axis=1, keepdims=True)
        nfree = (~fixed).sum(axis=1, keepdims=True)
        scaled = np.where(fs > 0, free_w / np.where(fs > 0, fs, 1) * budget, budget / np.maximum(nfree, 1))
        out = np.where(fixed, floor, scaled)
        low = (~fixed) & (out < floor - 1e-15)
        if not low.any():
            break
        fixed |= low
    return out


def zscore_rows(x: np.ndarray) -> np.ndarray:
    ok = ~np.isnan(x)
    cnt = ok.sum(axis=1, keepdims=True)
    xm = np.where(ok, x, 0.0)
    mean = xm.sum(axis=1, keepdims=True) / np.maximum(cnt, 1)
    var = (np.where(ok, (x - mean) ** 2, 0.0)).sum(axis=1, keepdims=True) / np.maximum(cnt, 1)
    sd = np.sqrt(var)
    z = np.where(ok & (sd > 0) & (cnt >= 2), (x - mean) / np.where(sd > 0, sd, 1), 0.0)
    return z


@dataclass
class Ctx:
    r_all: np.ndarray
    lp_all: np.ndarray
    univ: np.ndarray
    start: np.ndarray

    def lp(self, t: np.ndarray, lag: int) -> np.ndarray:
        """log level at end of month (t - lag), gathered per batch row -> (B, N); NaN if before data."""
        tt = t - lag
        out = np.full(self.univ.shape, np.nan)
        ok = tt >= 0
        if ok.any():
            out[ok] = self.lp_all[tt[ok][:, None], self.univ[ok]]
        return out

    def ret(self, t: np.ndarray) -> np.ndarray:
        return self.r_all[t[:, None], self.univ]


Policy = Callable[[np.ndarray, np.ndarray, Ctx], np.ndarray]


def run(ctx: Ctx, months: int, policy: Policy, record_w: bool = False) -> dict:
    B, N = ctx.univ.shape
    H = np.zeros((B, N))
    twr = np.zeros((B, months))
    wsum = np.zeros((B, N))
    for k in range(months):
        t = ctx.start + k
        w = policy(t, H, ctx)
        H = H + w
        wsum += w
        vb = H.sum(axis=1)
        H = H * (1.0 + ctx.ret(t))
        twr[:, k] = H.sum(axis=1) / vb - 1.0
    return {"W": H.sum(axis=1), "H": H, "twr": twr, "wmean": wsum / months}


# ------------------------------------------------------------------ signals (B, N) at purchase month t
def sig_rev(ctx: Ctx, t: np.ndarray, lookback: int) -> np.ndarray:
    """-(log return over the `lookback` months ending at end of t-1)."""
    return -(ctx.lp(t, 1) - ctx.lp(t, 1 + lookback))


def sig_amp(ctx: Ctx, t: np.ndarray) -> np.ndarray:
    """AMP value: log(mean level over ends of months t-67..t-55) - log level at end of t-1."""
    levels = np.stack([np.exp(ctx.lp(t, 1 + L)) for L in range(54, 67)], axis=0)
    return np.log(np.nanmean(levels, axis=0)) - ctx.lp(t, 1)


def sig_mom(ctx: Ctx, t: np.ndarray) -> np.ndarray:
    return ctx.lp(t, 2) - ctx.lp(t, 13)


# ------------------------------------------------------------------ policies
def pol_equal(t, H, ctx):
    B, N = H.shape
    return np.full((B, N), 1.0 / N)


def tilt(signal, k: float, floor_frac: float):
    def pol(t, H, ctx):
        B, N = H.shape
        z = zscore_rows(signal(ctx, t))
        w = np.maximum(1.0 + k * z, 0.0) / N
        return floor_project(w, floor_frac / N)

    return pol


def cf_rebalance(floor_frac: float, target=None):
    def pol(t, H, ctx):
        B, N = H.shape
        tw = np.full((B, N), 1.0 / N) if target is None else target(t, H, ctx)
        V = H.sum(axis=1, keepdims=True) + 1.0
        deficit = np.maximum(tw * V - H, 0.0)
        ds = deficit.sum(axis=1, keepdims=True)
        fl = floor_frac / N
        rem = 1.0 - N * fl
        d = np.where(ds > 1e-15, deficit / np.where(ds > 1e-15, ds, 1), tw)
        w = fl + rem * d
        return w / w.sum(axis=1, keepdims=True)

    return pol


def winners(floor_frac: float):
    def pol(t, H, ctx):
        B, N = H.shape
        s = H.sum(axis=1, keepdims=True)
        w = np.where(s > 0, H / np.where(s > 0, s, 1), 1.0 / N)
        return floor_project(w, floor_frac / N)

    return pol


def value_target(signal, k: float):
    def tgt(t, H, ctx):
        B, N = H.shape
        z = zscore_rows(signal(ctx, t))
        w = np.maximum(1.0 + k * z, 0.05) / N
        return w / w.sum(axis=1, keepdims=True)

    return tgt
