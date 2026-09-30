"""Exploration #1 — which price signals predict *horizon-integrated* relative returns?

Data: Ken French value-weighted industries, 1926-07..1974-12 ONLY (see SPLIT.md).
For each month t and industry i: signal s_{i,t} (cross-sectionally demeaned), target =
relative cumulative log return over (t, t+h], h in HORIZONS. Fama-MacBeth slope per month,
averaged; t-stat with Newey-West (lags = h) on the monthly slope series.
Slope units: 'relative log return over the horizon per 1.0 of (demeaned) signal'.
For a DCA purchase held to the horizon, the long-h slope is what counts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

END_EXPLORE = pd.Period("1974-12", "M")
HORIZONS = [1, 3, 6, 12, 24, 36, 60, 84, 120]


def nw_tstat(x: np.ndarray, lags: int) -> float:
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 10:
        return float("nan")
    m = x.mean()
    e = x - m
    gamma0 = (e @ e) / n
    s = gamma0
    for l in range(1, min(lags, n - 1) + 1):
        w = 1 - l / (lags + 1)
        s += 2 * w * (e[l:] @ e[:-l]) / n
    se = np.sqrt(s / n)
    return float(m / se) if se > 0 else float("nan")


def signals(logp: pd.DataFrame, r: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    out["rev_1m"] = -r  # last month's return, sign flipped (reversal)
    out["mom_12_1"] = logp.shift(1) - logp.shift(12)
    out["mom_6_1"] = logp.shift(1) - logp.shift(6)
    out["val_3y"] = -(logp - logp.shift(36))
    # AMP (2013) value: log(avg price 4.5–5.5y ago) − log(price now)
    avg_old = np.log(np.exp(logp).shift(54).rolling(13).mean())
    out["val_5y_amp"] = avg_old - logp
    out["val_10y"] = -(logp - logp.shift(120))
    # deviation from own 10y log-linear trend (fit on t-119..t), residual at t, sign flipped
    def trend_dev(col: pd.Series, win: int = 120) -> pd.Series:
        y = col.values
        res = np.full(len(y), np.nan)
        x = np.arange(win, dtype=float)
        xm = x.mean()
        sxx = ((x - xm) ** 2).sum()
        for t in range(win - 1, len(y)):
            seg = y[t - win + 1 : t + 1]
            if np.isnan(seg).any():
                continue
            b = ((x - xm) * (seg - seg.mean())).sum() / sxx
            a = seg.mean() - b * xm
            res[t] = -(seg[-1] - (a + b * x[-1]))
        return pd.Series(res, index=col.index)

    out["trend_dev_10y"] = logp.apply(trend_dev)
    out["low_vol_36m"] = -r.rolling(36).std()
    out["dd_36m"] = logp - logp.rolling(36).max()  # 0 at peak, negative in drawdown
    return out


def fm_slopes(sig: pd.DataFrame, fwd: pd.DataFrame) -> np.ndarray:
    slopes = []
    for t in sig.index:
        s = sig.loc[t]
        y = fwd.loc[t]
        ok = s.notna() & y.notna()
        if ok.sum() < 8:
            slopes.append(np.nan)
            continue
        sv = s[ok] - s[ok].mean()
        yv = y[ok] - y[ok].mean()
        den = (sv * sv).sum()
        slopes.append(float((sv * yv).sum() / den) if den > 0 else np.nan)
    return np.array(slopes)


def rank_ic(sig: pd.DataFrame, fwd: pd.DataFrame) -> np.ndarray:
    out = []
    for t in sig.index:
        s = sig.loc[t]
        y = fwd.loc[t]
        ok = s.notna() & y.notna()
        if ok.sum() < 8:
            out.append(np.nan)
            continue
        out.append(float(s[ok].rank().corr(y[ok].rank())))
    return np.array(out)


def main() -> None:
    ind = pd.read_pickle("/scratch/cache/ff_ind.pkl")
    for n in (17, 49):
        r_full = ind[n]
        r = r_full.loc[:END_EXPLORE]
        lr = np.log1p(r)
        logp = lr.cumsum()
        logp = logp.where(r.notna())
        sigs = signals(logp, r)
        print(f"\n=== {n} industries, exploration 1926-07..1974-12 ===")
        header = "signal".ljust(15) + "".join(f"h={h:<4}".rjust(16) for h in HORIZONS)
        print(header)
        for name, sig in sigs.items():
            cells = []
            for h in HORIZONS:
                # relative cumulative log return over (t, t+h], only if fully inside exploration window
                fwd = lr.rolling(h).sum().shift(-h)
                fwd = fwd.sub(fwd.mean(axis=1), axis=0)
                valid_idx = [t for t in fwd.index if t + h <= END_EXPLORE]
                f = fwd.loc[valid_idx]
                s = sig.loc[valid_idx]
                sl = fm_slopes(s, f)
                ic = rank_ic(s, f)
                cells.append(f"{np.nanmean(sl):+.3f}({nw_tstat(sl, h):+.1f})|{np.nanmean(ic):+.2f}")
            print(name.ljust(15) + "".join(c.rjust(16) for c in cells))
        print("cell = FM slope (NW t) | mean rank IC")


if __name__ == "__main__":
    main()
