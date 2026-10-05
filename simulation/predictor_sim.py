# -*- coding: utf-8 -*-
"""จำลองเกณฑ์ตัดสินของโหมด PREDICT: เกณฑ์ที่ล็อกไว้ (PREREG ข้อ 6) แยก "โชค" ออกจาก "เอดจ์จริง" ได้ดีแค่ไหน.

**ไม่ใช่หลักฐานว่าตัวทำนายใดได้ผล** — โลกจำลองให้ผลตามที่สมมติไว้ในโลกนั้น (ตรงกับข้อจำกัดของ simulation ทั้งระบบ) สิ่งที่วัดได้จริงมีสองอย่าง:
* โลก ``rw`` (ราคา 5 กองเป็นการเดินสุ่มที่มีค่าเฉลี่ย/ความแปรปรวนร่วมจากราคาจริง · **ไม่มีใครทายได้**): ตัวทำนายที่ไม่มีฝีมือผ่านเกณฑ์โดยบังเอิญบ่อยแค่ไหน (อัตราผ่านลวง)
* โลก ``mom`` (สมมติเอดจ์ของโมเดลโมเมนตัม +``MOM_EDGE_PCT_MONTH``%/เดือนต่อกอง ตามสัญญาณ): ถ้ามีเอดจ์ขนาดนั้นจริง เกณฑ์จับได้กี่ % ภายใน 36 cohort (กำลังทดสอบ)
จำลองเฉพาะ momentum_12_1 และ mean_reversion ช่วง 1 เดือน — Prophet/scorecard ช้าเกินไปต่อเส้นทาง จึงไม่ถูกจำลอง (ระบุในผลทุกครั้ง)
ผลไม่ไหลเข้าเกณฑ์ตัดสินหรือตัวทำนาย · ไม่มี LLM ไม่ยิงเน็ต (ใช้ราคาที่งานรายวันดึงมาแล้ว)
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from analysis import predict_lab as pl
from simulation import data as sim_data

FILE = "predict_sim.json"
DEFAULT_PATHS = 2000
CHUNK = 500
STEP = 21                       # วันทำการต่อ 1 cohort (≈ 1 เดือน = ช่วง 1m)
N_COHORTS = pl.N_MIN["1m"]      # 36
WARMUP = pl.MR_WINDOW + 1       # ให้ mean_reversion มีประวัติครบ 756 แท่งตั้งแต่ cohort แรก
MOM_EDGE_PCT_MONTH = 0.5
SEED = 20261005
MAX_AGE_DAYS = 7.0
LIMITATIONS = (
    "ผลคือสิ่งที่สมมติไว้ในโลกจำลอง — ไม่ใช่หลักฐานว่าตัวทำนายใดได้ผลในตลาดจริง · โลก rw = เดินสุ่มปกติหลายตัวแปรจากค่าเฉลี่ย/ความแปรปรวนร่วมของราคาจริง (ไม่มีหางหนัก ไม่มี regime)",
    "จำลองเฉพาะ momentum_12_1 และ mean_reversion ช่วง 1 เดือน — Prophet และ scorecard ไม่ถูกจำลอง (ช้าเกินไปต่อเส้นทาง)",
    "โลก mom สมมติเอดจ์ของโมเดลโมเมนตัมขนาด +0.5%/เดือนต่อกอง (สมมติ ไม่ได้วัด) ใช้ดูกำลังของเกณฑ์เท่านั้น",
    "ผลห้ามไหลเข้าเกณฑ์ตัดสินของ PREREG หรือใช้ปรับตัวทำนาย — ถ้าอัตราผ่านลวงสูงกว่าที่ล็อกไว้ ต้องทำ pre-registration ฉบับใหม่",
)


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="seconds")


def _moments(prices: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, str, int]:
    cols = [t for t in pl.TICKERS if t in prices.columns]
    if len(cols) < len(pl.TICKERS):
        raise ValueError(f"จำลองเกณฑ์ตัดสินต้องมีราคาครบ {pl.TICKERS} (ขาด {sorted(set(pl.TICKERS) - set(cols))})")
    lr = np.log(prices[list(pl.TICKERS)].dropna()).diff().dropna()
    if len(lr) < 500:
        raise ValueError(f"ราคาร่วมกันของ 5 กองมี {len(lr)} วัน (ต้อง ≥ 500) — ประมาณความแปรปรวนร่วมไม่ได้")
    return lr.mean().to_numpy(), lr.cov().to_numpy(), f"{lr.index.max():%Y-%m-%d}", len(lr)


def _evaluate(logp: np.ndarray, days: np.ndarray) -> dict[str, np.ndarray]:
    """ต่อเส้นทาง: ส่วนเกินความแม่น / ตะกร้า ต่อ cohort ของตัวทำนายสองตัว — คืนอาร์เรย์ (paths, cohorts)."""
    out: dict[str, np.ndarray] = {}
    fwd = logp[:, days + STEP, :] - logp[:, days, :]                       # ผลตอบแทนล็อกช่วงถัดไป (paths, K, 5)
    went_up = fwd > 0
    simple = np.expm1(fwd) * 100.0
    sigs = {
        "momentum_12_1": logp[:, days - pl.MOM_SKIP, :] - logp[:, days - pl.MOM_LOOKBACK, :] > 0,
    }
    mr = np.empty_like(sigs["momentum_12_1"])
    for k, d in enumerate(days):
        w = logp[:, d - pl.MR_WINDOW + 1: d + 1, :]
        z = (w[:, -1, :] - w.mean(axis=1)) / w.std(axis=1, ddof=1)
        mr[:, k, :] = z < 0
    sigs["mean_reversion"] = mr
    base = went_up.mean(axis=2)
    shadow = simple.mean(axis=2)
    for name, up in sigs.items():
        acc = (up == went_up).mean(axis=2)
        n_up = up.sum(axis=2)
        basket = np.where(n_up > 0, (simple * up).sum(axis=2) / np.maximum(n_up, 1), 0.0)
        out[f"{name}.excess_acc_pp"] = (acc - base) * 100.0
        out[f"{name}.excess_basket"] = basket - shadow
    return out


def _gate(excess_acc: np.ndarray, excess_basket: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """เกณฑ์ข้อ 6 ต่อเส้นทาง (36 cohort, ช่วง 1 เดือน) · คืน (ผ่านไหม, ส่วนเกินเฉลี่ย)."""
    from scipy import stats  # noqa: PLC0415

    n = excess_acc.shape[1]
    m = excess_acc.mean(axis=1)
    sd = excess_acc.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(sd > 0, m / (sd / math.sqrt(n)), 0.0)
    p = stats.t.sf(t, df=n - 1)
    ok = (m >= pl.MIN_EXCESS_ACC_PP) & (p < pl.ALPHA) & (excess_basket.mean(axis=1) > 0)
    return ok, m


def simulate_gate(prices: pd.DataFrame, *, paths: int = DEFAULT_PATHS, seed: int = SEED) -> dict[str, Any]:
    mu, cov, last_day, n_obs = _moments(prices)
    chol = np.linalg.cholesky(cov + 1e-12 * np.eye(len(mu)))
    T = WARMUP + N_COHORTS * STEP + STEP
    days = WARMUP - 1 + STEP * np.arange(N_COHORTS)                        # ดัชนีของ logp (ยาว T+1) ที่ cohort k บันทึก
    rng = np.random.default_rng(seed)
    acc: dict[str, dict[str, list[np.ndarray]]] = {w: {} for w in ("rw", "mom")}
    for start in range(0, paths, CHUNK):
        n = min(CHUNK, paths - start)
        for world in ("rw", "mom"):
            r = mu + rng.standard_normal((n, T, len(mu))) @ chol.T
            if world == "mom":
                logp = np.zeros((n, T + 1, len(mu)))
                logp[:, 1:WARMUP, :] = np.cumsum(r[:, :WARMUP - 1, :], axis=1)
                for k in range(N_COHORTS):
                    d = int(days[k])
                    sig = np.where(logp[:, d - pl.MOM_SKIP, :] - logp[:, d - pl.MOM_LOOKBACK, :] > 0, 1.0, -1.0)
                    blk = r[:, d:d + STEP, :] + (MOM_EDGE_PCT_MONTH / 100.0 / STEP) * sig[:, None, :]
                    logp[:, d + 1:d + 1 + STEP, :] = logp[:, d:d + 1, :] + np.cumsum(blk, axis=1)
                if N_COHORTS and int(days[-1]) + STEP + 1 > T + 1:
                    raise AssertionError("ขอบเขตเวลาไม่พอ")
            else:
                logp = np.concatenate([np.zeros((n, 1, len(mu))), np.cumsum(r, axis=1)], axis=1)
            for key, val in _evaluate(logp, days).items():
                acc[world].setdefault(key, []).append(val)
    results: dict[str, Any] = {}
    for world, d in acc.items():
        cat = {k: np.concatenate(v, axis=0) for k, v in d.items()}
        for name in ("momentum_12_1", "mean_reversion"):
            ok, m = _gate(cat[f"{name}.excess_acc_pp"], cat[f"{name}.excess_basket"])
            results[f"{world}|{name}"] = {
                "world": world, "predictor": name, "pass_rate_pct": float(ok.mean() * 100.0),
                "mean_excess_acc_pp": float(m.mean()), "sd_excess_acc_pp": float(m.std(ddof=1)),
                "p05_excess_acc_pp": float(np.percentile(m, 5)), "p95_excess_acc_pp": float(np.percentile(m, 95)),
            }
    from scipy import stats  # noqa: PLC0415

    z = float(stats.norm.isf(pl.ALPHA) + stats.norm.ppf(0.80))
    for name in ("momentum_12_1", "mean_reversion"):
        r = results[f"rw|{name}"]
        # ส่วนเกินความแม่น (เหนือ "ทายขึ้นเสมอ") ขั้นต่ำที่เกณฑ์จะจับได้ 80% ภายใน 36 cohort = z × SD ของค่าเฉลี่ยใน 36 cohort (ต้องเกินศูนย์ ไม่ใช่เกินค่าเฉลี่ยของคนไม่มีฝีมือ)
        r["mde80_excess_acc_pp"] = float(z * r["sd_excess_acc_pp"])
        # ทายสุ่มไม่มีฝีมือได้ส่วนเกินติดลบเสมอ (ตลาดขึ้นเกินครึ่ง ⇒ "ขึ้นเสมอ" ถูกบ่อยกว่าการทายสุ่ม) — จุดกึ่งกลางของโลกไร้ฝีมือจึงไม่ใช่ศูนย์
        r["null_offset_pp"] = r["mean_excess_acc_pp"]
    null_rate = max(results["rw|momentum_12_1"]["pass_rate_pct"], results["rw|mean_reversion"]["pass_rate_pct"])
    return {
        "created_at": _now_iso(), "paths": paths, "cohorts": N_COHORTS, "seed": seed, "horizon": "1m",
        "gate": {"alpha": pl.ALPHA, "min_excess_acc_pp": pl.MIN_EXCESS_ACC_PP}, "mom_edge_pct_month": MOM_EDGE_PCT_MONTH,
        "data": {"last_bar": last_day, "n_days": n_obs, "tickers": list(pl.TICKERS), "mu_annual_pct": [float(x * 252 * 100) for x in mu]},
        "results": results,
        "null_pass_rate_pct": float(null_rate),
        "null_pass_rate_ok": bool(null_rate <= 2 * pl.ALPHA * 100.0 + 0.5),
        "limitations": list(LIMITATIONS),
    }


def save(result: dict[str, Any], directory: Path | None = None) -> Path:
    d = Path(directory or sim_data.DATA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (FILE + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    os.replace(tmp, d / FILE)
    return d / FILE


def load(directory: Path | None = None) -> dict[str, Any] | None:
    p = Path(directory or sim_data.DATA_DIR) / FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def age_days(result: dict[str, Any] | None, now: datetime | None = None) -> float | None:
    if not result or not result.get("created_at"):
        return None
    try:
        t = datetime.fromisoformat(result["created_at"])
    except ValueError:
        return None
    return ((now or datetime.now(timezone.utc)) - t).total_seconds() / 86400.0
