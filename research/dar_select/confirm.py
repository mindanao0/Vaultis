# -*- coding: utf-8 -*-
"""ขั้น 5 ของ research/dar_select/PLAN.md — รันยืนยัน "ครั้งเดียว" ตาม PREREG.md (ล็อกด้วย SHA-256 ก่อนแตะข้อมูลยืนยัน).

    python confirm.py exploration   # ทดสอบท่อคำนวณบนข้อมูลสำรวจที่แปลงเป็นรายปี (ไม่ใช่หลักฐาน — ไว้ตรวจว่าโค้ดทำงาน)
    python confirm.py jst           # รันยืนยันบน JST Macrohistory R6, 1870–1974, ไม่รวมสหรัฐ → results_confirm.json

ห้ามแก้ไฟล์นี้หลังล็อก · ผ่านหรือไม่ตามเกณฑ์ใน PREREG.md ห้ามปรับแล้วรันซ้ำ (สูตรใหม่ = PREREG ใหม่)
ข้อมูล JST ใช้ได้ตามใบอนุญาต CC BY-NC-SA 4.0 (ต้องอ้างอิง ห้ามแจกจ่ายซ้ำ — ไฟล์ข้อมูลไม่อยู่ใน git):
Òscar Jordà, Moritz Schularick, Alan M. Taylor (2017) "Macrofinancial History and the New Business Cycle Facts", NBER Macroeconomics Annual 2016, 31.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from analysis import dar_dca  # ค่าคงที่/ฟังก์ชันของสูตรที่ล็อกไว้ — นำมาใช้ ไม่คัดลอก

HERE = Path(__file__).parent
K_PRIMARY, K_SECONDARY = 5, 8
GUARD = 0.25                 # ออกแบบบนข้อมูลสำรวจ (design.py → results_design.json: เลือก 25%)
HORIZON_YEARS = 20
MIN_HISTORY_YEARS = 16       # ต้องมีระดับสิ้นปีต่อเนื่อง 16 ตัว (t−15..t) — ตรงกับ 181 เดือนของสูตรรายเดือน
PASS_MEAN_PCT = 1.0          # เงินปลายทางเฉลี่ยเทียบแบ่งเท่ากัน ≥ +1% (ระยะเผื่อ survivorship)
PASS_WIN_PCT = 60.0          # ชนะอย่างน้อย 60% ของหน้าต่าง
PASS_P10_PCT = -5.0          # 10% ที่แย่สุดต้องไม่แย่กว่า −5%
EXCLUDED = ("USA",)          # ตัดสหรัฐออก: ดัชนีสหรัฐ 1926–1974 ถูกเปิดดูไปแล้วในรอบ 1


# ---------------------------------------------------------------- ข้อมูลรายปี
def jst_usd_returns(path: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(ผลตอบแทนหุ้นรวมปันผลเป็น USD, เป็นสกุลท้องถิ่น, dividend-price ratio) ปี × ประเทศ ช่วง 1870–1974 ไม่รวมสหรัฐ.

    USD: (1 + eq_tr) × (xrusd[t−1] / xrusd[t]) − 1  (xrusd = สกุลท้องถิ่นต่อ 1 USD — ตรวจทิศแล้วจากอัตรา ไม่ใช่จากผลตอบแทนหุ้น)
    ปีที่ขาดค่าใดค่าหนึ่ง = NaN (ตลาดนั้นไม่พร้อมในปีนั้น) — ไม่เติมค่า
    """
    d = pd.read_stata(path, convert_categoricals=False)
    d = d[~d["country"].isin(EXCLUDED)]
    eq = d.pivot(index="year", columns="country", values="eq_tr")
    fx = d.pivot(index="year", columns="country", values="xrusd")
    dp = d.pivot(index="year", columns="country", values="eq_dp")
    usd = (1.0 + eq) * (fx.shift(1) / fx) - 1.0          # คำนวณก่อนตัดช่วงปี: ปีแรกของช่วงต้องใช้อัตราแลกเปลี่ยนปีก่อนหน้า
    window = (eq.index >= 1870) & (eq.index <= 1974)
    return usd.where(usd > -1.0)[window], eq.where(eq > -1.0)[window], dp[window]


def annualize_monthly(ret: pd.DataFrame) -> pd.DataFrame:
    """ผลตอบแทนรายเดือน → รายปีปฏิทิน (ปีที่ครบ 12 เดือนเท่านั้น) — ไว้ทดสอบท่อคำนวณบนข้อมูลสำรวจ."""
    g = ret.groupby(ret.index.year)
    ann = g.apply(lambda x: (1.0 + x).prod() - 1.0)
    return ann.where(g.count() == 12)


# ---------------------------------------------------------------- สัญญาณ DAR รายปี (M1 — ล็อกใน PREREG)
def levels_from_returns(ann: pd.DataFrame) -> pd.DataFrame:
    """ระดับสิ้นปี (total return index) — ปีที่ไม่มีผลตอบแทน = NaN (และถือว่าอนุกรมขาดตอน ไม่ต่อข้าม)."""
    lev = (1.0 + ann.fillna(0.0)).cumprod()
    return lev.where(ann.notna())


def annual_z(levels: pd.DataFrame) -> pd.DataFrame:
    """z ณ สิ้นปี t: AMP = ln P[t−5] − ln P[t−0]; OLD = ln P[t−5] − ln P[t−15]; DAR = AMP + 0.5·OLD; z = (DAR − ค่าเฉลี่ย)/max(sd, 0.15).

    ต้องมี P[t−15..t] ครบทุกตัว (ไม่ขาดตอน) — มิฉะนั้นกองนั้นไม่มีสัญญาณปีนั้น · ต้องมีกองที่มีสัญญาณ ≥ 2
    """
    out = pd.DataFrame(np.nan, index=levels.index, columns=levels.columns)
    L = levels.to_numpy(dtype=float)
    for i in range(MIN_HISTORY_YEARS - 1, len(levels)):
        vals, cols = [], []
        for j in range(L.shape[1]):
            p = L[i - 15: i + 1, j]
            if np.isnan(p).any():
                continue
            amp = math.log(p[10]) - math.log(p[15])           # P[t−5] เทียบ P[t]
            old = math.log(p[10]) - math.log(p[0])            # P[t−5] เทียบ P[t−15]
            vals.append(amp + dar_dca.DRIFT_COEF * old); cols.append(j)
        if len(vals) >= 2:
            x = np.array(vals)
            sd = max(float(x.std(ddof=0)), dar_dca.SD_MIN)
            out.iloc[i, cols] = (x - x.mean()) / sd
    return out


# ---------------------------------------------------------------- นโยบาย
def _dar_split(z: np.ndarray, idx: np.ndarray) -> np.ndarray:
    n = len(idx)
    raw = np.maximum(1.0 + z[idx], 0.0) / n
    w = dar_dca.floor_project(raw, dar_dca.FLOOR_FRAC / n)
    if dar_dca.CAP_MULT / n < 1.0:
        w = dar_dca.cap_project(w, dar_dca.CAP_MULT / n)
    return w


def policy_weights(kind: str, z: np.ndarray, dp: np.ndarray | None, avail: np.ndarray, H: np.ndarray, k: int) -> np.ndarray:
    """น้ำหนักเงินใหม่ของปีนี้ — ``avail`` = มีสัญญาณและมีผลตอบแทนปีนี้ (ซื้อได้จริง)."""
    N = len(z)
    w = np.zeros(N)
    idx_all = np.where(avail)[0]
    if len(idx_all) < 2:
        return w
    if kind == "equal":
        w[idx_all] = 1.0 / len(idx_all)
        return w
    if kind == "dar_all":
        w[idx_all] = _dar_split(z, idx_all)
        return w
    tot = H.sum()
    blocked = (H / tot >= GUARD) if tot > 0 else np.zeros(N, dtype=bool)   # กองที่มูลค่า ≥ 25% ของพอร์ต ห้ามซื้อเพิ่ม
    cand = np.where(avail & ~blocked)[0]
    if len(cand) == 0:
        cand = idx_all          # ทุกกองที่ซื้อได้ติดเพดาน (เช่นมี ≤ 4 ตลาด) → ปลดเพดานเฉพาะปีนั้น ไม่ให้แผนค้างซื้อไม่ได้ (ล็อกใน PREREG)
    if kind == "yield_topk":
        cand = np.array([i for i in cand if dp is not None and not np.isnan(dp[i])], dtype=int)
    if len(cand) == 0:
        return w
    score = z[cand] if kind == "dar_topk" else dp[cand]
    chosen = cand[np.argsort(-score, kind="stable")[:k]]
    if kind == "dar_topk":
        w[chosen] = _dar_split(z, chosen)
    else:
        w[chosen] = 1.0 / len(chosen)
    return w


def simulate_window(ann: np.ndarray, Z: np.ndarray, DP: np.ndarray | None, start: int, kind: str, k: int) -> tuple[float, np.ndarray]:
    """ลง 1 หน่วยต้นปีทุกปี 20 ปี ห้ามขาย — คืน (เงินปลายทาง, มูลค่าถือครองต่อกอง). ตลาดที่ปีนั้นไม่มีผลตอบแทน = มูลค่าคงเดิม (0%)."""
    N = ann.shape[1]
    H = np.zeros(N)
    for t in range(start, start + HORIZON_YEARS):
        z = Z[t - 1]
        avail = ~np.isnan(z) & ~np.isnan(ann[t])
        w = policy_weights(kind, np.where(np.isnan(z), 0.0, z), None if DP is None else DP[t - 1], avail, H, k)
        if w.sum() == 0:
            return float("nan"), H
        H = H + w
        H = H * (1.0 + np.where(np.isnan(ann[t]), 0.0, ann[t]))
    return float(H.sum()), H


# ---------------------------------------------------------------- สถิติ
def newey_west_se(x: np.ndarray, lags: int) -> float:
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    n = len(x)
    v = float((x * x).sum() / n)
    for L in range(1, lags + 1):
        w = 1.0 - L / (lags + 1.0)
        v += 2.0 * w * float((x[L:] * x[:-L]).sum() / n)
    return math.sqrt(max(v, 0.0) / n)


def summarize(ratio: np.ndarray, years: list[int]) -> dict:
    ok = ~np.isnan(ratio)
    r = ratio[ok]
    ann = r ** (1.0 / HORIZON_YEARS) - 1.0
    se = newey_west_se(ann, HORIZON_YEARS - 1) if len(ann) > HORIZON_YEARS else float("nan")
    half = len(r) // 2
    return {
        "n": int(len(r)), "mean_pct": float((r.mean() - 1) * 100), "median_pct": float((np.median(r) - 1) * 100),
        "win_pct": float((r > 1).mean() * 100), "p10_pct": float((np.percentile(r, 10) - 1) * 100), "worst_pct": float((r.min() - 1) * 100),
        "annual_excess_pct": float(ann.mean() * 100), "annual_excess_ci95_pct": [float((ann.mean() - 1.96 * se) * 100), float((ann.mean() + 1.96 * se) * 100)],
        "first_half_mean_pct": float((r[:half].mean() - 1) * 100), "second_half_mean_pct": float((r[half:].mean() - 1) * 100),
        "first_year": years[int(np.where(ok)[0][0])], "last_year": years[int(np.where(ok)[0][-1])],
    }


def run_pipeline(ann: pd.DataFrame, dp: pd.DataFrame | None) -> dict:
    """ทุกกฎ × K ที่ล็อก บนผลตอบแทนรายปี ``ann`` (ปี × ตลาด) → สถิติเทียบแบ่งเท่ากันและ DAR เอียงทุกกอง."""
    levels = levels_from_returns(ann)
    Zdf = annual_z(levels)
    A = ann.to_numpy(dtype=float)
    Z = Zdf.to_numpy(dtype=float)
    DP = None if dp is None else dp.reindex(index=ann.index, columns=ann.columns).to_numpy(dtype=float)
    years = list(ann.index)
    first_sig = next(i for i in range(len(years)) if not np.isnan(Z[i]).all()) + 1
    starts = [s for s in range(first_sig, len(years) - HORIZON_YEARS + 1) if (~np.isnan(Z[s - 1])).sum() >= 2]
    wins = [years[s] for s in starts]
    eq = np.array([simulate_window(A, Z, DP, s, "equal", K_PRIMARY)[0] for s in starts])
    dall = np.array([simulate_window(A, Z, DP, s, "dar_all", K_PRIMARY)[0] for s in starts])
    out = {"n_windows": len(starts), "first_start_year": wins[0], "last_start_year": wins[-1], "markets": list(ann.columns),
           "dar_all_vs_equal": summarize(dall / eq, wins), "rules": {}}
    kinds = ["dar_topk"] + (["yield_topk"] if DP is not None else [])
    for kind in kinds:
        for k in (K_PRIMARY, K_SECONDARY):
            res = [simulate_window(A, Z, DP, s, kind, k) for s in starts]
            tw = np.array([r[0] for r in res])
            Hf = np.array([r[1] / max(r[1].sum(), 1e-300) for r in res])
            entry = {"vs_equal": summarize(tw / eq, wins), "vs_dar_all": summarize(tw / dall, wins),
                     "max_single_market_weight_pct": {"mean": float(Hf.max(axis=1).mean() * 100), "max": float(Hf.max() * 100)},
                     "markets_held_at_end": float((Hf > 1e-9).sum(axis=1).mean())}
            out["rules"][f"{kind}_K{k}"] = entry
    out["verdict"] = verdict(out)
    return out


def verdict(out: dict) -> dict:
    """เกณฑ์ผ่านหลัก (ล็อกใน PREREG) — ใช้กับ K = 5 ของแต่ละกฎ: ครบทุกข้อถึงจะผ่าน."""
    v = {}
    for name, e in out["rules"].items():
        if not name.endswith(f"_K{K_PRIMARY}"):
            continue
        s, d = e["vs_equal"], e["vs_dar_all"]
        checks = {"mean>=+1%": s["mean_pct"] >= PASS_MEAN_PCT, "win>=60%": s["win_pct"] >= PASS_WIN_PCT,
                  "p10>=-5%": s["p10_pct"] >= PASS_P10_PCT, "not_worse_than_DAR_tilt_all": d["mean_pct"] >= 0.0}
        v[name] = {"checks": checks, "PASS": all(checks.values())}
    return v


def main(which: str) -> None:
    if which == "exploration":
        sys.path.insert(0, str(HERE))
        from design_data import load_countries

        ann = annualize_monthly(load_countries().drop(columns=["Malaysia"], errors="ignore"))
        res = run_pipeline(ann, None)
        path = HERE / "results_pipeline_check_exploration.json"
    elif which == "jst":
        usd, local, dp = jst_usd_returns(sys.argv[2] if len(sys.argv) > 2 else "/scratch/jst/JSTdatasetR6.dta")
        res = {"usd": run_pipeline(usd, dp)}
        res["local_currency_secondary"] = run_pipeline(local, dp)
        path = HERE / "results_confirm.json"
    else:
        raise SystemExit("usage: confirm.py exploration|jst [path]")
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("เขียน", path.name)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
