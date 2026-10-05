# -*- coding: utf-8 -*-
"""ตรวจความแม่นยำของโมเดลเอง: ผลตอบแทนที่ simulation สร้าง ต้องมี "หน้าตา" เหมือนประวัติจริงของกองชุดเดียวกัน.

ถ้าโมเดล **หลวมกว่าอดีต** (ผันผวนต่ำกว่า ขาดทุนลึกน้อยกว่า หางบางกว่า) ตัวเลขความเสี่ยงที่แสดงให้ผู้ใช้จะต่ำเกินจริง —
ต้องรู้และบอก · ถ้า **โหดกว่าอดีต** เป็นเรื่องที่ตั้งใจ (เหตุการณ์ใหญ่ซ้อนทับ + ความผันผวนต่อเส้นทาง) แต่ต้องรู้ว่าโหดเท่าไร

ตรวจ 6 อย่างบนผลตอบแทนรายเดือนที่ลบค่าเฉลี่ยแล้ว (เทียบเฉพาะ "รูปร่าง" ไม่เทียบผลตอบแทนคาดหวัง ซึ่งเป็นข้อสมมติ):
ความผันผวน · ความสัมพันธ์ · หางหนา (kurtosis) · ความผันผวนเป็นกลุ่ม (ACF ของ |r|) · และสองข้อที่เทียบ "ประวัติจริงกับการกระจายของโมเดล"
คือ MaxDD ของพอร์ตแบ่งเท่ากัน และ 12 เดือนที่แย่สุด — ประวัติจริงควรอยู่กลางการกระจาย ไม่ใช่ตกขอบ

ข้อจำกัดที่ต้องพูด: ประวัติจริงมีทางเดียว (ราว 20 ปี) ข้อ MaxDD/12 เดือนที่แย่สุดจึงเป็นการดูว่า "ตกขอบหรือไม่" ไม่ใช่การทดสอบทางสถิติ
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from simulation import engine

# เกณฑ์ (ประกาศก่อนดูผล — ห้ามปรับให้ผ่านทีหลัง) ------------------------------------------------
VOL_RATIO_OK = (0.90, 1.45)       # ผันผวนของโมเดล ÷ อดีต: ต่ำกว่า 0.90 = หลวมกว่าอดีต · สูงกว่า 1.45 = โหดกว่ามาก
CORR_MAX_ABS_DIFF = 0.10          # ค่าเฉลี่ย |ความสัมพันธ์โมเดล − อดีต| ของทุกคู่
KURT_RATIO_MIN = 0.60             # หางของโมเดลบางกว่าอดีตเกินนี้ = หลวมกว่าอดีต
ACF_MIN_DIFF = -0.10              # ความผันผวนเป็นกลุ่มของโมเดลน้อยกว่าอดีตเกินนี้ = หลวมกว่าอดีต
COVERAGE_OK = (5.0, 95.0)         # เปอร์เซนไทล์ของประวัติจริงในการกระจายของโมเดล ต้องอยู่ในช่วงนี้


def _acf1_abs(r: np.ndarray) -> float:
    a = np.abs(r - r.mean(axis=0))
    a = a - a.mean(axis=0)
    num = (a[1:] * a[:-1]).sum(axis=0)
    den = (a * a).sum(axis=0)
    return float(np.mean(num / den))


def _excess_kurtosis(r: np.ndarray) -> np.ndarray:
    d = r - r.mean(axis=0)
    return (d ** 4).mean(axis=0) / (d ** 2).mean(axis=0) ** 2 - 3.0


def _ew_log_returns(r: np.ndarray) -> np.ndarray:
    """ผลตอบแทน log ของพอร์ตแบ่งเท่ากันที่ปรับสมดุลรายเดือน (r: ... × N)."""
    return np.log(np.exp(r).mean(axis=-1))


def _max_drawdown(logret: np.ndarray) -> np.ndarray:
    """MaxDD (สัดส่วน) ของแต่ละเส้นทาง — logret: P × T."""
    level = np.cumsum(logret, axis=-1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros(level.shape[:-1] + (1,)), level], axis=-1), axis=-1)[..., 1:]
    return 1.0 - np.exp((level - peak).min(axis=-1))


def _worst_12m(logret: np.ndarray) -> np.ndarray:
    c = np.concatenate([np.zeros(logret.shape[:-1] + (1,)), np.cumsum(logret, axis=-1)], axis=-1)
    return (c[..., 12:] - c[..., :-12]).min(axis=-1)


def _percentile_of(value: float, dist: np.ndarray) -> float:
    return float((dist < value).mean() * 100.0)


def historical_stats(panel: dict) -> dict[str, Any]:
    """สถิติของประวัติจริง (ผลตอบแทนรายเดือนช่วงสอบเทียบ) — ลบค่าเฉลี่ยเฉพาะที่ที่ต้องการ."""
    n = len(panel["funds"])
    r = np.asarray(panel["chron"]["X"])[:, :n]
    return {"months": int(len(r)), "vol_pct": (r.std(axis=0, ddof=1) * math.sqrt(12) * 100).tolist(),
            "corr": np.corrcoef(r.T), "kurt": _excess_kurtosis(r).tolist(), "acf1_abs": _acf1_abs(r), "returns": r}


def simulated_returns(panel: dict, *, paths: int, seed: int, world: str = "rw", months: int = 240, event_mult: float = 1.0) -> np.ndarray:
    """ผลตอบแทน log รายเดือนของกองที่เอนจินสร้าง (P × months × N) — ตามที่ผู้ใช้เห็นในผล simulation (รวมเหตุการณ์ใหญ่)."""
    cfg = engine.Config(world=world, drift="mid", event_mult=event_mult, P=paths, T=months, seed=seed, arms=("EQ",),
                        horizons=(months,), debug=paths, fx_revert=(0.0 if world == "boot" else engine.FX_REVERT))
    res = engine.run_chunk(panel, cfg)
    LL = res["LL"]
    Hr = panel["live_me_logs"].shape[0]
    return np.diff(LL[:, Hr - 1: Hr + months, :], axis=1)


def calibration_report(panel: dict, *, paths: int = 3000, seed: int = 20261005, world: str = "rw") -> dict[str, Any]:
    """เทียบ "หน้าตา" ผลตอบแทนของโมเดลกับประวัติจริง → ``{"checks": [...], "flags": [...], ...}`` (ไม่มี LLM)."""
    funds = list(panel["funds"])
    hist = historical_stats(panel)
    months = min(240, hist["months"])
    sim = simulated_returns(panel, paths=paths, seed=seed, world=world, months=months)
    flat = sim.reshape(-1, sim.shape[-1])
    checks: list[dict[str, Any]] = []

    # 1) ความผันผวนต่อปีของแต่ละกอง
    sim_vol = flat.std(axis=0, ddof=1) * math.sqrt(12) * 100
    for f, hv, sv in zip(funds, hist["vol_pct"], sim_vol):
        ratio = float(sv / hv)
        status = "ok" if VOL_RATIO_OK[0] <= ratio <= VOL_RATIO_OK[1] else ("หลวมกว่าอดีต" if ratio < VOL_RATIO_OK[0] else "โหดกว่าอดีตมาก")
        checks.append({"name": f"ความผันผวนต่อปี {f}", "history": round(hv, 2), "model": round(float(sv), 2), "ratio": round(ratio, 2),
                       "unit": "%", "status": status, "rule": f"ratio {VOL_RATIO_OK[0]}–{VOL_RATIO_OK[1]}"})

    # 2) ความสัมพันธ์
    if len(funds) > 1:
        iu = np.triu_indices(len(funds), 1)
        diff = float(np.abs(np.corrcoef(flat.T)[iu] - hist["corr"][iu]).mean())
        checks.append({"name": "ความสัมพันธ์ระหว่างกอง (เฉลี่ย |ต่าง|)", "history": None, "model": None, "ratio": None, "value": round(diff, 3),
                       "status": "ok" if diff <= CORR_MAX_ABS_DIFF else "ต่างจากอดีต", "rule": f"≤ {CORR_MAX_ABS_DIFF}"})

    # 3) หางหนา
    sk = _excess_kurtosis(flat)
    hk = np.array(hist["kurt"])
    ratio_k = float(np.mean(sk / np.where(hk > 0.2, hk, np.nan)) if (hk > 0.2).any() else float("nan"))
    ok_k = (not math.isnan(ratio_k)) and ratio_k >= KURT_RATIO_MIN
    checks.append({"name": "หางหนา (kurtosis เกิน) เฉลี่ยทุกกอง", "history": round(float(hk.mean()), 2), "model": round(float(sk.mean()), 2),
                   "ratio": None if math.isnan(ratio_k) else round(ratio_k, 2),
                   "status": "ok" if ok_k or math.isnan(ratio_k) else "หลวมกว่าอดีต", "rule": f"ratio ≥ {KURT_RATIO_MIN}"})

    # 4) ความผันผวนเป็นกลุ่ม
    sim_acf = float(np.mean([_acf1_abs(sim[p]) for p in range(min(paths, 500))]))
    d_acf = sim_acf - hist["acf1_abs"]
    checks.append({"name": "ความผันผวนเป็นกลุ่ม (ACF ของ |ผลตอบแทน| lag 1)", "history": round(hist["acf1_abs"], 3), "model": round(sim_acf, 3),
                   "value": round(d_acf, 3), "status": "ok" if d_acf >= ACF_MIN_DIFF else "หลวมกว่าอดีต", "rule": f"ต่าง ≥ {ACF_MIN_DIFF}"})

    # 5–6) ประวัติจริงอยู่ตรงไหนของการกระจายของโมเดล (พอร์ตแบ่งเท่ากัน ปรับสมดุลรายเดือน)
    hist_r = _ew_log_returns(hist["returns"][:months])
    sim_r = _ew_log_returns(sim)
    h_dd, s_dd = float(_max_drawdown(hist_r[None, :])[0]), _max_drawdown(sim_r)
    pct_dd = _percentile_of(h_dd, s_dd)
    checks.append({"name": f"MaxDD ของพอร์ตแบ่งเท่ากัน ({months} เดือน)", "history": round(h_dd * 100, 1), "model": round(float(np.median(s_dd)) * 100, 1),
                   "unit": "%", "percentile_of_history": round(pct_dd, 1),
                   "status": "ok" if COVERAGE_OK[0] <= pct_dd <= COVERAGE_OK[1] else ("โมเดลหลวมกว่าอดีต" if pct_dd > COVERAGE_OK[1] else "โมเดลโหดกว่าอดีตมาก"),
                   "rule": f"ประวัติอยู่เปอร์เซนไทล์ {COVERAGE_OK[0]:.0f}–{COVERAGE_OK[1]:.0f} ของโมเดล (model = มัธยฐาน)"})
    h_w, s_w = float(_worst_12m(hist_r[None, :])[0]), _worst_12m(sim_r)
    pct_w = _percentile_of(h_w, s_w)  # ค่าลบมาก = แย่ — ประวัติแย่กว่าโมเดลเกือบทั้งหมด = เปอร์เซนไทล์ต่ำ
    checks.append({"name": f"12 เดือนที่แย่สุดของพอร์ตแบ่งเท่ากัน ({months} เดือน)", "history": round((math.exp(h_w) - 1) * 100, 1),
                   "model": round((math.exp(float(np.median(s_w))) - 1) * 100, 1), "unit": "%", "percentile_of_history": round(pct_w, 1),
                   "status": "ok" if COVERAGE_OK[0] <= pct_w <= COVERAGE_OK[1] else ("โมเดลโหดกว่าอดีตมาก" if pct_w > COVERAGE_OK[1] else "โมเดลหลวมกว่าอดีต"),
                   "rule": f"ประวัติอยู่เปอร์เซนไทล์ {COVERAGE_OK[0]:.0f}–{COVERAGE_OK[1]:.0f} ของโมเดล"})

    flags_loose = [c["name"] for c in checks if "หลวม" in c["status"]]
    flags_harsh = [c["name"] for c in checks if "โหด" in c["status"]]
    return {"world": world, "paths": paths, "seed": seed, "months": months, "history_months": hist["months"], "funds": funds,
            "checks": checks, "flags_loose": flags_loose, "flags_harsh": flags_harsh,
            "verdict": ("ok" if not flags_loose else "หลวมกว่าอดีตในบางเรื่อง — ตัวเลขความเสี่ยงอาจต่ำเกินจริง"),
            "caveat": "ประวัติจริงมีทางเดียว (ราว 20 ปี) — ข้อ MaxDD/12 เดือนที่แย่สุดดูแค่ว่าตกขอบหรือไม่ ไม่ใช่การทดสอบทางสถิติ"}
