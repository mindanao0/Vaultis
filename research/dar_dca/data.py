"""Long-history data loaders for the DCA formula research (scratch, not project code).

Sources (downloaded to /scratch/data):
- Ken French Data Library: 10/17/49 industry portfolios (value-weighted, monthly, 1926-07+),
  Fama/French factors (Mkt-RF, RF).
- World Bank Pink Sheet (CMO-Historical-Data-Monthly.xlsx): gold USD/troy oz, monthly, 1960+.

All returns are simple monthly returns as decimals. Missing values (-99.99 / -999) -> NaN.
"""
from __future__ import annotations

import io
import os
import re
import zipfile

import numpy as np
import pandas as pd

DATA = os.environ.get("RESEARCH_DATA", "/scratch/data")


def _read_ff_zip(name: str) -> str:
    with zipfile.ZipFile(os.path.join(DATA, name)) as zf:
        inner = zf.namelist()[0]
        return zf.read(inner).decode("latin-1")


def _ff_monthly_block(text: str, block_title_regex: str) -> pd.DataFrame:
    """Return the first monthly (YYYYMM) table following a title line matching the regex."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.search(block_title_regex, line, flags=re.I):
            start = i
            break
    if start is None:
        raise ValueError(f"block not found: {block_title_regex}")
    # header line = first line after start that starts with a comma
    j = start + 1
    while j < len(lines) and not lines[j].lstrip().startswith(","):
        j += 1
    header = [h.strip() for h in lines[j].split(",")]
    rows = []
    k = j + 1
    while k < len(lines):
        parts = [p.strip() for p in lines[k].split(",")]
        if not parts or not re.fullmatch(r"\d{6}", parts[0] or ""):
            break
        rows.append(parts)
        k += 1
    df = pd.DataFrame(rows, columns=["date"] + header[1:])
    df["date"] = pd.PeriodIndex(df["date"].str.slice(0, 4) + "-" + df["date"].str.slice(4, 6), freq="M")
    df = df.set_index("date").apply(pd.to_numeric, errors="coerce")
    df = df.mask(df <= -99.99)
    return df / 100.0


def load_ff_industries(n: int = 49, weighting: str = "value") -> pd.DataFrame:
    text = _read_ff_zip(f"{n}_Industry_Portfolios_CSV.zip")
    title = "Average Value Weighted Returns -- Monthly" if weighting == "value" else "Average Equal Weighted Returns -- Monthly"
    return _ff_monthly_block(text, re.escape(title))


def load_ff_factors() -> pd.DataFrame:
    text = _read_ff_zip("F-F_Research_Data_Factors_CSV.zip")
    lines = text.splitlines()
    # first table is monthly; header line starts with ",Mkt-RF"
    j = next(i for i, l in enumerate(lines) if l.startswith(",Mkt-RF"))
    header = [h.strip() for h in lines[j].split(",")]
    rows = []
    k = j + 1
    while k < len(lines):
        parts = [p.strip() for p in lines[k].split(",")]
        if not re.fullmatch(r"\d{6}", parts[0] or ""):
            break
        rows.append(parts)
        k += 1
    df = pd.DataFrame(rows, columns=["date"] + header[1:])
    df["date"] = pd.PeriodIndex(df["date"].str.slice(0, 4) + "-" + df["date"].str.slice(4, 6), freq="M")
    return df.set_index("date").apply(pd.to_numeric, errors="coerce") / 100.0


def load_wb_gold_price() -> pd.Series:
    """Monthly average gold price (USD/troy oz). World Bank reports monthly averages."""
    import sys

    sys.path.insert(0, "/scratch/pylib")
    raw = pd.read_excel(os.path.join(DATA, "CMO-Historical-Data-Monthly.xlsx"), sheet_name="Monthly Prices", header=None)
    # find header row containing 'Gold'
    hdr_row = None
    for i in range(0, 12):
        vals = [str(v) for v in raw.iloc[i].tolist()]
        if any(v.strip().lower() == "gold" for v in vals):
            hdr_row = i
            break
    if hdr_row is None:
        raise ValueError("gold header not found")
    cols = [str(v).strip() for v in raw.iloc[hdr_row].tolist()]
    gcol = cols.index("Gold")
    data = raw.iloc[hdr_row + 1 :, [0, gcol]].copy()
    data.columns = ["date", "gold"]
    data = data[data["date"].astype(str).str.fullmatch(r"\d{4}M\d{2}")]
    idx = pd.PeriodIndex(data["date"].str.replace("M", "-"), freq="M")
    s = pd.Series(pd.to_numeric(data["gold"], errors="coerce").values, index=idx, name="gold")
    return s.dropna()


if __name__ == "__main__":
    for n in (10, 17, 49):
        df = load_ff_industries(n)
        print(n, df.shape, df.index.min(), df.index.max(), "NaN share", float(df.isna().mean().mean()))
    f = load_ff_factors()
    print("factors", f.shape, f.index.min(), f.index.max(), list(f.columns))
    g = load_wb_gold_price()
    print("gold", g.shape, g.index.min(), g.index.max(), g.loc["1968-01":"1974-12"].iloc[::12].round(1).to_dict())
    os.makedirs("/scratch/cache", exist_ok=True)
    pd.to_pickle({n: load_ff_industries(n) for n in (10, 17, 49)}, "/scratch/cache/ff_ind.pkl")
    pd.to_pickle(f, "/scratch/cache/ff_factors.pkl")
    pd.to_pickle(g, "/scratch/cache/wb_gold.pkl")
    print("cached")


def load_ff_generic(zipname: str, title_regex: str) -> pd.DataFrame:
    return _ff_monthly_block(_read_ff_zip(zipname), title_regex)


def build_style_cache() -> None:
    dp = load_ff_generic("Portfolios_Formed_on_D-P_CSV.zip", r"^\s*Value Weight Returns -- Monthly")
    dp = dp[["<= 0", "Lo 20", "Qnt 2", "Qnt 3", "Qnt 4", "Hi 20"]].add_prefix("DP ")
    bm = load_ff_generic("Portfolios_Formed_on_BE-ME_CSV.zip", r"^\s*Value Weight Returns -- Monthly")
    bm = bm[["Lo 20", "Qnt 2", "Qnt 3", "Qnt 4", "Hi 20"]].add_prefix("BM ")
    six = load_ff_generic("6_Portfolios_2x3_CSV.zip", r"Average Value Weighted Returns -- Monthly").add_prefix("SZBM ")
    fac = load_ff_factors()
    mkt = (fac["Mkt-RF"] + fac["RF"]).rename("MKT")
    style = pd.concat([mkt, dp, bm, six], axis=1)
    pd.to_pickle(style, "/scratch/cache/ff_style.pkl")
    print("style", style.shape, style.index.min(), style.index.max(), list(style.columns))
    print(style.loc["1926-07":"1974-12"].isna().sum().to_dict())
