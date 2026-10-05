# -*- coding: utf-8 -*-
"""จำลองเกณฑ์ของโหมด TRADE (หุ้น 30 ตัว รายวัน): เกณฑ์ความแม่น 1d (PREREG ข้อ 5) แยกโชคจากเอดจ์ได้ดีแค่ไหน และค่าธรรมเนียมที่การซื้อขายตามสัญญาณจะเสีย.

**ไม่ใช่หลักฐานว่าตัวทำนายใดได้ผล** — โลกจำลองให้ผลตามที่สมมติไว้ในโลกนั้น · วัดได้จริงสามอย่าง:
* โลก ``rw`` (ไม่มีใครทายได้ · ผลตอบแทนรายวันปกติหลายตัวแปรจากค่าเฉลี่ย/ความแปรปรวนร่วมของราคาจริง 30 ตัว): อัตราผ่านลวง · ส่วนเกินเฉลี่ยของคนไร้ฝีมือ · ส่วนเกินขั้นต่ำที่เกณฑ์จับได้ 80%
* โลก ``mom`` (สมมติเอดจ์โมเมนตัม +``MOM_EDGE_PCT_MONTH``%/เดือน): กำลังของเกณฑ์
* **ค่าธรรมเนียม**: สัญญาณพลิกบ่อยแค่ไหน (รายการ/ช่อง/ปี) × 0.15% ต่อรายการ = ต้นทุนต่อปีที่บัญชีกระดาษต้องชนะให้ได้ก่อนจะมีกำไรสุทธิ
จำลองเฉพาะ momentum_12_1 / mean_reversion / reversal_5d ช่วง 1d — Prophet, scorecard และ majority ไม่ถูกจำลอง (ช้าเกินไป/ขึ้นกับตัวอื่น) ·
ผลไม่ไหลเข้าเกณฑ์ตัดสินหรือตัวทำนาย · ไม่มี LLM ไม่ยิงเน็ต
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
from analysis import trade_lab as tl
from simulation import data as sim_data

FILE = "trade_sim.json"
DEFAULT_PATHS = 600
CHUNK = 100
N_DAYS = tl.N_MIN["1d"]                     # 252 วันที่บันทึก (cohort รายวัน)
WARMUP = pl.MR_WINDOW                       # ดัชนีวันแรกที่ทุกตัวทำนายมีประวัติครบ (756 แท่ง)
MOM_EDGE_PCT_MONTH = 0.5
SEED = 20261005
MAX_AGE_DAYS = 7.0
SIM_PREDICTORS = ("momentum_12_1", "mean_reversion", "reversal_5d")
LIMITATIONS = (
    "ผลคือสิ่งที่สมมติไว้ในโลกจำลอง — ไม่ใช่หลักฐานว่าตัวทำนายใดได้ผลในตลาดจริง · โลก rw = เดินสุ่มปกติหลายตัวแปรจากราคาจริง 30 ตัว (ไม่มีหางหนัก/regime/gap เปิดตลาด)",
    "จำลองเฉพาะ momentum_12_1, mean_reversion, reversal_5d ช่วง 1d — Prophet, scorecard และ majority ไม่ถูกจำลอง",
    "ค่าธรรมเนียมประมาณจากจำนวนครั้งที่สัญญาณพลิก × 0.15% ต่อรายการต่อช่อง — ไม่รวมภาษีปันผล/FX/slippage ที่จะทำให้ผลจริงแย่กว่านี้",
    "โลก mom สมมติเอดจ์ +0.5%/เดือนต่อหุ้น (สมมติ ไม่ได้วัด) ใช้ดูกำลังของเกณฑ์เท่านั้น · ผลห้ามไหลเข้าเกณฑ์ตัดสินของ PREREG",
)


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="seconds")


def _moments(close: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, str, int]:
    cols = [t for t in tl.TICKERS if t in close.columns]
    if len(cols) < len(tl.TICKERS):
        raise ValueError(f"จำลองต้องมีราคาครบ 30 ตัว (ขาด {sorted(set(tl.TICKERS) - set(cols))})")
    lr = np.log(close[list(tl.TICKERS)].dropna()).diff().dropna()
    if len(lr) < 500:
        raise ValueError(f"ราคาร่วมกันของ 30 ตัวมี {len(lr)} วัน (ต้อง ≥ 500) — ประมาณความแปรปรวนร่วมไม่ได้")
    return lr.mean().to_numpy(), lr.cov().to_numpy(), f"{lr.index.max():%Y-%m-%d}", len(lr)


def _signals(logp: np.ndarray, days: np.ndarray) -> dict[str, np.ndarray]:
    """ทิศ (ขึ้น = True) ต่อวันที่บันทึก (paths, days, assets)."""
    mom = (logp[:, days - pl.MOM_SKIP, :] - logp[:, days - pl.MOM_LOOKBACK, :]) > 0
    rev = (logp[:, days, :] - logp[:, days - tl.REVERSAL_BARS, :]) < 0
    mr = np.empty_like(mom)
    for k, d in enumerate(days):
        w = logp[:, d - pl.MR_WINDOW + 1: d + 1, :]
        mr[:, k, :] = (w[:, -1, :] - w.mean(axis=1)) / w.std(axis=1, ddof=1) < 0
    return {"momentum_12_1": mom, "mean_reversion": mr, "reversal_5d": rev}


def _gate(excess: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    from scipy import stats  # noqa: PLC0415

    n = excess.shape[1]
    m = excess.mean(axis=1)
    sd = excess.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(sd > 0, m / (sd / math.sqrt(n)), 0.0)
    p = stats.t.sf(t, df=n - 1)
    return (m >= tl.MIN_EXCESS_ACC_PP) & (p < tl.ALPHA), m


def simulate_gate(close: pd.DataFrame, *, paths: int = DEFAULT_PATHS, seed: int = SEED) -> dict[str, Any]:
    from scipy import stats  # noqa: PLC0415

    mu, cov, last_day, n_obs = _moments(close)
    k_assets = len(mu)
    chol = np.linalg.cholesky(cov + 1e-12 * np.eye(k_assets))
    T = WARMUP + N_DAYS + 1
    days = WARMUP + np.arange(N_DAYS)                      # วันที่บันทึก d (ดัชนีของ logp) · ผลจริง = logp[d+1] − logp[d]
    rng = np.random.default_rng(seed)
    edge = MOM_EDGE_PCT_MONTH / 100.0 / 21.0
    acc: dict[str, dict[str, list[np.ndarray]]] = {w: {} for w in ("rw", "mom")}
    for start in range(0, paths, CHUNK):
        n = min(CHUNK, paths - start)
        for world in ("rw", "mom"):
            r = mu + rng.standard_normal((n, T, k_assets)) @ chol.T
            logp = np.zeros((n, T + 1, k_assets))
            if world == "rw":
                logp[:, 1:, :] = np.cumsum(r, axis=1)
            else:
                logp[:, 1:WARMUP + 1, :] = np.cumsum(r[:, :WARMUP, :], axis=1)
                for d in range(WARMUP, WARMUP + N_DAYS + 1):
                    sig = np.where(logp[:, d - pl.MOM_SKIP, :] - logp[:, d - pl.MOM_LOOKBACK, :] > 0, 1.0, -1.0)
                    logp[:, d + 1, :] = logp[:, d, :] + r[:, d, :] + edge * sig
            went_up = (logp[:, days + 1, :] - logp[:, days, :]) > 0
            base = went_up.mean(axis=2)
            for name, up in _signals(logp, days).items():
                acc[world].setdefault(f"{name}.excess", []).append((((up == went_up).mean(axis=2)) - base) * 100.0)
                acc[world].setdefault(f"{name}.flips", []).append((up[:, 1:, :] != up[:, :-1, :]).sum(axis=1).mean(axis=1))
    z = float(stats.norm.isf(tl.ALPHA) + stats.norm.ppf(0.80))
    results: dict[str, Any] = {}
    for world, d in acc.items():
        for name in SIM_PREDICTORS:
            ex = np.concatenate(d[f"{name}.excess"], axis=0)
            fl = np.concatenate(d[f"{name}.flips"], axis=0)
            ok, m = _gate(ex)
            trades_year = float(fl.mean() * 252.0 / (N_DAYS - 1))
            sd = float(m.std(ddof=1))
            results[f"{world}|{name}"] = {
                "world": world, "predictor": name, "pass_rate_pct": float(ok.mean() * 100.0), "mean_excess_acc_pp": float(m.mean()),
                "sd_excess_acc_pp": sd, "p05_excess_acc_pp": float(np.percentile(m, 5)), "p95_excess_acc_pp": float(np.percentile(m, 95)),
                "mde80_excess_acc_pp": float(z * sd), "trades_per_slot_year": trades_year,
                "fee_drag_pct_year": float(trades_year * tl.FEE * 100.0),
            }
    null_rate = max(results[f"rw|{n}"]["pass_rate_pct"] for n in SIM_PREDICTORS)
    return {
        "created_at": _now_iso(), "paths": paths, "days": N_DAYS, "seed": seed, "horizon": "1d",
        "gate": {"alpha": tl.ALPHA, "min_excess_acc_pp": tl.MIN_EXCESS_ACC_PP, "fee": tl.FEE}, "mom_edge_pct_month": MOM_EDGE_PCT_MONTH,
        "data": {"last_bar": last_day, "n_days": n_obs, "tickers": list(tl.TICKERS)},
        "results": results, "null_pass_rate_pct": float(null_rate),
        "null_pass_rate_ok": bool(null_rate <= 2 * tl.ALPHA * 100.0 + 0.5), "limitations": list(LIMITATIONS),
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
