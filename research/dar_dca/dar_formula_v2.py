"""DAR-DCA v2 = v1 (dar_formula.py, unchanged and still hash-locked) + a per-fund ceiling.

After v1's weights (floor 0.2/N), any fund above CAP_MULT/N is cut to the ceiling and the excess is handed
to the funds still below the ceiling in proportion to their weights (repeat until nothing exceeds it).
Motivation (stated as post-hoc in round 1): without a ceiling the formula poured up to ~84% of a month
into one asset and kept doing so through gold's 1980–2001 bear market.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import dar_formula as F

CAP_MULT = 1.5  # no fund gets more than 1.5x an equal share (30% for N = 5)


def cap_project(w: np.ndarray, cap: float) -> np.ndarray:
    w = np.asarray(w, dtype=float).copy()
    for _ in range(len(w)):
        over = w > cap + 1e-15
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        room = (~over) & (w < cap - 1e-15)
        share = np.where(room, w, 0.0)
        if share.sum() > 0:
            w = w + share / share.sum() * excess
    return w / w.sum()


def dar_weights_v2(month_end_prices: pd.DataFrame, cap_mult: float = CAP_MULT) -> pd.Series:
    w1 = F.dar_weights(month_end_prices)
    n = len(w1)
    if cap_mult * 1.0 / n >= 1.0:
        return w1
    return pd.Series(cap_project(w1.values, cap_mult / n), index=w1.index)
