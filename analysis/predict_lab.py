# -*- coding: utf-8 -*-
"""โหมด PREDICT ("ทำนายตลาด") — สมุดคำทำนาย + ให้คะแนนย้อนหลังด้วยผลจริง แยกจากแผน DCA ทุกแผน.

ที่มา/กติกา: ``research/predict_lab/PREREG.md`` (ล็อก SHA-256 ใน ``LOCK.sha256``) · **ไม่มี backtest**: ตัวทำนายจากราคาพอดีกับอดีตได้เสมอ
และ 5 กองนี้เป็นกองที่รอดมาถึงวันนี้ ⇒ หลักฐานมาจากคำทำนายที่บันทึกไว้ก่อนผลเกิดเท่านั้น
คำถามเดียว: ตัวทำนายไหนทายทิศทางได้ **ดีกว่าทายว่า "ขึ้นเสมอ"** (เส้นฐาน) · ก่อนครบเกณฑ์ตอบได้อย่างเดียวว่า "ยังตอบไม่ได้"

ทุกค่า (กอง ตัวทำนาย ช่วงเวลา เกณฑ์) ตายตัวในโค้ด ไม่อ่านจาก config.json · ตัวเลขทุกตัวคำนวณในโค้ด (ไม่มี LLM) ·
ข้อมูลไม่พอ/ตัวทำนายรันไม่ได้ = ไม่บันทึกคำทำนายนั้นพร้อมเหตุผล (ไม่เดา ไม่ให้ค่ากลาง) · ดึงราคาทีละกอง (ห้าม ``yf.download``)
ผลของโมดูลนี้ **ห้ามไหลเข้าเลขคะแนน/จัดสรร** ของแผนใด ๆ — โมดูลนี้ import จากส่วนอื่นเฉพาะตัวดึงราคาและสูตรคะแนนแบบอ่านอย่างเดียว
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from analysis import dar_dca
from data.fetcher import PriceDataUnavailableError

REPO_ROOT = Path(__file__).resolve().parent.parent
PREREG_PATH = REPO_ROOT / "research" / "predict_lab" / "PREREG.md"
LOCK_PATH = REPO_ROOT / "research" / "predict_lab" / "LOCK.sha256"

# --- ค่าคงที่ที่ล็อก (PREREG.md ข้อ 1–6) ---
TICKERS = ("VOO", "SCHD", "QQQM", "XLV", "GLDM")
HORIZON_DAYS = {"1m": 30, "6m": 182, "1y": 365}
HORIZON_MONTHS = {"1m": 1, "6m": 6, "1y": 12}              # ระยะห่างของ cohort ที่ไม่ซ้อนทับ
PREDICTORS = ("momentum_12_1", "mean_reversion", "scorecard", "prophet")
MOM_LOOKBACK, MOM_SKIP, MOM_MIN_BARS = 252, 21, 253
MR_WINDOW, MR_MIN_BARS = 756, 700
SCORECARD_UP_SIGNALS = ("Strong Buy", "Buy")
PROPHET_FIT_BARS = 504
FETCH_YEARS = 5
MAX_STALE_DAYS = 10
RESOLVE_TOLERANCE_DAYS = 5
N_MIN = {"1m": 36, "6m": 12, "1y": 12}                      # cohort ที่ไม่ซ้อนทับและครบกำหนด ขั้นต่ำก่อนตัดสิน
MIN_EXCESS_ACC_PP = 5.0
N_TESTS = len(PREDICTORS) * len(HORIZON_DAYS)
ALPHA = 0.05 / N_TESTS                                       # Bonferroni — ทดสอบ 12 ชุดพร้อมกัน

EVIDENCE_CAVEATS = (
    "ไม่มีหลักฐานย้อนหลังเลย — ตัวทำนายจากราคาพอดีกับอดีตได้เสมอ จึงไม่ backtest · หลักฐานมาจากคำทำนายที่บันทึกไว้ก่อนผลเกิดเท่านั้น",
    "ตลาดส่วนใหญ่ขึ้นเกินครึ่ง จึงต้องชนะ \"ทายว่าขึ้นเสมอ\" ไม่ใช่ชนะ 50% — นั่นคือเส้นฐานที่ใช้ให้คะแนน",
    "ผู้พัฒนาทำนายไว้ล่วงหน้าว่า ~90% ไม่มีตัวทำนายชุดไหนผ่านเกณฑ์ (ล็อกใน PREREG.md ข้อ 7)",
    "ทดสอบ 12 ชุดพร้อมกัน (4 ตัว × 3 ช่วง) จึงใช้เกณฑ์ p < 0.0042 · ผ่านชุดเดียวต้องอ่านเป็นผลของการทดสอบหลายชุด ไม่ใช่การค้นพบ",
    "ช่วง 6 เดือนใช้เวลา ≈ 6 ปี และช่วง 1 ปี ≈ 12 ปีกว่าจะตัดสินได้ (ต้องมี cohort ที่ไม่ซ้อนทับครบ 12) · 5 กองสหสัมพันธ์สูง",
    "พอร์ตกระดาษ ไม่มีภาษีปันผล/FX/ค่าธรรมเนียม · คำทำนาย ≠ คำแนะนำลงทุน · ผลห้ามไหลกลับไปปรับสูตร DCA",
)


class PredictUnavailableError(RuntimeError):
    """ทำนายไม่ได้เพราะข้อมูล — ห้ามเดาแทน."""


# ----------------------------------------------------------------------------- ข้อมูล
def fetch_prices(
    tickers: tuple[str, ...] = TICKERS,
    *,
    fetch: Callable[[list[str], int], pd.DataFrame] = dar_dca.fetch_total_return_history,
) -> tuple[pd.DataFrame, dict[str, str]]:
    cols: dict[str, pd.Series] = {}
    failed: dict[str, str] = {}
    for t in tickers:
        try:
            cols[t] = fetch([t], FETCH_YEARS)[t]
        except (PriceDataUnavailableError, KeyError) as exc:
            failed[t] = str(exc)
    if not cols:
        raise PredictUnavailableError("ดึงราคาไม่ได้เลย — " + "; ".join(f"{k}: {v}" for k, v in failed.items()))
    return pd.DataFrame(cols).sort_index(), failed


# ----------------------------------------------------------------------------- ตัวทำนาย
def _clean(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return s[s > 0]


def predict_momentum(series: pd.Series) -> tuple[int | None, float | None, str | None]:
    s = _clean(series)
    if len(s) < MOM_MIN_BARS:
        return None, None, f"ประวัติไม่พอ ({len(s)} แท่ง ต้อง ≥ {MOM_MIN_BARS})"
    r = float(s.iloc[-1 - MOM_SKIP] / s.iloc[-1 - MOM_LOOKBACK] - 1.0)
    if not math.isfinite(r):
        return None, None, "คำนวณไม่ได้"
    return (1 if r > 0 else -1), r * 100.0, None


def predict_mean_reversion(series: pd.Series) -> tuple[int | None, float | None, str | None]:
    s = _clean(series).iloc[-MR_WINDOW:]
    if len(s) < MR_MIN_BARS:
        return None, None, f"ประวัติไม่พอ ({len(s)} แท่ง ต้อง ≥ {MR_MIN_BARS})"
    lg = np.log(s.to_numpy(dtype=float))
    sd = float(lg.std(ddof=1))
    if not math.isfinite(sd) or sd <= 0:
        return None, None, "ส่วนเบี่ยงเบนเป็นศูนย์/คำนวณไม่ได้"
    z = float((lg[-1] - lg.mean()) / sd)
    return (1 if z < 0 else -1), z, None


def predict_scorecard(ticker: str, series: pd.Series) -> tuple[int | None, float | None, str | None]:
    from analysis.financial_model import score_from_prices  # noqa: PLC0415

    s = _clean(series)
    try:
        res = score_from_prices(ticker, s)
    except ValueError as exc:
        return None, None, str(exc)
    return (1 if res["signal"] in SCORECARD_UP_SIGNALS else -1), float(res["total_pct"]), None


def default_prophet(series: pd.Series) -> pd.Series:
    """yhat รายวันทำการหลังวันสุดท้าย (ตั้งค่าเดียวกับ ``PriceForecaster.build_model``) — ฟิตบนราคาปรับปันผล ``PROPHET_FIT_BARS`` แท่งล่าสุด."""
    import logging

    from analysis.forecaster import PriceForecaster  # noqa: PLC0415

    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
    logging.getLogger("prophet").setLevel(logging.WARNING)
    s = _clean(series).iloc[-PROPHET_FIT_BARS:]
    df = pd.DataFrame({"ds": pd.DatetimeIndex(s.index).tz_localize(None) if s.index.tz is not None else s.index, "y": s.to_numpy(dtype=float)})
    model = PriceForecaster().build_model()
    model.fit(df)
    horizon_bdays = int(max(HORIZON_DAYS.values()) * 5 / 7) + 15
    out = model.predict(model.make_future_dataframe(periods=horizon_bdays, freq="B")).set_index("ds")["yhat"]
    return out[out.index > df["ds"].max()]


@dataclass
class Prediction:
    ticker: str
    predictor: str
    horizon: str
    direction: int
    score: float
    price_usd: float


def make_predictions(
    prices: pd.DataFrame,
    asof: pd.Timestamp | None = None,
    *,
    prophet_fn: Callable[[pd.Series], pd.Series] = default_prophet,
    failed: dict[str, str] | None = None,
) -> tuple[list[Prediction], dict[str, str], str]:
    """คำทำนายทั้งชุด (ตัวทำนาย × กอง × ช่วง) จากราคา ณ ``asof`` · คืน (คำทำนาย, ตัวที่ไม่ได้ทำนายพร้อมเหตุผล, วันที่ของแท่งล่าสุด).

    ใช้ข้อมูลถึง ``asof`` เท่านั้น (ตัดแท่งหลังจากนั้นทิ้งก่อนคำนวณ) — ตัวที่ราคาเก่ากว่า ``MAX_STALE_DAYS`` ถูกตัดทั้งกอง
    """
    if prices.empty:
        raise PredictUnavailableError("ไม่มีราคาเลย")
    cut = prices if asof is None else prices.loc[:asof]
    latest = pd.Timestamp(cut.dropna(how="all").index.max())
    preds: list[Prediction] = []
    skipped: dict[str, str] = {f"*:{t}": f"ดึงราคาไม่ได้: {why}" for t, why in (failed or {}).items()}
    for t in TICKERS:
        if f"*:{t}" in skipped:
            continue
        if t not in cut.columns or cut[t].dropna().empty:
            skipped[f"*:{t}"] = "ไม่มีข้อมูลราคา"
            continue
        s = _clean(cut[t])
        if (latest - pd.Timestamp(s.index.max())).days > MAX_STALE_DAYS:
            skipped[f"*:{t}"] = f"ราคาล่าสุดเก่า ({s.index.max():%Y-%m-%d})"
            continue
        px = float(s.iloc[-1])
        single = {
            "momentum_12_1": predict_momentum(s),
            "mean_reversion": predict_mean_reversion(s),
            "scorecard": predict_scorecard(t, s),
        }
        for name, (direction, score, why) in single.items():
            if direction is None:
                skipped[f"{name}:{t}"] = why or "คำนวณไม่ได้"
                continue
            for h in HORIZON_DAYS:
                preds.append(Prediction(t, name, h, direction, float(score), px))
        try:
            yhat = prophet_fn(s)
            for h, days in HORIZON_DAYS.items():
                target = pd.Timestamp(s.index.max()) + pd.Timedelta(days=days)
                at = yhat[yhat.index >= target]
                if at.empty:
                    raise ValueError(f"พยากรณ์ไม่ครอบคลุมช่วง {h}")
                y = float(at.iloc[0])
                if not math.isfinite(y):
                    raise ValueError("yhat ไม่ใช่จำนวนจำกัด")
                preds.append(Prediction(t, "prophet", h, 1 if y > px else -1, (y / px - 1.0) * 100.0, px))
        except Exception as exc:  # noqa: BLE001 - Prophet/Stan ล้มได้หลายแบบ: ตัดตัวนี้พร้อมเหตุผล ไม่ลากทั้งชุด
            skipped[f"prophet:{t}"] = f"รัน Prophet ไม่ได้: {exc}"
    if not preds:
        raise PredictUnavailableError(f"ไม่มีคำทำนายเลย — {skipped}")
    return preds, skipped, f"{latest:%Y-%m-%d}"


# ----------------------------------------------------------------------------- ให้คะแนน
def resolve(log: pd.DataFrame, prices: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """เติมผลจริงให้คำทำนายที่ครบกำหนด · สถานะ ``pending`` / ``resolved`` / ``unpriceable`` (ครบกำหนดแล้วแต่หาราคาไม่ได้ — ไม่เดา)."""
    out = log.copy()
    out["due"] = pd.to_datetime(out["date"]) + pd.to_timedelta(out["horizon"].map(HORIZON_DAYS), unit="D")
    status, ret, up, correct = [], [], [], []
    for r in out.itertuples():
        if pd.Timestamp(today) < r.due:
            status.append("pending"); ret.append(np.nan); up.append(np.nan); correct.append(np.nan)
            continue
        col = _clean(prices[r.ticker]) if r.ticker in prices.columns else pd.Series(dtype=float)
        ref = col.loc[:r.due]
        entry = col.loc[:pd.Timestamp(r.date)]
        ok = (not ref.empty and not entry.empty and (r.due - ref.index.max()).days <= RESOLVE_TOLERANCE_DAYS
              and (pd.Timestamp(r.date) - entry.index.max()).days <= RESOLVE_TOLERANCE_DAYS and col.index.max() >= ref.index.max())
        if not ok:
            status.append("unpriceable"); ret.append(np.nan); up.append(np.nan); correct.append(np.nan)
            continue
        x = float(ref.iloc[-1] / entry.iloc[-1] - 1.0)
        status.append("resolved"); ret.append(x * 100.0); up.append(1.0 if x > 0 else 0.0)
        correct.append(1.0 if (x > 0) == (r.direction > 0) else 0.0)
    out["status"], out["ret_pct"], out["went_up"], out["correct"] = status, ret, up, correct
    return out


def _cohort_rows(g: pd.DataFrame) -> dict[str, float]:
    acc = float(g["correct"].mean())
    base = float(g["went_up"].mean())
    picked = g[g["direction"] > 0]
    basket = float(picked["ret_pct"].mean()) if len(picked) else 0.0     # ไม่มีกองที่ทายขึ้น = เงินสด ผลตอบแทน 0
    shadow = float(g["ret_pct"].mean())
    return {"accuracy": acc * 100.0, "baseline": base * 100.0, "excess_acc_pp": (acc - base) * 100.0,
            "basket_ret_pct": basket, "shadow_ret_pct": shadow, "excess_basket_pct": basket - shadow, "n_tickers": float(len(g))}


def _independent(cohorts: pd.DataFrame, step: int) -> pd.DataFrame:
    if cohorts.empty:
        return cohorts
    periods = pd.PeriodIndex(cohorts["plan_month"], freq="M")
    idx = np.array([(p - periods.min()).n for p in periods])
    return cohorts[idx % step == 0]


def official_rows(df: pd.DataFrame) -> pd.DataFrame:
    """แถวที่ใช้ตัดสิน = ชุดของ "วันแรกที่บันทึกในเดือนนั้น" เท่านั้น (PREREG ข้อ 5) — ชุดวันอื่นซ้อนทับกันเกือบทั้งหมด."""
    if df.empty:
        return df
    d = pd.to_datetime(df["date"])
    first = d.groupby(df["plan_month"]).transform("min")
    return df[d == first]


def descriptive(resolved: pd.DataFrame) -> pd.DataFrame:
    """ความแม่นรวมของ **ทุกวัน** ที่ครบกำหนดแล้ว (พรรณนาเท่านั้น — ซ้อนทับกัน ห้ามใช้ตัดสิน/ห้ามคิด p-value)."""
    done = resolved[resolved["status"] == "resolved"] if not resolved.empty else resolved
    if done.empty:
        return pd.DataFrame(columns=["predictor", "horizon", "n_rows", "accuracy", "baseline", "excess_pp"])
    g = done.groupby(["predictor", "horizon"]).agg(n_rows=("correct", "size"), accuracy=("correct", "mean"), baseline=("went_up", "mean")).reset_index()
    g["accuracy"] *= 100.0
    g["baseline"] *= 100.0
    g["excess_pp"] = g["accuracy"] - g["baseline"]
    return g


def score_all(resolved: pd.DataFrame) -> dict[str, Any]:
    """คะแนนต่อ (ตัวทำนาย × ช่วง) + ตัดสินตามเกณฑ์ที่ล็อก (ใช้เฉพาะชุดวันแรกของแต่ละเดือน)."""
    from scipy import stats  # noqa: PLC0415

    resolved = official_rows(resolved)
    groups: dict[str, Any] = {}
    for predictor in PREDICTORS:
        for h in HORIZON_DAYS:
            key = f"{predictor}|{h}"
            sub = resolved[(resolved["predictor"] == predictor) & (resolved["horizon"] == h)] if not resolved.empty else resolved
            info: dict[str, Any] = {"predictor": predictor, "horizon": h, "pending": 0, "unpriceable": 0, "n_cohorts": 0, "n_independent": 0,
                                    "mean_excess_acc_pp": None, "mean_excess_basket_pct": None, "p_value": None, "mean_accuracy": None,
                                    "mean_baseline": None, "status": "ยังไม่มีข้อมูล", "reasons": []}
            if sub.empty:
                groups[key] = info
                continue
            info["pending"] = int((sub["status"] == "pending").sum())
            info["unpriceable"] = int((sub["status"] == "unpriceable").sum())
            done = sub[sub["status"] == "resolved"]
            if info["unpriceable"]:
                info["status"] = "ปฏิเสธ"
                info["reasons"] = [f"{info['unpriceable']} คำทำนายครบกำหนดแล้วแต่หาราคาไม่ได้ — ห้ามตัดสิน (หาไม่ได้ ≠ ทายผิด ≠ ทายถูก)"]
                groups[key] = info
                continue
            rows = [{"plan_month": m, **_cohort_rows(g)} for m, g in done.groupby("plan_month")]
            coh = pd.DataFrame(rows).sort_values("plan_month").reset_index(drop=True) if rows else pd.DataFrame()
            info["n_cohorts"] = len(coh)
            info["cohorts"] = coh
            if coh.empty:
                info["status"] = "ยังตอบไม่ได้"
                info["reasons"] = ["ยังไม่มีคำทำนายที่ครบกำหนด"]
                groups[key] = info
                continue
            ind = _independent(coh, HORIZON_MONTHS[h])
            info["n_independent"] = len(ind)
            info["mean_accuracy"] = float(ind["accuracy"].mean())
            info["mean_baseline"] = float(ind["baseline"].mean())
            info["mean_excess_acc_pp"] = float(ind["excess_acc_pp"].mean())
            info["mean_excess_basket_pct"] = float(ind["excess_basket_pct"].mean())
            if len(ind) >= 2 and float(ind["excess_acc_pp"].std(ddof=1)) > 0:
                t = info["mean_excess_acc_pp"] / (float(ind["excess_acc_pp"].std(ddof=1)) / math.sqrt(len(ind)))
                info["p_value"] = float(stats.t.sf(t, df=len(ind) - 1))
            if len(ind) < N_MIN[h]:
                info["status"] = "ยังตอบไม่ได้"
                info["reasons"] = [f"มี cohort ที่ไม่ซ้อนทับและครบกำหนด {len(ind)} จาก {N_MIN[h]} ที่เกณฑ์ล็อกไว้ — ตัวเลขแสดงได้ แต่ห้ามอ่านว่าชนะหรือแพ้"]
            else:
                checks = {
                    f"ส่วนเกินความแม่น ≥ +{MIN_EXCESS_ACC_PP:.0f} จุด": info["mean_excess_acc_pp"] >= MIN_EXCESS_ACC_PP,
                    f"p < {ALPHA:.4f} (ทดสอบหลายชุด)": info["p_value"] is not None and info["p_value"] < ALPHA,
                    "ส่วนเกินตะกร้า > 0": info["mean_excess_basket_pct"] > 0,
                }
                info["status"] = "ผ่าน" if all(checks.values()) else "ไม่ผ่าน"
                info["reasons"] = [f"{'✓' if ok else '✗'} {k}" for k, ok in checks.items()]
            groups[key] = info
    return {"groups": groups, "n_pass": sum(1 for g in groups.values() if g["status"] == "ผ่าน")}


def lock_status() -> dict[str, Any]:
    try:
        locked = LOCK_PATH.read_text(encoding="utf-8").split()[0]
        actual = hashlib.sha256(PREREG_PATH.read_bytes()).hexdigest()
    except OSError as exc:
        return {"ok": False, "reason": f"อ่านไฟล์ลงทะเบียน/ล็อกไม่ได้: {exc}"}
    return {"ok": locked == actual, "reason": None if locked == actual else "PREREG.md ไม่ตรงกับ hash ที่ล็อก — ห้ามอ่านผลเป็นหลักฐาน"}
