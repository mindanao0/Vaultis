# -*- coding: utf-8 -*-
"""โหมด TRADE (PREDICT-STOCK รายวัน) — ทำนายหุ้นรายตัว 30 ตัวทุกวัน + บัญชีกระดาษซื้อขายตามคำทำนาย · **แยกจากโหมด PREDICT (5 กอง) ทุกอย่าง**.

กติกา: ``research/trade_lab/PREREG.md`` (ล็อก SHA-256 ใน ``LOCK.sha256``) · ไม่มี backtest (ข้อมูลฟรีไม่มีหุ้นที่ตายแล้ว + รายชื่อเป็นผู้รอด) ·
หลักฐานมาจากคำทำนายที่บันทึกก่อนผลเกิดเท่านั้น · ก่อนครบเกณฑ์ตอบได้อย่างเดียวว่า "ยังตอบไม่ได้"
สองคำถามแยกกัน: (1) ทายทิศทางได้ดีกว่า "ขึ้นเสมอ" ไหม (ความแม่นรายวัน/สัปดาห์/เดือน) (2) ซื้อขายตามคำทำนายแล้ว **หลังหักค่าธรรมเนียม 0.15% ทั้งสองขา** ชนะถือเฉย ๆ ไหม
ทุกค่าตายตัวในโค้ด ไม่อ่านจาก config.json · ตัวเลขทุกตัวคำนวณในโค้ด (ไม่มี LLM) · ข้อมูลขาด = ตัดทิ้งพร้อมเหตุผล/ปฏิเสธตัดสิน ไม่เดา · ดึงราคาทีละตัว (ห้าม ``yf.download``)
ใช้ฟังก์ชันตัวทำนายบริสุทธิ์ของ ``analysis/predict_lab.py`` ซ้ำ (นิยามตัวเดียว ไม่เขียนสองชุด) — ผลลัพธ์/สมุด/สถานะของสองโหมดแยกกันสิ้นเชิง
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from analysis import predict_lab as pl
from data.fetcher import PriceDataUnavailableError
from portfolio.fees import DIME_FEE_RATE

REPO_ROOT = Path(__file__).resolve().parent.parent
PREREG_PATH = REPO_ROOT / "research" / "trade_lab" / "PREREG.md"
LOCK_PATH = REPO_ROOT / "research" / "trade_lab" / "LOCK.sha256"

# --- ค่าคงที่ที่ล็อก (PREREG.md ข้อ 1–7) ---
UNIVERSE: tuple[tuple[str, str], ...] = (
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "Nvidia"), ("GOOGL", "Alphabet"), ("AMZN", "Amazon"),
    ("META", "Meta"), ("AVGO", "Broadcom"), ("TSLA", "Tesla"), ("BRK-B", "Berkshire Hathaway"), ("JPM", "JPMorgan"),
    ("V", "Visa"), ("MA", "Mastercard"), ("UNH", "UnitedHealth"), ("LLY", "Eli Lilly"), ("JNJ", "Johnson & Johnson"),
    ("PG", "Procter & Gamble"), ("KO", "Coca-Cola"), ("PEP", "PepsiCo"), ("WMT", "Walmart"), ("COST", "Costco"),
    ("HD", "Home Depot"), ("MRK", "Merck"), ("ABBV", "AbbVie"), ("XOM", "Exxon Mobil"), ("CVX", "Chevron"),
    ("MCD", "McDonald's"), ("TXN", "Texas Instruments"), ("CSCO", "Cisco"), ("ORCL", "Oracle"), ("ADBE", "Adobe"),
)
TICKERS = tuple(t for t, _ in UNIVERSE)
NAME = dict(UNIVERSE)
BENCHMARK = "VOO"
BASE_PREDICTORS = ("momentum_12_1", "mean_reversion", "scorecard", "prophet", "reversal_5d")
PREDICTORS = (*BASE_PREDICTORS, "majority")
HORIZON_BARS = {"1d": 1, "1w": 5, "1m": 21}
N_MIN = {"1d": 252, "1w": 52, "1m": 12}
MIN_EXCESS_ACC_PP = 2.0
N_TESTS = len(PREDICTORS) * len(HORIZON_BARS)
ALPHA = 0.05 / N_TESTS
REVERSAL_BARS = 5
MIN_VOTERS = 3
FEE = DIME_FEE_RATE
SLOT_USD = 1000.0
ACCOUNT_HORIZON = "1d"
MIN_WEEKS = 52
N_ACCOUNTS = len(PREDICTORS)
ALPHA_ACCOUNT = 0.05 / N_ACCOUNTS
WEEK_BARS = 5
FETCH_YEARS = 5
FETCH_ATTEMPTS = 3
MAX_STALE_DAYS = pl.MAX_STALE_DAYS

EVIDENCE_CAVEATS = (
    "ไม่มีหลักฐานย้อนหลังเลย — ข้อมูลฟรีไม่มีหุ้นที่ล้ม/ถูกซื้อ และ 30 ตัวนี้เป็นหุ้นใหญ่ที่รอดมาถึงวันนี้ จึงไม่ backtest · หลักฐานมาจากคำทำนายที่บันทึกก่อนผลเกิดเท่านั้น",
    "ตลาดขึ้นเกินครึ่ง จึงต้องชนะ \"ทายว่าขึ้นเสมอ\" ไม่ใช่ชนะ 50% · ความแม่นที่ผ่านเกณฑ์ ≠ ทำเงินได้ เพราะค่าธรรมเนียม Dime 0.15% ต่อรายการทั้งซื้อและขาย",
    "บัญชีกระดาษซื้อ/ขายที่ราคาเปิดวันถัดไป หักค่าธรรมเนียมแล้ว แต่ไม่มีภาษีปันผล 15% / FX spread / slippage — ผลจริงจะแย่กว่าในกระดาษเสมอ",
    "ผู้พัฒนาทำนายไว้ล่วงหน้าว่า ~85% ไม่มีบัญชีใดผ่านเกณฑ์ และ ~80% บัญชีที่ซื้อขายบ่อยที่สุดแพ้ถือเฉย ๆ (PREREG ข้อ 8)",
    "ทดสอบความแม่น 18 ชุด + 6 บัญชีพร้อมกัน ⇒ เกณฑ์เข้มขึ้น (p < 0.0028 / 0.0083) · ผ่านชุดเดียวต้องอ่านเป็นผลของการทดสอบหลายชุด ไม่ใช่การค้นพบ",
    "ป้าย \"ซื้อ/ขาย/ถือ\" คือสิ่งที่บัญชีกระดาษทำตามคำทำนาย ไม่ใช่คำแนะนำลงทุน · ถ้าซื้อขายจริงตามป้าย ผู้ใช้รับความเสี่ยงเองทั้งหมด — ระบบยังไม่มีหลักฐานว่าทำกำไรได้",
)


class TradeUnavailableError(RuntimeError):
    """ทำนาย/คำนวณบัญชีไม่ได้เพราะข้อมูล — ห้ามเดาแทน."""


# ----------------------------------------------------------------------------- ข้อมูล
def default_fetch(ticker: str, years: int) -> pd.DataFrame:
    """Open/Close ปรับปันผลรายวันของหุ้นหนึ่งตัว (``yf.Ticker(...).history`` ทีละตัว · ลอง 3 ครั้งแล้ว ``PriceDataUnavailableError``)."""
    import yfinance as yf

    end = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize() + pd.Timedelta(days=1)
    start = end - pd.DateOffset(years=int(years))
    last: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            h = yf.Ticker(ticker).history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), auto_adjust=True, actions=False)
            if h.empty or "Close" not in h or "Open" not in h:
                raise ValueError("ไม่มีราคาในช่วงที่ขอ")
            idx = pd.DatetimeIndex(h.index)
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            out = pd.DataFrame({"Open": pd.to_numeric(h["Open"], errors="coerce").to_numpy(), "Close": pd.to_numeric(h["Close"], errors="coerce").to_numpy()},
                               index=idx.normalize())
            out = out[out["Close"] > 0]
            if out.empty:
                raise ValueError("ไม่มีราคาปิดที่ใช้ได้")
            return out
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < FETCH_ATTEMPTS - 1:
                time.sleep(2.0)
    raise PriceDataUnavailableError(f"ดึงราคาของ {ticker} ไม่สำเร็จหลังลอง {FETCH_ATTEMPTS} ครั้ง: {last}")


def fetch_ohlc(
    tickers: tuple[str, ...] = TICKERS,
    *,
    fetch: Callable[[str, int], pd.DataFrame] = default_fetch,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    """(เปิด, ปิด, ตัวที่ดึงไม่ได้พร้อมเหตุผล) · VOO ถูกดึงด้วยเสมอและต้องดึงได้ (แขนเทียบ + ปฏิทินซื้อขายหลัก)."""
    opens: dict[str, pd.Series] = {}
    closes: dict[str, pd.Series] = {}
    failed: dict[str, str] = {}
    for t in dict.fromkeys((*tickers, BENCHMARK)):
        try:
            df = fetch(t, FETCH_YEARS)
            opens[t], closes[t] = df["Open"], df["Close"]
        except (PriceDataUnavailableError, KeyError) as exc:
            failed[t] = str(exc)
    if BENCHMARK not in closes:
        raise TradeUnavailableError(f"ดึงราคา {BENCHMARK} (แขนเทียบ/ปฏิทิน) ไม่ได้: {failed.get(BENCHMARK)}")
    if len(closes) <= 1:
        raise TradeUnavailableError("ดึงราคาหุ้นในจักรวาลไม่ได้เลย — " + "; ".join(f"{k}: {v}" for k, v in failed.items()))
    return pd.DataFrame(opens).sort_index(), pd.DataFrame(closes).sort_index(), failed


# ----------------------------------------------------------------------------- ทำนาย
@dataclass
class Prediction:
    ticker: str
    predictor: str
    horizon: str
    direction: int
    score: float
    price_usd: float


def predict_reversal(series: pd.Series) -> tuple[int | None, float | None, str | None]:
    s = pl._clean(series)  # noqa: SLF001 - นิยามทำความสะอาดชุดเดียวกับโหมด PREDICT
    if len(s) < REVERSAL_BARS + 1:
        return None, None, f"ประวัติไม่พอ ({len(s)} แท่ง)"
    r = float(s.iloc[-1] / s.iloc[-1 - REVERSAL_BARS] - 1.0) * 100.0
    return (1 if r < 0 else -1), r, None


def make_predictions(
    close: pd.DataFrame,
    asof: pd.Timestamp | None = None,
    *,
    prophet_fn: Callable[[pd.Series], pd.Series] = pl.default_prophet,
    failed: Mapping[str, str] | None = None,
) -> tuple[list[Prediction], dict[str, str], str]:
    """คำทำนายทั้งชุด (หุ้น × ตัวทำนาย × ช่วง) จากราคา ณ ``asof`` (ตัดแท่งหลังจากนั้นทิ้งก่อนคำนวณ) · คืน (คำทำนาย, ตัวที่ไม่ได้ทำนาย+เหตุผล, วันที่แท่งล่าสุด)."""
    if close.empty or BENCHMARK not in close.columns:
        raise TradeUnavailableError("ไม่มีราคาปิด (หรือไม่มี VOO)")
    cut = close if asof is None else close.loc[:asof]
    latest = pd.Timestamp(cut[BENCHMARK].dropna().index.max())
    skipped: dict[str, str] = {f"*:{t}": f"ดึงราคาไม่ได้: {why}" for t, why in (failed or {}).items() if t != BENCHMARK}
    preds: list[Prediction] = []
    for t in TICKERS:
        if f"*:{t}" in skipped:
            continue
        s = pl._clean(cut[t]) if t in cut.columns else pd.Series(dtype=float)  # noqa: SLF001
        if s.empty:
            skipped[f"*:{t}"] = "ไม่มีข้อมูลราคา"
            continue
        if (latest - pd.Timestamp(s.index.max())).days > MAX_STALE_DAYS:
            skipped[f"*:{t}"] = f"ราคาล่าสุดเก่า ({s.index.max():%Y-%m-%d}) — อาจเลิกกิจการ/ถูกซื้อ/ย้ายตัวย่อ"
            continue
        px = float(s.iloc[-1])
        votes: dict[str, dict[str, tuple[int, float]]] = {h: {} for h in HORIZON_BARS}
        single = {
            "momentum_12_1": pl.predict_momentum(s),
            "mean_reversion": pl.predict_mean_reversion(s),
            "scorecard": pl.predict_scorecard(t, s),
            "reversal_5d": predict_reversal(s),
        }
        for name, (direction, score, why) in single.items():
            if direction is None:
                skipped[f"{name}:{t}"] = why or "คำนวณไม่ได้"
                continue
            for h in HORIZON_BARS:
                preds.append(Prediction(t, name, h, direction, float(score), px))
                votes[h][name] = (direction, float(score))
        try:
            yhat = prophet_fn(s)
            for h, bars in HORIZON_BARS.items():
                if len(yhat) < bars:
                    raise ValueError(f"พยากรณ์ไม่ครอบคลุม {bars} แท่ง")
                y = float(yhat.iloc[bars - 1])
                if not math.isfinite(y):
                    raise ValueError("yhat ไม่ใช่จำนวนจำกัด")
                d = 1 if y > px else -1
                preds.append(Prediction(t, "prophet", h, d, (y / px - 1.0) * 100.0, px))
                votes[h]["prophet"] = (d, (y / px - 1.0) * 100.0)
        except Exception as exc:  # noqa: BLE001 - Prophet/Stan ล้มได้หลายแบบ: ตัดตัวนี้พร้อมเหตุผล ไม่ลากทั้งชุด
            skipped[f"prophet:{t}"] = f"รัน Prophet ไม่ได้: {exc}"
        for h in HORIZON_BARS:
            v = votes[h]
            if len(v) < MIN_VOTERS or len(v) % 2 == 0:
                skipped[f"majority:{t}:{h}"] = f"ผู้ลงคะแนน {len(v)} ตัว (ต้อง ≥ {MIN_VOTERS} และเป็นเลขคี่)"
                continue
            total = sum(d for d, _ in v.values())
            preds.append(Prediction(t, "majority", h, 1 if total > 0 else -1, float(total), px))
    if not preds:
        raise TradeUnavailableError(f"ไม่มีคำทำนายเลย — {skipped}")
    return preds, skipped, f"{latest:%Y-%m-%d}"


# ----------------------------------------------------------------------------- ให้คะแนนความแม่น
def resolve(log: pd.DataFrame, close: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """เติมผลจริงของคำทำนายที่ครบกำหนด (นับเป็นแท่งซื้อขาย) · ``pending`` / ``resolved`` / ``unpriceable`` (ไม่เดา)."""
    out = log.copy().reset_index(drop=True)
    n = len(out)
    status = np.array(["pending"] * n, dtype=object)
    ret = np.full(n, np.nan)
    went_up = np.full(n, np.nan)
    correct = np.full(n, np.nan)
    if n:
        dates = pd.to_datetime(out["date"]).to_numpy()
        bars = out["horizon"].map(HORIZON_BARS).to_numpy()
        for t, rows in out.groupby("ticker").groups.items():
            idx = np.array(list(rows))
            s = pl._clean(close[t]) if t in close.columns else pd.Series(dtype=float)  # noqa: SLF001
            if s.empty:
                status[idx] = "unpriceable"
                continue
            pos = s.index.get_indexer(pd.DatetimeIndex(dates[idx]))
            vals = s.to_numpy(dtype=float)
            stale = (pd.Timestamp(today) - pd.Timestamp(s.index.max())).days > MAX_STALE_DAYS
            for k, i in enumerate(idx):
                p = pos[k]
                if p < 0:
                    status[i] = "unpriceable"
                elif p + bars[i] < len(vals):
                    x = vals[p + bars[i]] / vals[p] - 1.0
                    status[i] = "resolved"
                    ret[i] = x * 100.0
                    went_up[i] = 1.0 if x > 0 else 0.0
                    correct[i] = 1.0 if (x > 0) == (out["direction"].iat[i] > 0) else 0.0
                elif stale:
                    status[i] = "unpriceable"
    out["status"], out["ret_pct"], out["went_up"], out["correct"] = status, ret, went_up, correct
    return out


def _independent_dates(all_dates: list[pd.Timestamp], horizon: str) -> set[pd.Timestamp]:
    step = HORIZON_BARS[horizon]
    return {d for i, d in enumerate(sorted(all_dates)) if i % step == 0}


def descriptive(resolved: pd.DataFrame) -> pd.DataFrame:
    """ความแม่นรวมทุกวันที่ครบกำหนด (พรรณนาเท่านั้น — ซ้อนทับกัน ห้ามใช้ตัดสิน/ห้ามคิด p-value)."""
    done = resolved[resolved["status"] == "resolved"] if not resolved.empty else resolved
    if done.empty:
        return pd.DataFrame(columns=["predictor", "horizon", "n_rows", "accuracy", "baseline", "excess_pp"])
    g = done.groupby(["predictor", "horizon"]).agg(n_rows=("correct", "size"), accuracy=("correct", "mean"), baseline=("went_up", "mean")).reset_index()
    g["accuracy"] *= 100.0
    g["baseline"] *= 100.0
    g["excess_pp"] = g["accuracy"] - g["baseline"]
    return g


def score_all(resolved: pd.DataFrame) -> dict[str, Any]:
    """คะแนนความแม่นต่อ (ตัวทำนาย × ช่วง) + ตัดสินตามเกณฑ์ที่ล็อก (วันที่ไม่ซ้อนทับเท่านั้น)."""
    from scipy import stats  # noqa: PLC0415

    groups: dict[str, Any] = {}
    all_dates = sorted(pd.to_datetime(resolved["date"]).unique()) if not resolved.empty else []
    for predictor in PREDICTORS:
        for h in HORIZON_BARS:
            info: dict[str, Any] = {"predictor": predictor, "horizon": h, "pending": 0, "unpriceable": 0, "n_days": 0, "n_independent": 0,
                                    "mean_accuracy": None, "mean_baseline": None, "mean_excess_acc_pp": None, "p_value": None,
                                    "status": "ยังไม่มีข้อมูล", "reasons": []}
            sub = resolved[(resolved["predictor"] == predictor) & (resolved["horizon"] == h)] if not resolved.empty else resolved
            if sub.empty:
                groups[f"{predictor}|{h}"] = info
                continue
            info["pending"] = int((sub["status"] == "pending").sum())
            info["unpriceable"] = int((sub["status"] == "unpriceable").sum())
            if info["unpriceable"]:
                info["status"] = "ปฏิเสธ"
                info["reasons"] = [f"{info['unpriceable']} คำทำนายครบกำหนดแล้วแต่หาราคาไม่ได้ — ห้ามตัดสิน (หาไม่ได้ ≠ ทายผิด ≠ ทายถูก)"]
                groups[f"{predictor}|{h}"] = info
                continue
            done = sub[sub["status"] == "resolved"]
            keep = _independent_dates(all_dates, h)
            daily = done.groupby("date").agg(accuracy=("correct", "mean"), baseline=("went_up", "mean"))
            info["n_days"] = len(daily)
            daily = daily[daily.index.isin(keep)]
            info["n_independent"] = len(daily)
            if daily.empty:
                info["status"] = "ยังตอบไม่ได้"
                info["reasons"] = ["ยังไม่มีคำทำนายที่ครบกำหนดในวันที่ไม่ซ้อนทับ"]
                groups[f"{predictor}|{h}"] = info
                continue
            excess = (daily["accuracy"] - daily["baseline"]) * 100.0
            info["mean_accuracy"] = float(daily["accuracy"].mean() * 100.0)
            info["mean_baseline"] = float(daily["baseline"].mean() * 100.0)
            info["mean_excess_acc_pp"] = float(excess.mean())
            if len(excess) >= 2 and float(excess.std(ddof=1)) > 0:
                tstat = float(excess.mean() / (excess.std(ddof=1) / math.sqrt(len(excess))))
                info["p_value"] = float(stats.t.sf(tstat, df=len(excess) - 1))
            if len(daily) < N_MIN[h]:
                info["status"] = "ยังตอบไม่ได้"
                info["reasons"] = [f"มีวันที่ไม่ซ้อนทับและครบกำหนด {len(daily)} จาก {N_MIN[h]} ที่เกณฑ์ล็อกไว้ — ตัวเลขแสดงได้ แต่ห้ามอ่านว่าชนะหรือแพ้"]
            else:
                checks = {
                    f"ส่วนเกินความแม่น ≥ +{MIN_EXCESS_ACC_PP:.1f} จุด": info["mean_excess_acc_pp"] >= MIN_EXCESS_ACC_PP,
                    f"p < {ALPHA:.4f} (ทดสอบหลายชุด)": info["p_value"] is not None and info["p_value"] < ALPHA,
                }
                info["status"] = "ผ่าน" if all(checks.values()) else "ไม่ผ่าน"
                info["reasons"] = [f"{'✓' if ok else '✗'} {k}" for k, ok in checks.items()]
            groups[f"{predictor}|{h}"] = info
    return {"groups": groups, "n_pass": sum(1 for g in groups.values() if g["status"] == "ผ่าน")}


# ----------------------------------------------------------------------------- บัญชีกระดาษ
def replay_accounts(log: pd.DataFrame, open_: pd.DataFrame, close: pd.DataFrame) -> dict[str, Any]:
    """เล่นซ้ำบัญชีกระดาษทั้ง 6 บัญชีจากสมุดคำทำนายที่แก้ไม่ได้ + ราคา (ไม่มีสถานะแยกที่ถูกแก้ได้) · ซื้อขายที่ **ราคาเปิดของแท่งถัดไป** หักค่าธรรมเนียม ``FEE``.

    30 ช่อง/บัญชี ช่องละ ``SLOT_USD`` · สัญญาณ = คำทำนาย 1d · ซื้อขายเฉพาะเมื่อสัญญาณพลิก (เงินสด+ขึ้น ⇒ ซื้อทั้งช่อง · ถือหุ้น+ลง ⇒ ขายทั้งช่อง)
    ``pending`` = สิ่งที่ควรทำที่เปิดตลาดพรุ่งนี้ (จากชุดล่าสุดที่ยังไม่มีแท่งเปิดถัดไป) · หุ้นที่ถืออยู่แล้วราคาปิดหาย ⇒ ช่องนั้นประเมินไม่ได้ (NAV = NaN ไม่เดา)
    """
    empty: dict[str, Any] = {"accounts": {}, "benchmarks": {}, "calendar_start": None}
    one = log[log["horizon"] == ACCOUNT_HORIZON] if not log.empty else log
    if one.empty or BENCHMARK not in close.columns:
        return empty
    cal = pd.DatetimeIndex(close[BENCHMARK].dropna().index)
    cols = list(TICKERS)
    o = open_.reindex(index=cal, columns=cols).to_numpy(dtype=float)
    c = close.reindex(index=cal, columns=cols).to_numpy(dtype=float)
    first_sig = pd.to_datetime(one["date"]).min()
    start = cal.searchsorted(first_sig)
    if start >= len(cal) or cal[start] != first_sig:
        return empty
    accounts: dict[str, Any] = {}
    for predictor in PREDICTORS:
        sig = one[one["predictor"] == predictor].copy()
        sig["date"] = pd.to_datetime(sig["date"])
        desired_by_date = {d: g.set_index("ticker")["direction"].reindex(cols).to_numpy(dtype=float) for d, g in sig.groupby("date")}
        cash = np.full(len(cols), SLOT_USD)
        shares = np.zeros(len(cols))
        bad = np.zeros(len(cols), dtype=bool)
        nav = pd.Series(np.nan, index=cal[start:], dtype=float)
        trades: list[dict[str, Any]] = []
        fees = 0.0
        for k in range(start, len(cal)):
            t = cal[k]
            prev = desired_by_date.get(cal[k - 1]) if k > start else None
            if prev is not None:
                for i in range(len(cols)):
                    d = prev[i]
                    if math.isnan(d):
                        continue
                    px = o[k, i]
                    if d > 0 and shares[i] == 0 and cash[i] > 0:
                        if not math.isfinite(px) or px <= 0:
                            bad[i] = True
                            continue
                        fee = cash[i] * FEE
                        shares[i] = (cash[i] - fee) / px
                        fees += fee
                        trades.append({"date": t, "ticker": cols[i], "side": "BUY", "price": px, "usd": float(cash[i]), "fee": float(fee)})
                        cash[i] = 0.0
                    elif d < 0 and shares[i] > 0:
                        if not math.isfinite(px) or px <= 0:
                            bad[i] = True
                            continue
                        gross = shares[i] * px
                        fee = gross * FEE
                        trades.append({"date": t, "ticker": cols[i], "side": "SELL", "price": px, "usd": float(gross), "fee": float(fee)})
                        fees += fee
                        cash[i] = gross - fee
                        shares[i] = 0.0
            held = shares > 0
            if (held & ~np.isfinite(c[k])).any():
                nav.iloc[k - start] = np.nan
                bad |= held & ~np.isfinite(c[k])
            else:
                nav.iloc[k - start] = float(cash.sum() + np.nansum(shares * c[k]))
        # คำสั่งที่ควรทำพรุ่งนี้: ชุดล่าสุดที่ยังไม่ถูกใช้ (วันที่ของชุด = แท่งสุดท้ายในปฏิทิน)
        pending: list[dict[str, Any]] = []
        today_set = desired_by_date.get(cal[-1])
        for i, tk in enumerate(cols):
            want = today_set[i] if today_set is not None else float("nan")
            if math.isnan(want):
                continue
            holds = shares[i] > 0
            action = "BUY" if (want > 0 and not holds) else "SELL" if (want < 0 and holds) else ("HOLD" if holds else "STAY_CASH")
            pending.append({"ticker": tk, "action": action, "signal": "ขึ้น" if want > 0 else "ลง"})
        accounts[predictor] = {
            "nav": nav, "trades": pd.DataFrame(trades), "n_trades": len(trades), "fees_paid_usd": float(fees),
            "positions": {cols[i]: float(shares[i]) for i in range(len(cols)) if shares[i] > 0},
            "pending": pending, "unpriceable": [cols[i] for i in range(len(cols)) if bad[i]],
            "last_signal_date": max(desired_by_date) if desired_by_date else None,
        }
    # แขนเทียบ: ซื้อครั้งเดียวที่เปิดของแท่งถัดจากชุดแรก ถือตลอด หักค่าธรรมเนียมซื้อ
    bench: dict[str, pd.Series] = {}
    e0 = start + 1
    if e0 < len(cal):
        idx = cal[start:]
        ew = np.full(len(idx), 30 * SLOT_USD, dtype=float)
        vo = np.full(len(idx), 30 * SLOT_USD, dtype=float)
        sh = (SLOT_USD * (1 - FEE)) / o[e0]
        ewv = (sh[None, :] * c[e0:]).sum(axis=1)                          # NaN เมื่อหุ้นตัวใดราคาหาย ⇒ NAV ของแขนเทียบเป็น NaN (ไม่เดา)
        ew[e0 - start:] = ewv
        vopen = float(open_[BENCHMARK].reindex(cal).iloc[e0])
        vo[e0 - start:] = (30 * SLOT_USD * (1 - FEE)) / vopen * close[BENCHMARK].reindex(cal).to_numpy(dtype=float)[e0:]
        bench = {"ew30": pd.Series(ew, index=idx), "voo": pd.Series(vo, index=idx)}
    return {"accounts": accounts, "benchmarks": bench, "calendar_start": f"{cal[start]:%Y-%m-%d}", "last_bar": f"{cal[-1]:%Y-%m-%d}"}


def account_verdicts(replay: dict[str, Any]) -> dict[str, Any]:
    """ตัดสินแต่ละบัญชีตาม PREREG ข้อ 7 (หลังหักค่าธรรมเนียม · ผลต่างรายสัปดาห์ที่ไม่ซ้อนทับ)."""
    from scipy import stats  # noqa: PLC0415

    out: dict[str, Any] = {}
    bench = replay.get("benchmarks") or {}
    for name, acc in (replay.get("accounts") or {}).items():
        nav = acc["nav"]
        v: dict[str, Any] = {"status": "ยังตอบไม่ได้", "reasons": [], "return_pct": None, "ew30_return_pct": None, "voo_return_pct": None,
                             "weeks": 0, "p_value": None, "n_trades": acc["n_trades"], "fees_paid_usd": acc["fees_paid_usd"]}
        if acc["unpriceable"] or nav.isna().any() or any(b.isna().any() for b in bench.values()):
            v["status"] = "ปฏิเสธ"
            v["reasons"] = [f"ประเมินราคาไม่ได้: {acc['unpriceable'] or 'หุ้นในแขนเทียบ/บัญชีมีราคาหาย'} — ห้ามตัดสิน (ประเมินไม่ได้ ≠ ศูนย์ ≠ ไม่เสียหาย)"]
            out[name] = v
            continue
        if "ew30" not in bench or len(nav) < 2:
            v["reasons"] = ["ยังไม่มีแท่งซื้อขายแรก (ต้องรอแท่งถัดจากชุดแรก)"]
            out[name] = v
            continue
        v["return_pct"] = float((nav.iloc[-1] / nav.iloc[0] - 1) * 100.0)
        v["ew30_return_pct"] = float((bench["ew30"].iloc[-1] / bench["ew30"].iloc[0] - 1) * 100.0)
        v["voo_return_pct"] = float((bench["voo"].iloc[-1] / bench["voo"].iloc[0] - 1) * 100.0)
        wk_acc, wk_ew = nav.iloc[::WEEK_BARS].pct_change().dropna(), bench["ew30"].iloc[::WEEK_BARS].pct_change().dropna()
        diff = (wk_acc - wk_ew).dropna()
        v["weeks"] = len(diff)
        if len(diff) >= 2 and float(diff.std(ddof=1)) > 0:
            v["p_value"] = float(stats.t.sf(float(diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff)))), df=len(diff) - 1))
        if len(diff) < MIN_WEEKS:
            v["reasons"] = [f"มี {len(diff)} สัปดาห์จาก {MIN_WEEKS} ที่เกณฑ์ล็อกไว้ — ตัวเลขแสดงได้ แต่ห้ามอ่านว่าชนะหรือแพ้"]
        else:
            checks = {
                "ผลตอบแทนสะสม > แขนแบ่งเท่ากัน 30 ช่อง (หลังหักค่าธรรมเนียม)": v["return_pct"] > v["ew30_return_pct"],
                "ผลตอบแทนสะสม > VOO": v["return_pct"] > v["voo_return_pct"],
                f"ผลต่างรายสัปดาห์เฉลี่ย > 0 ที่ p < {ALPHA_ACCOUNT:.4f}": float(diff.mean()) > 0 and v["p_value"] is not None and v["p_value"] < ALPHA_ACCOUNT,
            }
            v["status"] = "ผ่าน" if all(checks.values()) else "ไม่ผ่าน"
            v["reasons"] = [f"{'✓' if ok else '✗'} {k}" for k, ok in checks.items()]
        out[name] = v
    return out


def lock_status() -> dict[str, Any]:
    try:
        locked = LOCK_PATH.read_text(encoding="utf-8").split()[0]
        actual = hashlib.sha256(PREREG_PATH.read_bytes()).hexdigest()
    except OSError as exc:
        return {"ok": False, "reason": f"อ่านไฟล์ลงทะเบียน/ล็อกไม่ได้: {exc}"}
    return {"ok": locked == actual, "reason": None if locked == actual else "PREREG.md ไม่ตรงกับ hash ที่ล็อก — ห้ามอ่านผลเป็นหลักฐาน"}
