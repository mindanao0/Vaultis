# -*- coding: utf-8 -*-
"""ขั้น 3 ของ research/dar_select/PLAN.md — ออกแบบกติกาเลือกกองบน **ข้อมูลสำรวจเท่านั้น** (ตลาดรายประเทศ Ken French 1975–2025).

ข้อมูลยืนยัน (JST 1870–1974) ยังไม่ถูกเปิดดูค่าใด ๆ ในสคริปต์นี้ — ไม่มีการอ่านไฟล์ JST เลย

ตัดสินใจ 2 เรื่องด้วยกติกาที่ประกาศไว้ **ก่อนรัน** (ในไฟล์นี้) :
 (1) วิธีแปลงสูตร DAR จากรายเดือนเป็นรายปี (ข้อมูล JST เป็นรายปี) — เลือกวิธีที่ z รายปีสอดคล้องกับ z รายเดือนของสูตรจริงมากกว่า
     (Spearman เฉลี่ยข้ามประเทศ ณ สิ้นปีทุกปี) เสมอกัน (ต่าง < 0.01) → เลือกวิธีที่เรียบง่ายกว่า (M1)
 (2) เพดานกันกระจุก X (กองที่มูลค่าเกิน X ของพอร์ต ห้ามซื้อเพิ่ม) จาก {25%, 30%, 40%, 50%, ไม่มีเพดาน} —
     เลือก X ที่ทำให้ "เปอร์เซนไทล์ที่ 10 ของเงินปลายทางเทียบแบ่งเท่ากัน" สูงสุด (กันหางเสียหาย — บทเรียนรอบ 1)
     เสมอกัน (ต่าง < 0.5 จุด) → เลือก X เล็กกว่า
ผลการสำรวจนี้ **ไม่ใช่หลักฐาน** (ข้อมูลถูกเปิดดูแล้ว ใช้ออกแบบเท่านั้น)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from design_data import load_countries  # noqa: E402

from analysis import dar_dca  # noqa: E402  ค่าคงที่/ฟังก์ชันของสูตรที่ล็อกไว้

K = 5
HORIZON = 240
MAP_TIE = 0.01
X_TIE_PTS = 0.5
X_CANDIDATES = (0.25, 0.30, 0.40, 0.50, None)


# ---------------------------------------------------------------- สัญญาณ DAR แบบเวกเตอร์ (รายเดือน = สูตรจริง)
def monthly_z(ret: pd.DataFrame) -> pd.DataFrame:
    """z ของสูตร DAR จริง (รายเดือน) ณ สิ้นเดือน t ของทุกประเทศที่มีประวัติ ≥ 181 เดือน — ตรงกับ ``dar_dca.dar_weights``."""
    lev = (1.0 + ret.fillna(0.0)).cumprod().where(ret.notna() | ret.shift(1).notna())
    lp = np.log(lev)
    out = pd.DataFrame(np.nan, index=ret.index, columns=ret.columns)
    first = {c: ret[c].first_valid_index() for c in ret.columns}
    n_idx = {p: i for i, p in enumerate(ret.index)}
    for t in range(len(ret.index)):
        vals, cols = [], []
        for c in ret.columns:
            f = first[c]
            if f is None:
                continue
            fi = n_idx[f]
            if ret[c].iloc[t:t + 1].isna().any():
                continue
            if t - fi + 1 < dar_dca.HISTORY_MONTHS:  # ต้องมีสิ้นเดือน ≥ 181 ตัวนับรวมเดือนแรก
                continue
            series = lev[c].iloc[fi:t + 1].to_numpy()
            if np.isnan(series).any():
                continue
            ref = float(np.mean(series[len(series) - 1 - dar_dca.AMP_TO: len(series) - dar_dca.AMP_FROM]))
            amp = math.log(ref) - float(np.log(series[-1]))
            old = float(np.log(series[-1 - 60]) - np.log(series[-1 - 180]))
            vals.append(amp + dar_dca.DRIFT_COEF * old); cols.append(c)
        if len(vals) >= 2:
            x = np.array(vals)
            sd = max(float(x.std(ddof=0)), dar_dca.SD_MIN)
            out.loc[ret.index[t], cols] = (x - x.mean()) / sd
    return out


def annual_levels(ret: pd.DataFrame) -> pd.DataFrame:
    """ระดับ (total return index) สิ้นปี — ปีที่ขาดเดือนใดเดือนหนึ่งเป็น NaN."""
    yr = ret.groupby(ret.index.year)
    n = yr.count()
    ann = yr.apply(lambda g: (1.0 + g).prod() - 1.0)
    ann = ann.where(n == 12)
    return (1.0 + ann.fillna(0.0)).cumprod().where(ann.notna() | ann.shift(1).notna())


def annual_z(levels: pd.DataFrame, mapping: str) -> pd.DataFrame:
    """z รายปี ณ สิ้นปี t (ใช้ข้อมูลถึงสิ้นปี t): AMP จากระดับ 5 ปีก่อน (M1 ตัวเดียว · M2 เฉลี่ย t−4,t−5,t−6) · OLD = ln P[t−5] − ln P[t−15]."""
    out = pd.DataFrame(np.nan, index=levels.index, columns=levels.columns)
    years = list(levels.index)
    for i, y in enumerate(years):
        vals, cols = [], []
        for c in levels.columns:
            if i < 15:
                continue
            p = levels[c].iloc[: i + 1].to_numpy()
            if np.isnan(p[i - 15: i + 1]).any():
                continue
            if mapping == "M1":
                ref = p[i - 5]
            else:
                ref = float(np.mean([p[i - 4], p[i - 5], p[i - 6]]))
            amp = math.log(ref) - math.log(p[i])
            old = math.log(p[i - 5]) - math.log(p[i - 15])
            vals.append(amp + dar_dca.DRIFT_COEF * old); cols.append(c)
        if len(vals) >= 2:
            x = np.array(vals)
            sd = max(float(x.std(ddof=0)), dar_dca.SD_MIN)
            out.loc[y, cols] = (x - x.mean()) / sd
    return out


def mapping_consistency(ret: pd.DataFrame, zm: pd.DataFrame) -> dict[str, float]:
    levels = annual_levels(ret)
    res: dict[str, list[float]] = {"M1": [], "M2": []}
    for m in res:
        za = annual_z(levels, m)
        for y in za.index:
            dec = pd.Period(f"{y}-12", freq="M")
            if dec not in zm.index:
                continue
            a, b = za.loc[y].dropna(), zm.loc[dec].dropna()
            common = a.index.intersection(b.index)
            if len(common) >= 5:
                res[m].append(float(a[common].rank().corr(b[common].rank())))
    return {m: float(np.mean(v)) for m, v in res.items() if v} | {"n_years": len(res["M1"])}


# ---------------------------------------------------------------- DCA แบบห้ามขาย (รายเดือน บนข้อมูลสำรวจ)
def topk_weights(z: np.ndarray, avail: np.ndarray, k: int, blocked: np.ndarray) -> np.ndarray:
    """เลือก k กองที่ z สูงสุดจากกองที่ (มีสัญญาณ ∧ ไม่ติดเพดาน) แล้วแบ่งเงินด้วยพื้น/เพดานเดิมของสูตร."""
    cand = np.where(avail & ~blocked)[0]
    if len(cand) == 0:
        return np.zeros(len(z))
    chosen = cand[np.argsort(-z[cand], kind="stable")[:k]]
    n = len(chosen)
    raw = np.maximum(1.0 + z[chosen], 0.0) / n
    w = dar_dca.floor_project(raw, dar_dca.FLOOR_FRAC / n)
    if dar_dca.CAP_MULT / n < 1.0:
        w = dar_dca.cap_project(w, dar_dca.CAP_MULT / n)
    out = np.zeros(len(z)); out[chosen] = w
    return out


def simulate(ret: np.ndarray, z: np.ndarray, start: int, months: int, policy: str, k: int, guard: float | None) -> float:
    """เงินปลายทาง (ลง 1 หน่วยทุกเดือน ไม่ขาย) ของนโยบาย: equal | dar_all | topk."""
    N = ret.shape[1]
    H = np.zeros(N)
    for m in range(months):
        t = start + m
        zt = z[t - 1]                       # สัญญาณจากสิ้นเดือนก่อนหน้า
        avail = ~np.isnan(zt) & ~np.isnan(ret[t])
        if avail.sum() < 2:
            return float("nan")
        if policy == "equal":
            w = np.zeros(N); w[avail] = 1.0 / avail.sum()
        elif policy == "dar_all":
            zz = np.where(avail, zt, 0.0)
            n = int(avail.sum())
            raw = np.zeros(N); raw[avail] = np.maximum(1.0 + zz[avail], 0.0) / n
            w = np.zeros(N)
            w[avail] = dar_dca.cap_project(dar_dca.floor_project(raw[avail], dar_dca.FLOOR_FRAC / n), dar_dca.CAP_MULT / n) if dar_dca.CAP_MULT / n < 1.0 \
                else dar_dca.floor_project(raw[avail], dar_dca.FLOOR_FRAC / n)
        else:
            tot = H.sum()
            blocked = (H / tot >= guard) if (guard is not None and tot > 0) else np.zeros(N, dtype=bool)
            w = topk_weights(np.where(avail, zt, 0.0), avail, k, blocked)
            if w.sum() == 0:
                return float("nan")
        H = H + w
        H = H * (1.0 + np.where(np.isnan(ret[t]), 0.0, ret[t]))
    return float(H.sum())


def summarize_ratio(ratios: np.ndarray) -> dict[str, float]:
    r = ratios[~np.isnan(ratios)]
    return {"n": int(len(r)), "mean_pct": float((r.mean() - 1) * 100), "win_pct": float((r > 1).mean() * 100),
            "p10_pct": float((np.percentile(r, 10) - 1) * 100), "worst_pct": float((r.min() - 1) * 100)}


def main(out_path: Path) -> None:
    ret = load_countries().drop(columns=["Malaysia"], errors="ignore")  # ปิดตลาดไป 2001 — ไม่ผ่านกติกา "ประวัติ ≥ 181 เดือนถึงปัจจุบัน"
    zm = monthly_z(ret)
    mc = mapping_consistency(ret, zm)
    chosen_map = "M1" if abs(mc["M1"] - mc["M2"]) < MAP_TIE else ("M1" if mc["M1"] > mc["M2"] else "M2")
    print(f"สอดคล้องกับ z รายเดือน (Spearman เฉลี่ย {mc['n_years']} ปี): M1={mc['M1']:.3f} M2={mc['M2']:.3f} → เลือก {chosen_map}")

    R = ret.to_numpy(dtype=float)
    Z = zm.to_numpy(dtype=float)
    first_ok = int(np.where(~np.isnan(Z).all(axis=1))[0][0]) + 1
    starts = list(range(first_ok, len(ret) - HORIZON))
    print(f"จุดเริ่ม DCA 20 ปี: {len(starts)} จุด ({ret.index[starts[0]]} → {ret.index[starts[-1]]}) · ประเทศ {ret.shape[1]}")
    eq = np.array([simulate(R, Z, s, HORIZON, "equal", K, None) for s in starts])
    dall = np.array([simulate(R, Z, s, HORIZON, "dar_all", K, None) for s in starts])
    results = {"mapping": mc, "chosen_mapping": chosen_map, "n_starts": len(starts), "dar_all_vs_equal": summarize_ratio(dall / eq), "guards": {}}
    for g in X_CANDIDATES:
        tk = np.array([simulate(R, Z, s, HORIZON, "topk", K, g) for s in starts])
        results["guards"]["none" if g is None else f"{int(g*100)}%"] = {"vs_equal": summarize_ratio(tk / eq), "vs_dar_all": summarize_ratio(tk / dall)}
        print(f"X={g}: เทียบแบ่งเท่ากัน {results['guards']['none' if g is None else f'{int(g*100)}%']['vs_equal']}")
    best = max(results["guards"], key=lambda k_: results["guards"][k_]["vs_equal"]["p10_pct"])
    top = results["guards"][best]["vs_equal"]["p10_pct"]
    near = [k_ for k_, v in results["guards"].items() if top - v["vs_equal"]["p10_pct"] < X_TIE_PTS]
    order = {"25%": 0.25, "30%": 0.30, "40%": 0.40, "50%": 0.50, "none": 9.9}
    results["chosen_guard"] = min(near, key=lambda k_: order[k_])
    print("เลือกเพดาน:", results["chosen_guard"], "(ใกล้เคียงกัน:", near, ")")
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(Path(__file__).parent / "results_design.json")
