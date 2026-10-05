# -*- coding: utf-8 -*-
"""สร้าง "แผงข้อมูลสอบเทียบ" จากข้อมูลจริง สำหรับ simulation อนาคต 5–20 ปี.

ทุกอย่างในไฟล์นี้ **วัดจากข้อมูล** ไม่มีตัวเลขที่ตั้งเอง ยกเว้นที่เขียนกำกับว่า "ข้อสมมติ":
  * ผลตอบแทนรายเดือนของ 5 กอง (USD, total return) ปรับด้วยกองพี่เมื่อลิสต์ไม่ถึง (SCHD←DVY เฉพาะที่นี่)
  * ค่าเงิน USDTHB (FRED DEXTHUS ตั้งแต่ 1981) · เงินเฟ้อสหรัฐ (CPIAUCSL) · ดอกเบี้ย (FEDFUNDS, DGS10)
    น้ำมัน (DCOILWTICO) · VIX · ช่วงถดถอย (USREC) · เงินเฟ้อไทยรายปี (World Bank ผ่าน FRED)
  * regime 4 แบบ ติดป้ายด้วยกติกาโปร่งใส 1990–2026 แล้วนับการเปลี่ยนสถานะจริง

ใช้: python panel.py <scratch_dir>   (ต้องมี daily.pkl, macro.pkl, fred/*.csv ใน scratch_dir)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.dar_dca import TICKERS, load_month_end_history, plan_month_of

FUNDS = list(TICKERS)  # VOO SCHD QQQM XLV GLDM
REGIMES = ["calm", "stress", "crisis", "highinfl"]
POOL_START = "2004-12-31"  # GLD ลิสต์ 2004-11 → เดือนแรกที่มีผลตอบแทนครบทั้ง 5 กอง
CUTOFF = pd.Timestamp("2026-10-01")  # เดือนที่ยังไม่ปิด ไม่เอา


def _fred(d: Path, sid: str) -> pd.Series:
    df = pd.read_csv(d / "fred" / f"{sid}.csv")
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    s.index = pd.to_datetime(df.iloc[:, 0])
    return s.dropna()


def _me(s: pd.Series) -> pd.Series:
    s = s.dropna().resample("ME").last().dropna()
    return s[s.index < CUTOFF]


def _splice(daily: pd.DataFrame, own: str, sib: str) -> pd.Series:
    """ต่อประวัติ own ด้วย sib โดยปรับระดับที่วันเชื่อม (กติกาเดียวกับ proxy_history)."""
    a, b = daily[own].dropna(), daily[sib].dropna()
    join = a.index[0]
    scale = float(a.iloc[0]) / float(b.loc[:join].iloc[-1])
    return pd.concat([b[b.index < join] * scale, a])


def build(scratch: Path) -> dict:
    daily = pd.read_pickle(scratch / "daily.pkl")
    macro = pd.read_pickle(scratch / "macro.pkl")

    # ---- 1) ราคาจริงตามที่ "สูตรสด" เห็น (ใช้ load_month_end_history ของโปรเจกต์เอง ไม่ประกอบใหม่) ----
    plan_month = plan_month_of(pd.Timestamp("2026-10-15"))
    live_me, used, _ = load_month_end_history(
        FUNDS, plan_month, fetch=lambda tickers, years: daily[list(tickers)]
    )

    # ---- 2) แผงรายเดือนสำหรับสอบเทียบ (ผลตอบแทนของกอง ยืดด้วยกองพี่ รวม SCHD←DVY) ----
    spliced = {
        "VOO": _splice(daily, "VOO", "SPY"),
        "SCHD": _splice(daily, "SCHD", "DVY"),
        "QQQM": _splice(daily, "QQQM", "QQQ"),
        "XLV": daily["XLV"].dropna(),
        "GLDM": _splice(daily, "GLDM", "GLD"),
    }
    lev = pd.DataFrame({k: _me(v) for k, v in spliced.items()})
    lr = np.log(lev).diff()

    fx_d = _fred(scratch, "DEXTHUS")
    fx_me = _me(fx_d)
    # ใช้ yfinance THB=X (ปิดล่าสุดของ Sep 30) เป็นจุดเริ่มต้นของ simulation ให้ตรงกับที่ใช้แปลงบาทจริง
    fx_yf = _me(macro["usdthb"])
    fx0 = float(fx_yf.iloc[-1])

    cpi = _fred(scratch, "CPIAUCSL")
    cpi.index = cpi.index + pd.offsets.MonthEnd(0)
    ffr = _fred(scratch, "FEDFUNDS")
    ffr.index = ffr.index + pd.offsets.MonthEnd(0)
    y10 = _me(_fred(scratch, "DGS10"))
    wti = _me(_fred(scratch, "DCOILWTICO"))
    vix = _fred(scratch, "VIXCLS").resample("ME").mean()
    rec = _fred(scratch, "USREC")
    rec.index = rec.index + pd.offsets.MonthEnd(0)

    infl_m = np.log(cpi).diff()                      # เงินเฟ้อสหรัฐ m/m (log)
    infl_yoy = np.exp(np.log(cpi).diff(12)) - 1.0

    # ---- 3) ป้าย regime 1990–2026 (กติกาตายตัว อ่านง่าย — ไม่ได้ฟิต) ----
    idx = pd.date_range("1990-01-31", "2026-09-30", freq="ME")
    lab = pd.Series("calm", index=idx, dtype=object)
    v = vix.reindex(idx)
    r = rec.reindex(idx).fillna(0)
    yoy = infl_yoy.reindex(idx)
    crisis = (r == 1) | (v >= 30)
    highinfl = (~crisis) & (yoy >= 0.04)
    stress = (~crisis) & (~highinfl) & (v >= 20)
    lab[stress] = "stress"
    lab[highinfl] = "highinfl"
    lab[crisis] = "crisis"
    lab = lab.where(v.notna(), other="calm")
    code = lab.map({n: i for i, n in enumerate(REGIMES)}).to_numpy()
    K = len(REGIMES)
    trans = np.ones((K, K)) * 0.5  # ปรับเรียบเล็กน้อย กันแถวเป็นศูนย์
    for a, b in zip(code[:-1], code[1:]):
        trans[a, b] += 1.0
    trans = trans / trans.sum(axis=1, keepdims=True)
    start_regime = int(code[-1])

    # ---- 4) กองรวบเป็นเวกเตอร์เดียวต่อเดือน: 5 กอง + dlogFX + infl + dFFR + d10y + dlogOil ----
    cols = FUNDS + ["fx", "us_infl", "d_ffr", "d_10y", "oil"]
    panel = pd.DataFrame(
        {
            **{f: lr[f] for f in FUNDS},
            "fx": np.log(fx_me).diff(),
            "us_infl": infl_m,
            "d_ffr": ffr.diff(),
            "d_10y": y10.diff(),
            "oil": np.log(wti.where(wti > 0)).diff(),
        }
    )[cols]
    panel = panel.loc[POOL_START:].dropna()
    pool_regime = lab.reindex(panel.index).map({n: i for i, n in enumerate(REGIMES)}).to_numpy()

    pools = []
    for k in range(K):
        pools.append(panel.to_numpy()[pool_regime == k])

    # ---- 4b) ความแปรปรวนร่วมรายวัน "ที่เกิดขึ้นจริง" ของแต่ละเดือน (ผลตอบแทนเป็นบาท) — ไว้ให้ ERC ใน sim ประมาณ
    #      ด้วยสูตรเดียวกับของจริง (vol 252 วัน × corr 5 ปี) แทนการประมาณจาก 12 จุดรายเดือนที่สั่นเกินจริง
    daily_px = pd.DataFrame(spliced).dropna()
    fx_daily = macro["usdthb"].reindex(daily_px.index).ffill(limit=3)
    thb_d = np.log(daily_px.mul(fx_daily, axis=0)).diff().dropna()
    iu = np.triu_indices(len(FUNDS))
    S_rows, S_days, S_idx = [], [], []
    for me_ts, g in thb_d.groupby(pd.Grouper(freq="ME")):
        if me_ts >= CUTOFF or len(g) == 0:
            continue
        a = g.to_numpy()
        S_rows.append((a.T @ a)[iu])
        S_days.append(len(g))
        S_idx.append(me_ts)
    S_all = pd.DataFrame(S_rows, index=S_idx)
    S_days = pd.Series(S_days, index=S_idx)
    pool_S, pool_days = [], []
    for k in range(K):
        sel = panel.index[pool_regime == k]
        pool_S.append(S_all.reindex(sel).to_numpy())
        pool_days.append(S_days.reindex(sel).to_numpy().astype(float))
    live_idx = pd.date_range(end="2026-09-30", periods=60, freq="ME")
    live_S = S_all.reindex(live_idx).to_numpy()
    live_days = S_days.reindex(live_idx).to_numpy().astype(float)
    assert not np.isnan(live_S).any(), "ไม่มีผลตอบแทนรายวันครบ 60 เดือนล่าสุด"
    assert all(not np.isnan(p).any() for p in pool_S), "pool มีเดือนที่ไม่มีผลตอบแทนรายวัน"

    # ---- 5) เงินเฟ้อไทย ≈ a + b × เงินเฟ้อสหรัฐ (รายปี, 1981–2025) — ความมั่นใจต่ำ รายงานตามจริง ----
    th = _fred(scratch, "FPCPITOTLZGTHA") / 100.0
    th.index = th.index.year
    us_ann = (cpi.groupby(cpi.index.year).mean().pct_change()).dropna()
    both = pd.concat([th.rename("th"), us_ann.rename("us")], axis=1).dropna().loc[1981:2025]
    b, a = np.polyfit(both["us"], both["th"], 1)
    resid = both["th"] - (a + b * both["us"])
    th_fit = {
        "a": float(a), "b": float(b),
        "resid_sd_annual": float(resid.std(ddof=2)),
        "r2": float(1 - resid.var() / both["th"].var()),
        "n": int(len(both)),
        "th_mean": float(both["th"].mean()), "us_mean": float(both["us"].mean()),
    }

    # ---- 6) เหตุการณ์ที่วัดได้จากข้อมูล (ไม่ต้องสมมติขนาด) ----
    ev = {}
    # (ก) ฟองสบู่หุ้นเทค 2000-03-24 → 2002-10-09: ผลตอบแทนรวมจริงของ SPY / QQQ / XLV
    def tr(sym: str, a_: str, b_: str) -> float:
        s = daily[sym].dropna()
        return float(math.log(s.loc[:b_].iloc[-1] / s.loc[:a_].iloc[-1]))
    ev["dotcom"] = {
        "VOO": tr("SPY", "2000-03-24", "2002-10-09"),
        "QQQM": tr("QQQ", "2000-03-24", "2002-10-09"),
        "XLV": tr("XLV", "2000-03-24", "2002-10-09"),
        "months": 31,
    }
    # (ข) ค่าเงินบาท: การเคลื่อนไหวสะสม 6 เดือน / 24 เดือน ที่ใหญ่ที่สุดของ DEXTHUS 1981–2026
    lfx = np.log(fx_me.loc["1981":])
    ev["fx_up_6m"] = float((lfx.diff(6)).max())     # บาทอ่อนเร็วสุดใน 6 เดือน (ปี 1997)
    ev["fx_down_24m"] = float((lfx.diff(24)).min())  # บาทแข็งแรงสุดใน 24 เดือน
    ev["fx_up_6m_end"] = str(lfx.diff(6).idxmax().date())
    ev["fx_down_24m_end"] = str(lfx.diff(24).idxmin().date())

    # ---- 7) ประวัติจริงสำหรับให้สูตรเห็นตอนเดือนแรก ----
    me_live = live_me.copy()
    first_valid = {c: int(me_live[c].notna().to_numpy().argmax()) for c in me_live.columns}

    # ผลตอบแทนบาทรายเดือนล่าสุด 60 เดือน (ไว้ให้ ERC มี correlation ตั้งแต่เดือนแรก) — ใช้ราคาจริงของแผน
    lr_live = np.log(me_live).diff()
    fx_l = np.log(fx_yf).diff().reindex(lr_live.index)
    thb_hist = (lr_live.add(fx_l, axis=0)).dropna().tail(60)

    stats = {}
    pr = panel.loc[:, FUNDS]
    stats["pool_months"] = int(len(panel))
    stats["pool_range"] = [str(panel.index[0].date()), str(panel.index[-1].date())]
    stats["fund_cagr_pct"] = {f: round(float(np.expm1(pr[f].mean() * 12) * 100), 2) for f in FUNDS}
    stats["fund_vol_pct"] = {f: round(float(pr[f].std() * math.sqrt(12) * 100), 2) for f in FUNDS}
    stats["corr"] = pr.corr().round(2).to_dict()
    stats["regime_counts_1990_2026"] = {n: int((code == i).sum()) for i, n in enumerate(REGIMES)}
    stats["regime_pool_sizes"] = {n: int(len(pools[i])) for i, n in enumerate(REGIMES)}
    stats["transition"] = {REGIMES[i]: {REGIMES[j]: round(float(trans[i, j]), 3) for j in range(K)} for i in range(K)}
    stats["mean_duration_months"] = {REGIMES[i]: round(float(1 / (1 - trans[i, i])), 1) for i in range(K)}
    stats["start_regime"] = REGIMES[start_regime]
    stats["fx_start"] = fx0
    stats["fx_hist_range"] = [str(fx_me.index[0].date()), str(fx_me.index[-1].date())]
    stats["fx_vol_annual_pct_since_2004"] = round(float(panel["fx"].std() * math.sqrt(12) * 100), 2)
    stats["us_infl_yoy_latest_pct"] = round(float(infl_yoy.iloc[-1] * 100), 2)
    stats["us_infl_latest_month"] = str(infl_yoy.index[-1].date())
    stats["ffr_latest"] = float(ffr.iloc[-1])
    stats["y10_latest"] = float(y10.iloc[-1])
    stats["wti_latest"] = float(wti.iloc[-1])
    stats["vix_latest"] = float(vix.iloc[-1])
    stats["proxies_live"] = used

    return {
        "funds": FUNDS, "cols": cols, "regimes": REGIMES, "pools": pools,
        "trans": trans, "start_regime": start_regime,
        "live_me_logs": np.log(me_live).to_numpy(), "live_me_index": [str(i.date()) for i in me_live.index],
        "first_valid": first_valid,
        "thb_hist": thb_hist.to_numpy(), "fx0": fx0,
        "chron": {"X": panel.to_numpy(), "S": S_all.reindex(panel.index).to_numpy(),
                  "days": S_days.reindex(panel.index).to_numpy().astype(float), "reg": pool_regime},
        "pool_S": pool_S, "pool_days": pool_days, "live_S": live_S, "live_days": live_days,
        "S_all": S_all, "S_days": S_days,
        "th_fit": th_fit, "events_measured": ev,
        "start_levels": {"ffr": float(ffr.iloc[-1]), "y10": float(y10.iloc[-1]), "oil": float(wti.iloc[-1])},
        "us_infl_mean_m": float(panel["us_infl"].mean()),
        "stats": stats,
        "live_me_df": me_live,
    }


if __name__ == "__main__":
    scratch = Path(sys.argv[1])
    out = build(scratch)
    pd.to_pickle(out, scratch / "panel.pkl")
    import json
    print(json.dumps(out["stats"], indent=1, ensure_ascii=False))
    print("th_fit", out["th_fit"])
    print("events", out["events_measured"])
    print("live months per fund", {c: int(out['live_me_df'][c].notna().sum()) for c in out['live_me_df'].columns})
