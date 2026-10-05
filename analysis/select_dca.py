# -*- coding: utf-8 -*-
"""โหมด "โมเดลเลือกกองเอง" (SELECT-DCA) — พอร์ตทดลองที่แยกจากพอร์ตหลัก (ERC/blend) และพอร์ต DAR ทุกอย่าง เริ่มจากศูนย์.

ที่มา/หลักฐาน: ``research/dar_select/`` (PLAN.md → PREREG.md ล็อก SHA-256 → ``confirm.py`` รันครั้งเดียว → ``RESULT.md``)
ทุกเดือนกติกาในโค้ดเลือกเองว่าจะซื้อ **K = 5 จาก 12 ตลาด** (จักรวาลคงที่ในโค้ด) ไม่ขาย ลงเงินเต็มงบ — ผู้ใช้ไม่ต้องเลือกกอง

กติกาหลัก (``RULE``): **yield_topk (B)** — 5 ตลาดที่ dividend yield 12 เดือน ณ สิ้นปีก่อนหน้าสูงสุด แบ่งเท่ากัน
(กฎ A — DAR top-K — คำนวณคู่กันไว้แสดงเปรียบเทียบเท่านั้น ไม่ได้ใช้ซื้อ) ทั้งสองผ่านเกณฑ์ที่ล็อกไว้ใน JST 1870–1974 แต่:
**ผลนั้นยังไม่ใช่หลักฐานว่าใช้เงินจริงได้** (survivorship, หน้าต่างซ้อนทับ, ผลสกุลท้องถิ่นล้ม, น้ำหนักตลาดเดียวถึง ~49%,
ไม่เคยทดสอบกับ ETF 2026/ค่าธรรมเนียม/ภาษีปันผล/ซื้อรายเดือน) — ``evidence_summary()`` ต้องแสดงคู่กับทุกแผนเสมอ

ส่วนที่ **ต่างจากสิ่งที่ทดสอบ** (ต้องบอกผู้ใช้): ทดสอบแบบลงเงินรายปี/สัญญาณรายปี → ที่นี่ลงรายเดือนโดยใช้การจัดอันดับ ณ สิ้นปีก่อนหน้า
คงเดิมทั้งปี (ปีละครั้ง) · ตลาด = ETF รายประเทศของ Franklin FTSE (ต่อประวัติด้วย iShares MSCI รายประเทศเฉพาะที่ต้องใช้วัดสัญญาณ DAR)

ค่าคงที่ทุกตัว (รายชื่อตลาด K เพดาน งบ) **ตายตัวในโค้ด ไม่อ่านจาก config.json** — เปลี่ยน = แก้โค้ดโดยตั้งใจ ไม่ใช่ตอบสนองต่อผลไม่กี่เดือน
ตัวเลขทุกตัวคำนวณในโค้ด (ไม่มี LLM) · ดึงราคา/ปันผลไม่ได้ = raise ไม่เดา · ดึงทีละกองด้วย ``yf.Ticker(...)`` ห้าม ``yf.download``
(เหตุผลเดียวกับ ``analysis/dar_dca.py``)
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from analysis import dar_dca
from data.fetcher import PriceDataUnavailableError
from portfolio.fees import DIME_FEE_RATE

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_PATH = REPO_ROOT / "research" / "dar_select" / "results_confirm.json"

# --- ค่าคงที่ที่ล็อก (PREREG.md ข้อ 1/5) ---
K = 5
GUARD = 0.25                      # กองที่มูลค่า ≥ 25% ของพอร์ตห้ามซื้อเพิ่ม (ปลดเฉพาะเดือนที่ทุกกองติดเพดาน)
RULE = "yield_topk"               # กฎที่ใช้ซื้อ (B) · "dar_topk" (A) แสดงเปรียบเทียบเท่านั้น
MIN_HISTORY_MONTHS = 181          # จักรวาลต้องมีประวัติอย่างน้อย 181 เดือน (นับรวมกองพี่) — ตรงกับ PLAN หัวข้อ 4
MONTHLY_BUDGET_THB = 5000.0       # ตายตัวในโค้ด (ไม่ตาม dca.monthly_budget_thb)
ALLOCATION_UNIT_THB = dar_dca.ALLOCATION_UNIT_THB
YIELD_WINDOW_DAYS = 365
MIN_YIELD_HISTORY_DAYS = 330      # ETF ต้องมีข้อมูลก่อน as-of อย่างน้อยเท่านี้วัน จึงนับ yield 12 เดือนได้ครบ
FETCH_YEARS = 32                  # ให้ถึงกองพี่ที่เก่าสุด (1996) + เผื่อ
FETCH_ATTEMPTS = 3
_RETRY_SLEEP_SEC = 2.0


@dataclass(frozen=True)
class Market:
    ticker: str
    name_th: str
    proxy: str | None          # กองพี่ที่ตามดัชนีประเทศเดียวกัน (ใช้ยืดประวัติเฉพาะสัญญาณ DAR) — ต่างเจ้าดัชนี (MSCI vs FTSE)
    fee_pct: float             # ค่าธรรมเนียมกอง (วัดจาก yfinance 2026-10-05)


#: จักรวาล: ETF รายประเทศ ค่าธรรมเนียม ≤ 0.20% ประเภทเดียวกัน (ตลาดหุ้นรายประเทศ) ไม่ซ้อนกัน — **ยังไม่ได้ตรวจว่า Dime ขายครบ**
#: (ผู้ใช้รับทราบแล้ว PLAN หัวข้อ 3.3) · FLFR/FLHK ปิดกองไปแล้ว (Yahoo ไม่มีข้อมูล) จึงไม่อยู่ในรายการ · FLIN เข้าได้เมื่อมีประวัติครบ 181 เดือน
UNIVERSE: tuple[Market, ...] = (
    Market("VOO", "สหรัฐ", "SPY", 0.03),
    Market("FLJP", "ญี่ปุ่น", "EWJ", 0.09),
    Market("FLGB", "สหราชอาณาจักร", "EWU", 0.09),
    Market("FLCA", "แคนาดา", "EWC", 0.09),
    Market("FLGR", "เยอรมนี", "EWG", 0.09),
    Market("FLSW", "สวิตเซอร์แลนด์", "EWL", 0.09),
    Market("FLAU", "ออสเตรเลีย", "EWA", 0.09),
    Market("FLKR", "เกาหลีใต้", "EWY", 0.09),
    Market("FLTW", "ไต้หวัน", "EWT", 0.19),
    Market("FLIN", "อินเดีย", "INDA", 0.19),
    Market("FLBR", "บราซิล", "EWZ", 0.19),
    Market("FLMX", "เม็กซิโก", "EWW", 0.19),
    Market("FLCH", "จีน", "MCHI", 0.19),
)
TICKERS = tuple(m.ticker for m in UNIVERSE)
_MARKET = {m.ticker: m for m in UNIVERSE}


class SelectUnavailableError(RuntimeError):
    """คำนวณแผนไม่ได้เพราะข้อมูล (ราคา/ปันผล/ประวัติไม่พอ) — ห้ามเดาแทน."""


# ----------------------------------------------------------------------------- ข้อมูล
def _ticker_history(ticker: str, auto_adjust: bool) -> pd.Series:
    import yfinance as yf

    end = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize() + pd.Timedelta(days=1)
    start = end - pd.DateOffset(years=FETCH_YEARS)
    last: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            hist = yf.Ticker(ticker).history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                                             auto_adjust=auto_adjust, actions=False)
            close = pd.to_numeric(hist["Close"], errors="coerce").dropna() if "Close" in hist else pd.Series(dtype=float)
            close = close[close > 0]
            if close.empty:
                raise ValueError("ไม่มีราคาในช่วงที่ขอ")
            idx = pd.DatetimeIndex(close.index)
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            return pd.Series(close.to_numpy(dtype=float), index=idx.normalize())
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < FETCH_ATTEMPTS - 1:
                time.sleep(_RETRY_SLEEP_SEC)
    raise PriceDataUnavailableError(f"ดึงราคาของ {ticker} ไม่สำเร็จหลังลอง {FETCH_ATTEMPTS} ครั้ง: {last}")


def fetch_dividends(ticker: str) -> pd.Series:
    """ปันผลจริงต่อหน่วย (USD) — ซีรีส์ว่างจริง = กองไม่จ่ายปันผล · ดึงล้ม = ล้มดัง."""
    import yfinance as yf

    last: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            s = pd.to_numeric(yf.Ticker(ticker).dividends, errors="coerce").dropna()
            idx = pd.DatetimeIndex(s.index)
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            return pd.Series(s.to_numpy(dtype=float), index=idx.normalize())
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < FETCH_ATTEMPTS - 1:
                time.sleep(_RETRY_SLEEP_SEC)
    raise PriceDataUnavailableError(f"ดึงปันผลของ {ticker} ไม่สำเร็จหลังลอง {FETCH_ATTEMPTS} ครั้ง: {last}")


@dataclass
class MarketData:
    """ข้อมูลดิบทั้งหมดที่แผนหนึ่งใช้ (ดึงทีละกอง)."""

    adj: dict[str, pd.Series]          # ราคาปรับปันผล (total return) ของกอง + กองพี่
    raw_close: dict[str, pd.Series]    # ราคาจริงไม่ปรับ (ไว้หาร yield ณ วันนั้น)
    dividends: dict[str, pd.Series]


def fetch_market_data(tickers: Sequence[str] = TICKERS) -> MarketData:
    """ดึงข้อมูลสดของจักรวาล — ล้มกองใดกองหนึ่ง = ``PriceDataUnavailableError`` (ไม่ส่งชุดครึ่งเดียว)."""
    adj: dict[str, pd.Series] = {}
    raw_close: dict[str, pd.Series] = {}
    divs: dict[str, pd.Series] = {}
    for t in tickers:
        adj[t] = _ticker_history(t, True)
        raw_close[t] = _ticker_history(t, False)
        divs[t] = fetch_dividends(t)
        proxy = _MARKET[t].proxy if t in _MARKET else None
        if proxy and proxy not in adj:
            try:
                adj[proxy] = _ticker_history(proxy, True)
            except PriceDataUnavailableError:
                pass          # กองพี่ใช้แค่ยืดประวัติสัญญาณ DAR — ขาด = กองนั้นมีประวัติแค่ของตัวเอง (ตรวจคุณสมบัติจริงทีหลัง)
    return MarketData(adj=adj, raw_close=raw_close, dividends=divs)


# ----------------------------------------------------------------------------- สัญญาณ
def spliced_adjusted(data: MarketData, ticker: str) -> pd.Series:
    """ราคาปรับปันผลของกอง ต่อประวัติด้วยกองพี่ (ปรับระดับที่วันเชื่อม — เหมือน ``proxy_history``)."""
    own = data.adj[ticker].dropna()
    proxy = _MARKET[ticker].proxy if ticker in _MARKET else None
    if not proxy or proxy not in data.adj:
        return own
    sib = data.adj[proxy].dropna()
    join = own.index[0]
    before = sib.loc[:join]
    if before.empty or sib.index[0] >= join:
        return own
    scale = float(own.iloc[0]) / float(before.iloc[-1])
    return pd.concat([sib[sib.index < join] * scale, own])


def history_months(data: MarketData, ticker: str) -> int:
    s = spliced_adjusted(data, ticker)
    return int(s.resample("ME").last().dropna().shape[0])


def dividend_yield(data: MarketData, ticker: str, asof: pd.Timestamp) -> float | None:
    """dividend yield 12 เดือนล่าสุด ณ ``asof`` = ปันผลรวม (asof−365d, asof] ÷ ราคาจริงวันทำการล่าสุดที่ ≤ asof.

    ไม่มีข้อมูลพอ (ETF เพิ่งตั้งหลัง asof−330 วัน) → ``None`` (ไม่ถูกจัดอันดับ — ไม่ใช่ 0) ·
    ซีรีส์ปันผลว่างแต่ราคามีพอ = yield 0 จริง (กองไม่จ่ายปันผล)
    """
    px = data.raw_close.get(ticker)
    if px is None or px.empty:
        return None
    px = px.loc[:asof]
    if px.empty or (asof - px.index[0]).days < MIN_YIELD_HISTORY_DAYS:
        return None
    div = data.dividends.get(ticker, pd.Series(dtype=float))
    window = div[(div.index > asof - pd.Timedelta(days=YIELD_WINDOW_DAYS)) & (div.index <= asof)] if len(div) else div
    return float(window.sum() / float(px.iloc[-1]))


def annual_levels(adj: pd.Series, asof_year: int) -> pd.Series:
    """ระดับ total return สิ้นปีปฏิทิน ≤ ``asof_year`` (วันทำการสุดท้ายของปี)."""
    s = adj.dropna()
    s = s[s.index.year <= asof_year]
    return s.groupby(s.index.year).last()


def dar_signals(data: MarketData, tickers: Sequence[str], asof_year: int) -> dict[str, float | None]:
    """z ของ DAR รายปี (วิธี M1 ที่ล็อกใน PREREG): AMP = ln P[t−5] − ln P[t]; OLD = ln P[t−5] − ln P[t−15]; DAR = AMP + 0.5·OLD.

    ต้องมีระดับสิ้นปีต่อเนื่อง 16 ตัว (t−15..t) · กองที่ไม่มีสัญญาณ = ``None`` · ต้องมี ≥ 2 กองถึงจะมี z
    """
    vals: dict[str, float] = {}
    for t in tickers:
        lv = annual_levels(spliced_adjusted(data, t), asof_year)
        years = list(range(asof_year - 15, asof_year + 1))
        if not all(y in lv.index for y in years):
            continue
        p = lv.loc[years].to_numpy(dtype=float)
        if (p <= 0).any() or np.isnan(p).any():
            continue
        vals[t] = (math.log(p[10]) - math.log(p[15])) + dar_dca.DRIFT_COEF * (math.log(p[10]) - math.log(p[0]))
    out: dict[str, float | None] = {t: None for t in tickers}
    if len(vals) >= 2:
        x = np.array(list(vals.values()))
        sd = max(float(x.std(ddof=0)), dar_dca.SD_MIN)
        for t, v in vals.items():
            out[t] = float((v - x.mean()) / sd)
    return out


# ----------------------------------------------------------------------------- เลือก + แบ่งเงิน
def eligible_markets(data: MarketData) -> tuple[list[str], dict[str, str]]:
    """ตลาดที่เข้าจักรวาลเดือนนี้ (ประวัติ ≥ 181 เดือน นับรวมกองพี่ และมีราคา) + เหตุผลของตัวที่ถูกตัด."""
    ok, out = [], {}
    for m in UNIVERSE:
        if m.ticker not in data.adj:
            out[m.ticker] = "ไม่มีข้อมูลราคา"
            continue
        months = history_months(data, m.ticker)
        if months < MIN_HISTORY_MONTHS:
            out[m.ticker] = f"ประวัติ {months} เดือน (ต้องการ {MIN_HISTORY_MONTHS}) — เข้าได้เมื่อครบ"
            continue
        ok.append(m.ticker)
    return ok, out


def choose(rule: str, scores: Mapping[str, float | None], holdings_share: Mapping[str, float], k: int = K,
           guard: float = GUARD) -> tuple[list[str], bool]:
    """เลือก ``k`` ตลาดคะแนนสูงสุดที่ซื้อได้ (มีคะแนน ∧ ไม่ติดเพดาน) — คืน (รายชื่อ, ปลดเพดานเดือนนี้ไหม).

    ``rule`` ใช้แค่ในข้อความ — ฟังก์ชันนี้จัดอันดับตาม ``scores`` ตรง ๆ (yield หรือ z) · ทุกตลาดที่มีคะแนนติดเพดานหมด → ปลดเพดานเดือนนั้น
    """
    ranked = {t: s for t, s in scores.items() if s is not None and math.isfinite(s)}
    if len(ranked) < 2:
        raise SelectUnavailableError(f"มีตลาดที่มีคะแนนแค่ {len(ranked)} ตัว (ต้องการอย่างน้อย 2) — เลือกกองไม่ได้ ({rule})")
    cand = [t for t in ranked if holdings_share.get(t, 0.0) < guard]
    lifted = False
    if not cand:
        cand, lifted = list(ranked), True
    order = sorted(cand, key=lambda t: (-ranked[t], t))
    return order[:k], lifted


def split_money(rule: str, chosen: Sequence[str], scores: Mapping[str, float | None], budget_thb: float = MONTHLY_BUDGET_THB) -> dict[str, int]:
    """เงินต่อกอง (เป็นจำนวนเต็มหลักร้อย รวมพอดีงบ ทุกกอง ≥ 1 หน่วย) — B = แบ่งเท่ากัน · A = พื้น/เพดานของ DAR."""
    n = len(chosen)
    if rule == "dar_topk":
        raw = np.array([max(1.0 + float(scores[t]), 0.0) / n for t in chosen])
        w = dar_dca.floor_project(raw, dar_dca.FLOOR_FRAC / n)
        if dar_dca.CAP_MULT / n < 1.0:
            w = dar_dca.cap_project(w, dar_dca.CAP_MULT / n)
    else:
        w = np.full(n, 1.0 / n)
    return dar_dca.round_to_units(pd.Series(w, index=list(chosen)), budget_thb, ALLOCATION_UNIT_THB)


@dataclass
class SelectLine:
    ticker: str
    name_th: str
    amount_thb: int
    weight: float
    rank: int
    yield_pct: float | None
    dar_z: float | None
    price_usd: float | None
    units: float | None
    holdings_share_pct: float


@dataclass
class SelectPlan:
    plan_month: str
    rule: str
    asof: str                                   # วันที่ที่ใช้จัดอันดับ (สิ้นปีก่อนหน้า)
    budget_thb: float
    unallocated_thb: float
    fx_rate: float
    fx_is_live: bool
    lines: list[SelectLine]
    universe: list[str]                         # ตลาดที่เข้าจักรวาลเดือนนี้ (ใช้เป็นพอร์ตเงา)
    skipped: dict[str, str]                     # ตลาดที่ถูกตัด + เหตุผล
    guard_lifted: bool
    alt_rule_top: list[str]                     # 5 อันดับของกฎอีกแบบ (แสดงเปรียบเทียบ ไม่ได้ซื้อ)
    data_through: str
    notes: list[str] = field(default_factory=list)
    generated_at: str = ""

    @property
    def total_thb(self) -> int:
        return int(sum(ln.amount_thb for ln in self.lines))

    def to_dict(self) -> dict[str, Any]:
        return {**{k: v for k, v in self.__dict__.items() if k != "lines"}, "lines": [ln.__dict__ for ln in self.lines]}


def build_plan(data: MarketData, plan_month: pd.Period, holdings_value_usd: Mapping[str, float] | None = None,
               fx_rate: float = 0.0, fx_is_live: bool = False, rule: str = RULE) -> SelectPlan:
    """แผนของเดือน ``plan_month``: จัดอันดับ ณ สิ้นปีก่อนหน้า (คงเดิมทั้งปี) → เพดาน 25% จากมูลค่าที่ถืออยู่ → 5 ตลาด.

    ``holdings_value_usd`` = มูลค่าที่ถืออยู่ต่อกองตามราคาล่าสุด (จากสมุด) — ว่าง = พอร์ตเริ่มจากศูนย์ ยังไม่มีเพดาน
    """
    asof_year = plan_month.year - 1
    asof = pd.Timestamp(year=asof_year, month=12, day=31)
    universe, skipped = eligible_markets(data)
    if len(universe) < 2:
        raise SelectUnavailableError(f"ตลาดที่เข้าจักรวาลได้มีแค่ {len(universe)} ตัว: {skipped}")
    yields = {t: dividend_yield(data, t, asof) for t in universe}
    z = dar_signals(data, universe, asof_year)
    holdings = dict(holdings_value_usd or {})
    total = float(sum(holdings.values()))
    share = {t: (holdings.get(t, 0.0) / total if total > 0 else 0.0) for t in universe}
    scores = yields if rule == "yield_topk" else z
    notes: list[str] = []
    missing = [t for t in universe if scores.get(t) is None]
    if missing:
        notes.append("ไม่ถูกจัดอันดับ (ข้อมูลไม่พอ): " + ", ".join(missing))
    chosen, lifted = choose(rule, scores, share)
    if lifted:
        notes.append("ทุกตลาดที่จัดอันดับได้ติดเพดาน 25% พร้อมกัน — ปลดเพดานเฉพาะเดือนนี้")
    amounts = split_money(rule, chosen, scores)
    alt_rule = "dar_topk" if rule == "yield_topk" else "yield_topk"
    alt_scores = z if alt_rule == "dar_topk" else yields
    try:
        alt_top, _ = choose(alt_rule, alt_scores, share)
    except SelectUnavailableError:
        alt_top = []
    latest_raw = {t: float(data.raw_close[t].iloc[-1]) for t in chosen if t in data.raw_close and not data.raw_close[t].empty}
    ranked = sorted([t for t in universe if scores.get(t) is not None], key=lambda t: (-scores[t], t))
    lines = []
    for t in sorted(chosen, key=lambda x: -amounts[x]):
        price = latest_raw.get(t)
        units = None if (price is None or fx_rate <= 0) else amounts[t] / fx_rate * (1.0 - DIME_FEE_RATE) / price
        lines.append(SelectLine(t, _MARKET[t].name_th, int(amounts[t]), amounts[t] / MONTHLY_BUDGET_THB, ranked.index(t) + 1,
                                None if yields.get(t) is None else yields[t] * 100.0, z.get(t), price, units, share.get(t, 0.0) * 100.0))
    last_bar = max(s.index.max() for t, s in data.adj.items() if t in universe)
    return SelectPlan(
        plan_month=str(plan_month), rule=rule, asof=asof.strftime("%Y-%m-%d"), budget_thb=MONTHLY_BUDGET_THB,
        unallocated_thb=MONTHLY_BUDGET_THB - sum(amounts.values()), fx_rate=float(fx_rate), fx_is_live=bool(fx_is_live), lines=lines,
        universe=universe, skipped=skipped, guard_lifted=lifted, alt_rule_top=alt_top, data_through=last_bar.strftime("%Y-%m-%d"),
        notes=notes, generated_at=datetime.now().isoformat(timespec="seconds"),
    )


# ----------------------------------------------------------------------------- หลักฐาน
EVIDENCE_CAVEATS = (
    "ผลยืนยันมาจากตลาดระดับประเทศ 1887–1974 (JST) ไม่ใช่ ETF รายประเทศปี 2026 — ค่าธรรมเนียม ภาษีปันผล 15% และการซื้อรายเดือนไม่เคยถูกทดสอบ",
    "JST มีแต่ประเทศที่ร่ำรวยในวันนี้ (survivorship) — กติกาที่ซื้อตลาดปันผลสูง/ราคาตกได้เปรียบจากข้อนี้โดยตรง",
    "~69 หน้าต่าง DCA 20 ปีที่ซ้อนทับกัน ไม่ใช่ตัวอย่างอิสระ — ผ่านเกณฑ์ ≠ มีนัยสำคัญทางสถิติ",
    "ผลแบบสกุลท้องถิ่น (ผลรอง) ล้มหนัก: ผลสรุปขึ้นกับการแปลงเป็น USD ในยุคเงินเฟ้อสูง/ค่าเงินพลิก",
    "เพดาน 25% กัน \"การซื้อเพิ่ม\" ไม่กันมูลค่าที่ขึ้นเอง — ตอนจบในการทดสอบมีตลาดเดียวสูงสุด 46–49% ของพอร์ต",
    "yield ที่ใช้จัดอันดับคือที่ ETF รายประเทศ \"จ่ายจริง\" ใน 12 เดือน (นับทุกการจ่าย รวมก้อนใหญ่ปลายปีที่อาจมีกำไรทุน เช่น FLJP จ่าย 1.52 ในธ.ค. เทียบ 0.25 กลางปี) "
    "ไม่ใช่ dividend-price ratio ของตลาดแบบที่ใช้ยืนยันใน JST — ตัวชี้วัดต่างกัน ผลยืนยันไม่ครอบคลุมส่วนต่างนี้ (วัดจริง 2026-10-05)",
    "ข้อมูลสำรวจยุคใหม่ (20 ประเทศ 1975–2025 รายปี) ให้กฎ DAR top-K แพ้ DAR เอียงทุกตลาด (ไม่ใช่หลักฐาน — เปิดดูแล้ว)",
    "คำทำนายล่วงหน้าของผู้พัฒนาพลาด (คาดว่าไม่มีกฎใดผ่าน) — ผลที่ผ่านจึงไม่ได้มาจากการตั้งใจให้ผ่าน แต่ก็อย่าอ่านเกินจริง",
)


def evidence_summary(path: Path | None = None) -> dict[str, Any]:
    """สรุปผลยืนยัน (ผลหลัก USD, K=5) จาก ``results_confirm.json`` — ไม่มีไฟล์ = ``SelectUnavailableError`` (ไม่แสดงตัวเลขเดา)."""
    p = Path(path or EVIDENCE_PATH)
    if not p.exists():
        raise SelectUnavailableError(f"ไม่พบไฟล์หลักฐาน {p} — แสดงสถานะหลักฐานไม่ได้")
    d = json.loads(p.read_text(encoding="utf-8"))["usd"]
    rows = {}
    for key, label in (("yield_topk_K5", "B — yield top-5 (กฎที่ใช้ซื้อ)"), ("dar_topk_K5", "A — DAR top-5 (เทียบเฉยๆ)")):
        e = d["rules"][key]
        s = e["vs_equal"]
        rows[key] = {"label": label, "mean_pct": s["mean_pct"], "win_pct": s["win_pct"], "p10_pct": s["p10_pct"], "worst_pct": s["worst_pct"],
                     "annual_excess_pct": s["annual_excess_pct"], "annual_excess_ci95_pct": s["annual_excess_ci95_pct"],
                     "first_half_mean_pct": s["first_half_mean_pct"], "second_half_mean_pct": s["second_half_mean_pct"],
                     "vs_dar_all_mean_pct": e["vs_dar_all"]["mean_pct"], "max_single_market_weight_pct": e["max_single_market_weight_pct"]["max"],
                     "passed": bool(d["verdict"][key]["PASS"])}
    return {"data": "JST Macrohistory R6, 1870–1974, ไม่รวมสหรัฐ, 17 ตลาด", "windows": d["n_windows"], "rows": rows,
            "caveats": list(EVIDENCE_CAVEATS), "thresholds": "เฉลี่ย ≥ +1% · ชนะ ≥ 60% · p10 ≥ −5% · ไม่แพ้ DAR เอียงทุกตลาด"}


# ----------------------------------------------------------------------------- ต่อกับสมุด/ค่าเงิน (ของจริง — เทสต์สตับตรงนี้)
def plan_month_of(now: datetime | pd.Timestamp | None = None) -> pd.Period:
    ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="Asia/Bangkok")
    return pd.Period(year=ts.year, month=ts.month, freq="M")


def holdings_value_usd(tx: pd.DataFrame, data: MarketData) -> dict[str, float]:
    """มูลค่าที่ถืออยู่ต่อกอง (USD) = หน่วยสะสมจากสมุด × ราคาจริงล่าสุด — ไว้ใช้เพดาน 25% ของเดือนถัดไป."""
    if tx.empty:
        return {}
    units = tx.groupby("ticker")["units"].sum()
    out: dict[str, float] = {}
    for t, u in units.items():
        px = data.raw_close.get(t)
        if px is None or px.empty:
            raise SelectUnavailableError(f"ไม่มีราคาล่าสุดของ {t} (ที่ถืออยู่ตามสมุด) — คำนวณเพดานน้ำหนักไม่ได้")
        out[t] = float(u) * float(px.iloc[-1])
    return out


def build_select_plan_live(now: datetime | pd.Timestamp | None = None) -> SelectPlan:
    """แผนของเดือนนี้จากข้อมูลสดและสมุด SELECT ปัจจุบัน — ดึงไม่ได้ = ``PriceDataUnavailableError`` / ``SelectUnavailableError``."""
    from portfolio.select_ledger import load_select_transactions
    from utils.fx import get_usdthb

    data = fetch_market_data()
    tx = load_select_transactions()
    fx = get_usdthb()
    return build_plan(data, plan_month_of(now), holdings_value_usd(tx, data), fx_rate=float(fx.rate), fx_is_live=bool(fx.is_live))


def compare_live(now: datetime | pd.Timestamp | None = None) -> dict[str, Any] | None:
    """เทียบพอร์ต SELECT กับเงาแบ่งเท่ากันทั้งจักรวาล (เงินเข้าชุดเดียวกัน) — สมุดว่าง = ``None``."""
    from portfolio.select_ledger import compare_with_equal_shadow, load_select_transactions
    from utils.fx import get_usdthb

    tx = load_select_transactions()
    if tx.empty:
        return None
    need = sorted({t for u in tx["universe"] for t in str(u).split(",") if t} | set(tx["ticker"]))
    prices = pd.DataFrame({t: _ticker_history(t, True) for t in need})
    return compare_with_equal_shadow(tx, prices, float(get_usdthb().rate))
