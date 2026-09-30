"""What the locked DAR formula would buy for the user's funds now (descriptive, not an outcome test).

Uses month-end total-return levels; the September 2026 'month-end' is the last available close (2026-09-29).
"""
import json

import numpy as np
import pandas as pd

import dar_formula as F
from conf_etf import month_end_history

etf = pd.read_pickle("/scratch/cache/etf_daily.pkl")
adj = etf["adj"]
tickers = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
me = month_end_history(adj, ["VOO", "SCHD", "QQQM", "XLV", "GLDM"])
rows = {}
for label, cutoff in [("plan_2026-10 (Sep month-end = 2026-09-29 close)", "2026-10-01"), ("plan_2026-09 (Aug month-end)", "2026-09-01")]:
    h = me.loc[me.index < pd.Timestamp(cutoff), tickers]
    sig = F.dar_signal(h)
    w = F.dar_weights(h)
    comp = {}
    for t in tickers:
        s = h[t].dropna()
        n = len(s)
        if n >= F.HISTORY_MONTHS:
            lp = np.log(s.values)
            amp = np.log(np.mean(s.values[-1 - F.AMP_TO : n - F.AMP_FROM])) - lp[-1]
            old = lp[-61] - lp[-181]
            comp[t] = {"months": n, "ret_5y_pct": float((np.exp(-amp) - 1) * 100), "ret_prior10y_pct": float((np.exp(old) - 1) * 100),
                       "AMP": float(amp), "OLD": float(old), "DAR": float(amp + F.DRIFT_COEF * old), "weight": float(w[t])}
        else:
            comp[t] = {"months": n, "neutral": True, "weight": float(w[t])}
    rows[label] = comp
    print(label)
    for t, c in comp.items():
        print(" ", t, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in c.items()})
    alloc = {t: round(float(w[t]) * 5000 / 100) * 100 for t in tickers}
    print("  5,000 THB split (100-THB rounding, before fixing the sum):", alloc, "sum", sum(alloc.values()))
with open("/scratch/results/current_plan.json", "w") as fh:
    json.dump(rows, fh)
