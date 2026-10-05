# -*- coding: utf-8 -*-
"""โหมด STOCK-DCA ("เลือกหุ้นรายตัว") — พอร์ต **กระดาษ** สำหรับ forward test แยกจากพอร์ตหลัก/DAR/SELECT ทั้งหมด.

ที่มา: ``research/stock_pick/PREREG.md`` (ล็อก SHA-256 ใน ``LOCK.sha256``) — **ไม่มี backtest และจะไม่มี**: ข้อมูลฟรีไม่มีหุ้นที่ตายแล้ว
(yfinance คืนว่างสำหรับ SIVB/FRC/TWTR/ATVI/XLNX ฯลฯ) ผลย้อนหลังใด ๆ จึงเห็นแต่ผู้รอด · หลักฐานมาจากการบันทึกแผนรายเดือนไปข้างหน้าเท่านั้น
สูตร (ปัจจัยเดียว): ทุกเดือนเลือก **K=5 ตัวที่ความผันผวน 3 ปีต่ำสุด** จากจักรวาลตายตัว 30 ตัว แบ่งเท่ากัน 5,000 บาท ไม่ขาย

ทุกค่า (จักรวาล K หน้าต่าง งบ) **ตายตัวในโค้ด ไม่อ่านจาก config.json** — เปลี่ยน = pre-registration ฉบับใหม่ ไม่ใช่การจูนจากผลที่เห็น
ตัวเลขทุกตัวคำนวณในโค้ด (ไม่มี LLM) · ข้อมูลขาด = ตัดออกพร้อมเหตุผลหรือ raise ไม่เดา · ดึงราคาทีละตัวด้วย ``yf.Ticker`` (ห้าม ``yf.download``)
โมดูลนี้ import จากโหมดอื่นเฉพาะ helper บริสุทธิ์ของ ``analysis/dar_dca.py`` (ดึงราคา ปัดเงิน ประมาณหน่วย)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from analysis import dar_dca
from data.fetcher import PriceDataUnavailableError

REPO_ROOT = Path(__file__).resolve().parent.parent
PREREG_PATH = REPO_ROOT / "research" / "stock_pick" / "PREREG.md"
LOCK_PATH = REPO_ROOT / "research" / "stock_pick" / "LOCK.sha256"

# --- ค่าคงที่ที่ล็อก (PREREG.md ข้อ 1–4) ---
K = 5
VOL_WINDOW_BARS = 756            # ≈ 3 ปี
MIN_VOL_BARS = 700
MAX_STALE_DAYS = 10              # แท่งล่าสุดเก่ากว่านี้ = ตัวนี้ราคาหาย (อาจเลิกกิจการ/ถูกซื้อ) ไม่ใช่ "ราคาเดิม"
MONTHLY_BUDGET_THB = 5000.0
ALLOCATION_UNIT_THB = dar_dca.ALLOCATION_UNIT_THB
MIN_COHORTS = 36                 # ก่อนนี้ตอบได้อย่างเดียวว่า "ยังตอบไม่ได้"
MATURE_COHORT_DAYS = 365
MIN_WIN_RATE_PCT = 60.0
BENCHMARK = "VOO"
FETCH_YEARS = 5
RULE = "lowvol_k5"
PRICE_COLUMN_NOTE = "ราคาปรับปันผล (total return) USD"

UNIVERSE: tuple[tuple[str, str], ...] = (
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "Nvidia"), ("GOOGL", "Alphabet"), ("AMZN", "Amazon"),
    ("META", "Meta"), ("AVGO", "Broadcom"), ("TSLA", "Tesla"), ("BRK-B", "Berkshire Hathaway"), ("JPM", "JPMorgan"),
    ("V", "Visa"), ("MA", "Mastercard"), ("UNH", "UnitedHealth"), ("LLY", "Eli Lilly"), ("JNJ", "Johnson & Johnson"),
    ("PG", "Procter & Gamble"), ("KO", "Coca-Cola"), ("PEP", "PepsiCo"), ("WMT", "Walmart"), ("COST", "Costco"),
    ("HD", "Home Depot"), ("MRK", "Merck"), ("ABBV", "AbbVie"), ("XOM", "Exxon Mobil"), ("CVX", "Chevron"),
    ("MCD", "McDonald's"), ("TXN", "Texas Instruments"), ("CSCO", "Cisco"), ("ORCL", "Oracle"), ("ADBE", "Adobe"),
)
TICKERS = tuple(t for t, _ in UNIVERSE)
_NAME = dict(UNIVERSE)

EVIDENCE_CAVEATS = (
    "ไม่มีหลักฐานย้อนหลังเลย — ข้อมูลฟรีไม่มีหุ้นที่ล้ม/ถูกซื้อไปแล้ว ผล backtest ใด ๆ จะเห็นแต่ผู้รอดและดูดีเกินจริง จึงไม่ทำ",
    "นี่คือ forward test พอร์ตกระดาษ: สูตรล็อกไว้ก่อน (PREREG.md) แล้วสะสมแผนรายเดือน ต้องครบ 36 เดือนก่อนจะตอบได้ว่าชนะหรือไม่ — ก่อนนั้นคำตอบคือ \"ยังตอบไม่ได้\"",
    "ผู้พัฒนาทำนายไว้ล่วงหน้าว่า ~70% ไม่ชนะ VOO (หุ้นผันผวนต่ำตามตลาดขาขึ้นไม่ทัน) — โหมดนี้มีแนวโน้มไม่ผ่านตั้งแต่ก่อนเริ่ม",
    "ไม่มีภาษีปันผล 15% และ FX spread · หุ้นที่ล้ม/ถูกซื้อภายหลังราคาจะหายจาก yfinance → ประเมินผลไม่ได้ ไม่ใช่ศูนย์และไม่ใช่ไม่เสียหาย",
    "หุ้นรายตัวกระจุกกว่ากองรวม · ยังไม่ผ่านโลกจำลอง (engine ไม่มีนโยบายคัดหุ้น) · ยังไม่ได้ตรวจว่า Dime ขายครบ · ไม่ใช่คำแนะนำลงทุน",
)


class StockPickUnavailableError(RuntimeError):
    """คำนวณแผนไม่ได้เพราะข้อมูล — ห้ามเดาแทน."""


# ----------------------------------------------------------------------------- ข้อมูล
def fetch_prices(
    tickers: tuple[str, ...] = TICKERS,
    *,
    fetch: Callable[[list[str], int], pd.DataFrame] = dar_dca.fetch_total_return_history,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """ราคาปรับปันผลทีละตัว · ตัวที่ดึงไม่ได้ไปอยู่ใน ``failed`` พร้อมเหตุผล (ไม่ทำให้ทั้งแผนล้ม ไม่ถูกเดา).

    VOO ถูกดึงด้วยเสมอ (แขนเทียบ) และ **ต้องดึงได้** — ไม่มีมันเทียบผลไม่ได้
    """
    cols: dict[str, pd.Series] = {}
    failed: dict[str, str] = {}
    for t in dict.fromkeys((*tickers, BENCHMARK)):
        try:
            cols[t] = fetch([t], FETCH_YEARS)[t]
        except (PriceDataUnavailableError, KeyError) as exc:
            failed[t] = str(exc)
    if BENCHMARK not in cols:
        raise StockPickUnavailableError(f"ดึงราคา {BENCHMARK} (แขนเทียบ) ไม่ได้: {failed.get(BENCHMARK)}")
    if not cols or len(cols) <= 1:
        raise StockPickUnavailableError("ดึงราคาหุ้นในจักรวาลไม่ได้เลย — " + "; ".join(f"{k}: {v}" for k, v in failed.items()))
    return pd.DataFrame(cols).sort_index(), failed


def annualised_vol(series: pd.Series) -> tuple[float | None, str | None]:
    """(ความผันผวนรายปี %, เหตุผลที่คำนวณไม่ได้) — ย้อนหลัง ``VOL_WINDOW_BARS`` แท่ง ต้องมี ≥ ``MIN_VOL_BARS``."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    s = s[s > 0].iloc[-(VOL_WINDOW_BARS + 1):]
    ret = np.log(s).diff().dropna()
    if len(ret) < MIN_VOL_BARS - 1:
        return None, f"ประวัติไม่พอ ({len(ret) + 1} แท่ง ต้อง ≥ {MIN_VOL_BARS})"
    v = float(ret.std(ddof=1) * math.sqrt(252) * 100.0)
    return (v, None) if math.isfinite(v) else (None, "คำนวณความผันผวนไม่ได้ (ค่าไม่ใช่จำนวนจำกัด)")


# ----------------------------------------------------------------------------- แผน
@dataclass
class StockPlanLine:
    ticker: str
    name: str
    rank: int
    vol_pct: float
    amount_thb: int
    price_usd: float | None
    units: float | None


@dataclass
class StockPlan:
    plan_month: str
    lines: list[StockPlanLine]
    universe: list[str]                       # ตัวที่ผ่านเกณฑ์เดือนนี้ (ขาเงาแบ่งเท่ากันใช้ชุดนี้)
    ranking: list[tuple[str, float]]          # ทุกตัวที่ผ่านเกณฑ์เรียงจากผันผวนต่ำ
    skipped: dict[str, str] = field(default_factory=dict)
    data_through: str = ""
    fx_rate: float = 0.0
    fx_is_live: bool = True
    rule: str = RULE
    notes: list[str] = field(default_factory=list)

    @property
    def total_thb(self) -> int:
        return int(sum(ln.amount_thb for ln in self.lines))


def plan_month_of(now: datetime | pd.Timestamp | None = None) -> pd.Period:
    return dar_dca.plan_month_of(now)


def build_plan(
    prices: pd.DataFrame,
    plan_month: pd.Period,
    *,
    fx_rate: float,
    fx_is_live: bool = True,
    failed: Mapping[str, str] | None = None,
) -> StockPlan:
    """เลือก K ตัวที่ผันผวนต่ำสุด — ตัวที่ข้อมูลไม่พอ/ราคาหาย **ถูกตัดพร้อมเหตุผล** ไม่ถูกเดา."""
    if prices.empty:
        raise StockPickUnavailableError("ไม่มีราคาเลย")
    latest = pd.Timestamp(prices[BENCHMARK].dropna().index.max())
    skipped: dict[str, str] = {t: f"ดึงราคาไม่ได้: {why}" for t, why in (failed or {}).items() if t != BENCHMARK}
    vols: dict[str, float] = {}
    for t in TICKERS:
        if t in skipped:
            continue
        if t not in prices.columns:
            skipped[t] = "ไม่มีข้อมูลราคา"
            continue
        col = prices[t].dropna()
        if col.empty:
            skipped[t] = "ไม่มีข้อมูลราคา"
            continue
        if (latest - pd.Timestamp(col.index.max())).days > MAX_STALE_DAYS:
            skipped[t] = f"ราคาล่าสุดเก่า ({col.index.max():%Y-%m-%d}) — อาจเลิกกิจการ/ถูกซื้อ/ย้ายตัวย่อ"
            continue
        v, why = annualised_vol(col)
        if v is None:
            skipped[t] = why or "คำนวณไม่ได้"
        else:
            vols[t] = v
    if len(vols) < K:
        raise StockPickUnavailableError(f"หุ้นที่ผ่านเกณฑ์มี {len(vols)} ตัว น้อยกว่า K={K} — ไม่เลือกให้ ({skipped})")
    ranking = sorted(vols.items(), key=lambda kv: (kv[1], kv[0]))
    chosen = [t for t, _ in ranking[:K]]
    amounts = dar_dca.round_to_units(pd.Series(1.0 / K, index=chosen), MONTHLY_BUDGET_THB, ALLOCATION_UNIT_THB)
    lines = []
    for rank, (t, v) in enumerate(ranking[:K], start=1):
        px = float(prices[t].dropna().iloc[-1])
        lines.append(StockPlanLine(t, _NAME[t], rank, v, amounts[t], px, dar_dca.estimate_units(amounts[t], fx_rate, px)))
    notes = []
    if not fx_is_live:
        notes.append("อัตราแลกเปลี่ยนเป็นค่าสำรอง (ดึงสดไม่ได้) — หน่วยโดยประมาณอาจคลาดเคลื่อน")
    return StockPlan(
        plan_month=str(plan_month),
        lines=lines,
        universe=sorted(vols),
        ranking=ranking,
        skipped=skipped,
        data_through=f"{latest:%Y-%m-%d}",
        fx_rate=float(fx_rate),
        fx_is_live=bool(fx_is_live),
        notes=notes,
    )


def lock_status() -> dict[str, Any]:
    """PREREG.md ตรงกับ hash ที่ล็อกหรือไม่ — แก้ไฟล์ที่ล็อก = หลักฐานทั้งชุดใช้ไม่ได้ ต้องบอกบนจอ."""
    import hashlib

    try:
        locked = LOCK_PATH.read_text(encoding="utf-8").split()[0]
        actual = hashlib.sha256(PREREG_PATH.read_bytes()).hexdigest()
    except OSError as exc:
        return {"ok": False, "reason": f"อ่านไฟล์ลงทะเบียน/ล็อกไม่ได้: {exc}"}
    return {"ok": locked == actual, "reason": None if locked == actual else "PREREG.md ไม่ตรงกับ hash ที่ล็อก — ห้ามอ่านผลเป็นหลักฐาน"}
