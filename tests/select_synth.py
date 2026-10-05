# -*- coding: utf-8 -*-
"""ข้อมูลสังเคราะห์ของโหมด SELECT-DCA — ตลาด 13 ตัวตามจักรวาลจริง ปันผลที่ "รู้อันดับ" ล่วงหน้า (ออฟไลน์)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from analysis import select_dca as sd

#: yield ต่อปีที่กำหนดให้แต่ละตลาด (สูง → ต่ำ ตามลำดับในลิสต์นี้) เพื่อให้รู้ผลการจัดอันดับล่วงหน้า
YIELD_ORDER = ["FLGB", "FLAU", "FLGR", "FLSW", "FLCA", "FLJP", "VOO", "FLMX", "FLBR", "FLTW", "FLKR", "FLCH", "FLIN"]
YIELDS = {t: 0.06 - 0.004 * i for i, t in enumerate(YIELD_ORDER)}   # FLGB 6.0% … FLIN 1.2%


def synthetic_market_data(seed: int = 0, end: str = "2026-10-02", short: tuple[str, ...] = ("FLIN",)) -> sd.MarketData:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("1996-03-18", end)
    adj, raw, divs = {}, {}, {}
    for m in sd.UNIVERSE:
        start_own = pd.Timestamp("2017-11-06") if m.ticker != "VOO" else pd.Timestamp("2010-09-09")
        if m.ticker in short:
            start_own = pd.Timestamp("2018-02-08")
        r = rng.normal(0.0003, 0.01, len(days))
        px = pd.Series(50.0 * np.exp(np.cumsum(r)), index=days)
        adj[m.ticker] = px[px.index >= start_own]
        raw[m.ticker] = pd.Series(50.0, index=adj[m.ticker].index)   # ราคาจริงคงที่ → yield ที่วัดได้ = yield ที่กำหนดเป๊ะ (อันดับไม่แกว่งตามสุ่ม)
        qd = pd.date_range(start_own, end, freq="3MS") + pd.Timedelta(days=14)
        div = [50.0 * YIELDS[m.ticker] / 4.0 for d in qd if d >= raw[m.ticker].index[0]]
        divs[m.ticker] = pd.Series(div, index=[d for d in qd if d >= raw[m.ticker].index[0]])
        if m.proxy:
            p0 = {"INDA": pd.Timestamp("2012-02-03"), "MCHI": pd.Timestamp("2011-03-31")}.get(m.proxy, pd.Timestamp("1996-03-18"))
            p = pd.Series(40.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(days)))), index=days)
            adj[m.proxy] = p[p.index >= p0]
    return sd.MarketData(adj=adj, raw_close=raw, dividends=divs)
