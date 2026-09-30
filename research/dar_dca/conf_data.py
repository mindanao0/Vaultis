"""Confirmation data: FF pools over the full period + a gold series (WB monthly avg to 2004-11, GLD after).

Writes /scratch/cache/conf_pools.pkl = {name: DataFrame of monthly simple returns (PeriodIndex)}.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def gold_returns() -> pd.Series:
    wb = pd.read_pickle("/scratch/cache/wb_gold.pkl")  # monthly average USD/oz, 1960-01..2024-12
    etf = pd.read_pickle("/scratch/cache/etf_daily.pkl")["adj"]["GLD"].dropna()
    gld_me = etf.resample("ME").last()
    gld_me.index = gld_me.index.to_period("M")
    r_wb = wb.pct_change().loc[:"2004-11"]
    r_gld = gld_me.pct_change().loc["2004-12":]
    r = pd.concat([r_wb, r_gld]).rename("GOLD")
    return r.dropna()


def main() -> None:
    ind = pd.read_pickle("/scratch/cache/ff_ind.pkl")
    style = pd.read_pickle("/scratch/cache/ff_style.pkl")
    g = gold_returns()
    end = pd.Period("2026-08", "M")
    pools = {
        "ind17": ind[17].loc[:end],
        "ind49": ind[49].loc[:end],
        "style": style.loc[:end],
        "mixed": pd.concat([ind[17].add_prefix("I "), style], axis=1).loc[:end],
    }
    mixed_gold = pools["mixed"].copy()
    mixed_gold["GOLD"] = g.reindex(mixed_gold.index)
    pools["mixed_gold"] = mixed_gold
    pd.to_pickle(pools, "/scratch/cache/conf_pools.pkl")
    for k, v in pools.items():
        print(k, v.shape, v.index.min(), v.index.max())
    print("gold first/last:", g.index.min(), g.index.max(), "n", len(g))


if __name__ == "__main__":
    main()
