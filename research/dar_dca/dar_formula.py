"""DAR-DCA — reference implementation of the candidate formula (research; single source for tests).

Monthly purchase split for a no-sell DCA, ticker-agnostic, price-only.

Inputs: month-end TOTAL-RETURN price levels for the user's funds, strictly BEFORE the purchase month
(row -1 = end of the month preceding the purchase). Proxies may extend history before inception.

For each fund with >= HISTORY_MONTHS month-ends:
    L(m)  = log price at month-end m months before the last row (L(0) = last row)
    AMP   = log( mean(P at month-ends 54..66 before) ) - L(0)          # 5-year value (Asness et al. 2013)
    OLD   = L(60) - L(180)                                               # return from 15y ago to 5y ago
    DAR   = AMP + DRIFT_COEF * OLD                                       # cancels a persistent drift mu:
                                                                         # AMP ~ -5mu, OLD ~ 10mu -> 0
Cross-section (funds that have a DAR):  z = (DAR - mean) / max(std, SD_MIN)   (population std)
  SD_MIN keeps near-identical funds (e.g. VOO vs SPY) from being tilted hard on noise: when the funds'
  DARs barely differ, the tilt shrinks with the difference instead of being blown up to +-1 std.
Funds without enough history get z = 0 (neutral, never a guessed signal).
Weights:  w = max(1 + K * z, 0) / N   then project onto {sum = 1, every w >= FLOOR_FRAC / N}
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HISTORY_MONTHS = 181          # need L(180)
AMP_FROM, AMP_TO = 54, 66     # inclusive month-end lags averaged for the 5-year reference level
DRIFT_COEF = 0.5              # 5y window / 10y window — theory, not fitted
K = 1.0                       # tilt strength (exploration knee: >=80% of max gain)
FLOOR_FRAC = 0.2              # every fund gets at least 20% of an equal share every month
SD_MIN = 0.15                 # lower bound on the cross-sectional std used for z (log units)


def floor_project(w: np.ndarray, floor: float) -> np.ndarray:
    w = np.maximum(np.asarray(w, dtype=float), 0.0)
    n = len(w)
    if w.sum() <= 0:
        return np.full(n, 1.0 / n)
    w = w / w.sum()
    fixed = np.zeros(n, dtype=bool)
    out = w
    for _ in range(n):
        budget = 1.0 - floor * fixed.sum()
        free = ~fixed
        fw = np.where(free, w, 0.0)
        s = fw.sum()
        scaled = fw / s * budget if s > 0 else np.where(free, budget / free.sum(), 0.0)
        out = np.where(fixed, floor, scaled)
        low = free & (out < floor - 1e-15)
        if not low.any():
            break
        fixed |= low
    return out


def dar_signal(month_end_prices: pd.DataFrame) -> pd.Series:
    """DAR per column (NaN when history is insufficient). Rows = month-ends, oldest first."""
    out = {}
    for col in month_end_prices.columns:
        s = month_end_prices[col].dropna()
        s = s[s > 0]
        if len(s) < HISTORY_MONTHS:
            out[col] = np.nan
            continue
        lp = np.log(s.values)
        L = lambda m: lp[-1 - m]  # noqa: E731
        ref = np.log(np.mean(s.values[-1 - AMP_TO : len(s) - AMP_FROM]))
        amp = ref - L(0)
        old = L(60) - L(180)
        out[col] = amp + DRIFT_COEF * old
    return pd.Series(out, dtype=float)


def dar_weights(
    month_end_prices: pd.DataFrame, k: float = K, floor_frac: float = FLOOR_FRAC, sd_min: float = SD_MIN
) -> pd.Series:
    cols = list(month_end_prices.columns)
    n = len(cols)
    if n == 0:
        raise ValueError("no funds")
    sig = dar_signal(month_end_prices)
    z = pd.Series(0.0, index=cols)
    ok = sig.notna()
    if ok.sum() >= 2:
        x = sig[ok]
        sd = max(float(x.std(ddof=0)), sd_min)
        if sd > 0:
            z[ok] = (x - x.mean()) / sd
    w = np.maximum(1.0 + k * z.values, 0.0) / n
    return pd.Series(floor_project(w, floor_frac / n), index=cols)
