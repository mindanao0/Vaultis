"""Round-2 confirmation data (never examined): international country markets + World Bank commodities.

- Ken French F-F_International_Countries.zip: first block of each .Dat = "Value-Weight Dollar Returns,
  All 4 Data Items Not Reqd", column 'Mkt' = the country's value-weighted market return in USD (with dividends).
- World Bank Pink Sheet monthly nominal USD prices; commodities pre-declared below (gold excluded: already spent).
Writes /scratch/cache/conf2_pools.pkl = {"intl": DataFrame, "cmdty": DataFrame} of monthly simple returns.
"""
from __future__ import annotations

import io
import re
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, "/scratch/pylib")

COMMODITIES = ["Crude oil, average", "Silver", "Platinum", "Copper", "Aluminum", "Nickel", "Zinc", "Lead", "Tin"]


def country_mkt(zf: zipfile.ZipFile, name: str) -> pd.Series:
    lines = zf.read(name).decode("latin-1").splitlines()
    # first header row that contains 'Mkt', rows until the next non-numeric line
    h = next(i for i, l in enumerate(lines) if l.strip().startswith("Mkt"))
    rows = []
    for l in lines[h + 1 :]:
        parts = l.split()
        if not parts or not re.fullmatch(r"\d{6}", parts[0]):
            break
        rows.append((parts[0], float(parts[1])))
    idx = pd.PeriodIndex([f"{d[:4]}-{d[4:]}" for d, _ in rows], freq="M")
    s = pd.Series([v for _, v in rows], index=idx)
    return (s.mask(s <= -99.99) / 100.0).rename(name.replace(".Dat", ""))


def main() -> None:
    with zipfile.ZipFile("/scratch/data/F-F_International_Countries.zip") as zf:
        intl = pd.concat([country_mkt(zf, n) for n in zf.namelist()], axis=1)
    raw = pd.read_excel("/scratch/data/CMO-Historical-Data-Monthly.xlsx", sheet_name="Monthly Prices", header=None)
    hdr = next(i for i in range(12) if any(str(v).strip() == "Gold" for v in raw.iloc[i].tolist()))
    cols = [str(v).strip() for v in raw.iloc[hdr].tolist()]
    data = raw.iloc[hdr + 1 :].copy()
    data = data[data.iloc[:, 0].astype(str).str.fullmatch(r"\d{4}M\d{2}")]
    idx = pd.PeriodIndex(data.iloc[:, 0].astype(str).str.replace("M", "-"), freq="M")
    cm = {}
    for c in COMMODITIES:
        j = cols.index(c)
        p = pd.Series(pd.to_numeric(data.iloc[:, j], errors="coerce").values, index=idx)
        cm[c] = p.pct_change()
    cmdty = pd.DataFrame(cm)
    pd.to_pickle({"intl": intl, "cmdty": cmdty}, "/scratch/cache/conf2_pools.pkl")
    print("intl", intl.shape, intl.index.min(), intl.index.max())
    print(intl.apply(lambda s: f"{s.first_valid_index()}..{s.last_valid_index()} n={s.notna().sum()}").to_string())
    print("cmdty", cmdty.shape, cmdty.index.min(), cmdty.index.max())
    print(cmdty.apply(lambda s: f"{s.first_valid_index()}..{s.last_valid_index()} n={s.notna().sum()}").to_string())


if __name__ == "__main__":
    main()
