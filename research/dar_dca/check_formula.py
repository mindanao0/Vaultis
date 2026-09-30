"""Sanity checks of dar_formula on synthetic series with known answers."""
import numpy as np
import pandas as pd

import dar_formula as F

idx = pd.period_range("2000-01", periods=200, freq="M").to_timestamp("M")
g = 0.008
t = np.arange(200)
A = np.exp(g * t)                                    # constant drift
B = np.exp(g * t)                                    # same drift, then -0.2 log shock spread over last 60 months
B[-60:] *= np.exp(-0.2 * (np.arange(1, 61) / 60))
C = np.exp(0.004 * t)                                # slower persistent drift (a 'persistent premium' loser)
df = pd.DataFrame({"A": A, "B": B, "C": C}, index=idx)
s = F.dar_signal(df)
print("DAR:", s.round(4).to_dict())
assert abs(s["A"]) < 0.01, "constant drift must give ~0 (drift cancels)"
assert abs(s["C"]) < 0.01, "a different persistent drift must also give ~0"
assert 0.15 < s["B"] < 0.25, "a transient -0.2 shock must give ~+0.2"
w = F.dar_weights(df)
print("weights:", w.round(4).to_dict(), "sum", round(w.sum(), 12))
assert abs(w.sum() - 1) < 1e-12 and (w >= F.FLOOR_FRAC / 3 - 1e-12).all()
assert w["B"] > w["A"] and w["B"] > w["C"]
# neutral when history is short
short = df.iloc[-150:]
ws = F.dar_weights(short)
print("short history ->", ws.round(4).to_dict())
assert np.allclose(ws.values, 1 / 3)
# one fund short -> neutral z=0 for it; others tilt among themselves
mix = df.copy()
mix.loc[mix.index[:60], "C"] = np.nan
wm = F.dar_weights(mix)
print("C short ->", wm.round(4).to_dict(), F.dar_signal(mix).round(4).to_dict())
assert np.isnan(F.dar_signal(mix)["C"])
print("OK")
# near-identical funds: tiny DAR differences must NOT be blown up to full tilts
twin = pd.DataFrame({"X": A, "Y": A * np.exp(0.002 * np.sin(np.arange(200) / 7.0))}, index=idx)
wt = F.dar_weights(twin)
print("near-identical ->", wt.round(4).to_dict(), F.dar_signal(twin).round(5).to_dict())
assert abs(wt["X"] - 0.5) < 0.02, "twins must stay near 50/50"
print("OK twins")
