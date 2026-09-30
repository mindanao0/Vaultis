# -*- coding: utf-8 -*-
"""DAR-DCA — สูตรแบ่งเงิน DCA แบบทดลอง (หน้าแยก + พอร์ตแยก ไม่เกี่ยวกับแผน ERC หลัก).

สูตร (Drift-Adjusted Reversal, รุ่น 2) — ใช้ราคาปิดสิ้นเดือนแบบรวมปันผล **ก่อน** เดือนที่ซื้อ:

    AMP = ln(ค่าเฉลี่ยราคา ณ สิ้นเดือนที่ 54..66 ก่อน) − ln(ราคาสิ้นเดือนล่าสุด)   # แพ้/ชนะมา 5 ปี
    OLD = ln(ราคา 60 เดือนก่อน) − ln(ราคา 180 เดือนก่อน)                        # 10 ปีก่อนหน้านั้น
    DAR = AMP + 0.5 × OLD        (กองที่โตเร็วเป็นนิสัย μ ต่อปี: AMP ≈ −5μ, OLD ≈ 10μ → หักล้างกันพอดี)
    z   = (DAR − ค่าเฉลี่ยของทุกกอง) / max(ส่วนเบี่ยงเบนประชากร, 0.15)
    w   = max(1 + z, 0) / N  → ทุกกอง ≥ 0.2/N → ไม่มีกองไหนเกิน 1.5/N

**ทำไมถึงเป็นแบบนี้** (รายละเอียดเต็ม + hash ของการล็อกสูตรอยู่ที่ ``research/dar_dca/``)

- เงิน DCA ที่ห้ามขายถูกถือหลายปี สัญญาณที่มีผลต่อเงินปลายทางจึงต้องแม่นขึ้นตามระยะถือ
  ข้อมูลสหรัฐ 1926–2026: momentum 12 เดือนทำนายได้ 1–12 เดือนแล้วกลับทิศหลัง 5 ปี —
  เอียงเงิน DCA ตาม momentum แพ้การแบ่งเท่ากันใน 67–77% ของ DCA 20 ปี
- "ซื้อกองที่แพ้มา 5 ปี" ใช้ได้กับกลุ่มที่ส่วนต่างเป็นของชั่วคราว (อุตสาหกรรม/ประเทศ) แต่พังกับ
  กองที่คัดหุ้นตามลักษณะซึ่งแพ้/ชนะเป็นนิสัย — OLD หักนิสัยนั้นออก
- เพดาน 1.5/N เพิ่มในรอบ 2 หลังรอบ 1 พบว่าสูตรไม่มีเพดานซื้อทองเกือบเต็มกำลังตลอดช่วงทองร่วง
  1980–2001 (10% แย่สุด −21%) รุ่นนี้ผ่านการยืนยันรอบ 2 บนข้อมูลที่ไม่เคยเปิดดู
  (ดัชนี 20 ประเทศ + สินค้าโภคภัณฑ์ 9 ชนิด): +1.24% เงินปลายทาง DCA 20 ปีเทียบแบ่งเท่ากัน
  ส่วนเกิน +0.11%/ปี CI95 [−0.10, +0.32] — **ความได้เปรียบเล็ก และยังแยกจากศูนย์ไม่ได้**

**ค่าคงที่ทุกตัวถูกล็อกด้วยการ pre-register** — เปลี่ยนตัวใดตัวหนึ่ง = สูตรใหม่ที่ต้องทดสอบใหม่
บนข้อมูลที่ยังไม่เคยเปิดดู ห้ามจูนจากผลของพอร์ต DAR ที่รันอยู่

กติกาความซื่อตรงของโปรเจกต์ใช้ครบ: ตัวเลขทุกตัวคำนวณในโค้ด (ไม่มี LLM) · ดึงราคาไม่ได้ = โยน
``PriceDataUnavailableError`` ไม่เดาน้ำหนัก · กองที่ประวัติไม่ถึง 181 เดือน (นับรวมกองพี่ใน
``analysis.proxy_history.PROXY_MAP``) ได้ ``z = 0`` พร้อมเหตุผล ไม่ใช่สัญญาณที่เดาขึ้น
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from analysis.proxy_history import PROXY_MAP
from data.fetcher import PriceDataUnavailableError
from portfolio.fees import DIME_FEE_RATE

# --- ค่าคงที่ที่ล็อกไว้ (PREREG.md + PREREG_v2.md ใน research/dar_dca/) ---
HISTORY_MONTHS = 181          # ต้องมี L(180)
AMP_FROM, AMP_TO = 54, 66     # สิ้นเดือนที่ 54..66 ก่อน (รวมทั้งสองปลาย) = จุดอ้างอิง 5 ปี
DRIFT_COEF = 0.5              # 5 ปี / 10 ปี — พีชคณิต ไม่ได้ฟิต
K = 1.0                       # ความแรงการเอียง
FLOOR_FRAC = 0.2              # ทุกกอง ≥ 20% ของส่วนเท่ากัน (กติกา "ซื้อทุกกองทุกเดือน")
SD_MIN = 0.15                 # กันกองที่เกือบเหมือนกันถูกเอียงเพราะสัญญาณรบกวน
CAP_MULT = 1.5                # ไม่มีกองไหนเกิน 1.5 เท่าของส่วนเท่ากัน (รุ่น 2)

# ดึงย้อนหลังพอให้มี 181 สิ้นเดือน + เผื่อวันหยุด/เดือนที่ยังไม่ปิด
FETCH_YEARS = 17

ALLOCATION_UNIT_THB = 100     # ปัดเงินเป็นหลักร้อย (หน่วยเดียวกับแผน DCA หลัก)

# --- พอร์ต DAR: รายชื่อกอง + งบต่อเดือน — ตายตัวในโค้ด ไม่อ่านจาก config.json (มติผู้ใช้ 2026-09-30) ---
# สูตรไม่เลือกกองเอง มันแบ่งเงินให้ทุกกองในรายชื่อนี้ (PREREG รอบ 1: "the user picks the funds")
# ไม่ตาม etf.tickers / dca.monthly_budget_thb ของแผน ERC และไม่มีฟอร์มตั้งค่าในหน้า DAR: เปลี่ยนรายชื่อ
# หลังเห็นผลไม่กี่เดือนทำให้การทดลองเสีย การเปลี่ยนจึงต้องเป็นการแก้โค้ดโดยตั้งใจเท่านั้น
TICKERS: tuple[str, ...] = ("VOO", "SCHD", "QQQM", "XLV", "GLDM")
MONTHLY_BUDGET_THB = 5000.0

# ป้ายสถานะของสัญญาณ — เกณฑ์ ±0.5 z ใช้แค่เลือก "คำ" บนหน้าจอ ไม่มีผลกับน้ำหนัก
_LABEL_Z = 0.5


# ----------------------------------------------------------------------------- math
def floor_project(w: np.ndarray, floor: float) -> np.ndarray:
    """ทำให้รวมเป็น 1 และทุกตัว ≥ ``floor`` โดยคงอัตราส่วนของตัวที่อยู่เหนือพื้นไว้ (water-filling)."""
    w = np.maximum(np.asarray(w, dtype=float), 0.0)
    n = len(w)
    if n == 0:
        return w
    if w.sum() <= 0:
        return np.full(n, 1.0 / n)
    w = w / w.sum()
    fixed = np.zeros(n, dtype=bool)
    out = w
    for _ in range(n):
        budget = 1.0 - floor * fixed.sum()
        free = ~fixed
        fw = np.where(free, w, 0.0)
        s = fw.sum()
        scaled = fw / s * budget if s > 0 else np.where(free, budget / free.sum(), 0.0)
        out = np.where(fixed, floor, scaled)
        low = free & (out < floor - 1e-15)
        if not low.any():
            break
        fixed |= low
    return out


def cap_project(w: np.ndarray, cap: float) -> np.ndarray:
    """ตัดตัวที่เกิน ``cap`` แล้วแจกส่วนเกินให้ตัวที่ยังต่ำกว่าเพดานตามสัดส่วนน้ำหนัก (ทำซ้ำจนไม่มีใครเกิน)."""
    w = np.asarray(w, dtype=float).copy()
    for _ in range(len(w)):
        over = w > cap + 1e-15
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        room = (~over) & (w < cap - 1e-15)
        share = np.where(room, w, 0.0)
        if share.sum() > 0:
            w = w + share / share.sum() * excess
    return w / w.sum()


# ----------------------------------------------------------------------------- signals
@dataclass(frozen=True)
class FundSignal:
    """สัญญาณของกองหนึ่ง ณ เดือนที่ซื้อ — ทุกช่องมาจากราคา ไม่มีค่าเดา."""

    ticker: str
    months: int                      # จำนวนสิ้นเดือนที่มี (รวมประวัติของกองพี่)
    amp: float | None
    old: float | None
    dar: float | None
    ret_5y_pct: float | None         # ผลตอบแทน % จากจุดอ้างอิง 5 ปีถึงล่าสุด
    ret_prior10y_pct: float | None   # ผลตอบแทน % จาก 15 ปีก่อนถึง 5 ปีก่อน
    z: float
    weight: float
    neutral_reason: str | None       # มีค่า = ได้ส่วนกลาง (z = 0) และบอกเหตุผล
    sibling: str | None = None       # กองพี่ที่ใช้ยืดประวัติ (ถ้ามี)
    history_start: str | None = None # สิ้นเดือนแรกที่ใช้

    @property
    def label(self) -> str:
        if self.neutral_reason:
            return "กลาง (ประวัติยังไม่พอ)"
        if self.z >= _LABEL_Z:
            return "ตามหลังผิดปกติ → ซื้อเพิ่ม"
        if self.z <= -_LABEL_Z:
            return "นำผิดปกติ → ซื้อน้อยลง"
        return "ใกล้ค่ากลาง"


def _signal_parts(series: pd.Series) -> tuple[float, float, float, float, float] | None:
    s = series.dropna()
    s = s[s > 0]
    if len(s) < HISTORY_MONTHS:
        return None
    values = s.to_numpy(dtype=float)
    lp = np.log(values)
    ref_level = float(np.mean(values[len(values) - 1 - AMP_TO : len(values) - AMP_FROM]))
    amp = math.log(ref_level) - float(lp[-1])
    old = float(lp[-1 - 60] - lp[-1 - 180])
    dar = amp + DRIFT_COEF * old
    ret_5y = (math.exp(-amp) - 1.0) * 100.0
    ret_prior = (math.exp(old) - 1.0) * 100.0
    return amp, old, dar, ret_5y, ret_prior


def dar_weights(
    month_end: pd.DataFrame,
    siblings: Mapping[str, str] | None = None,
) -> tuple[pd.Series, list[FundSignal]]:
    """น้ำหนักของเดือนที่ซื้อจากราคาสิ้นเดือน (แถวสุดท้าย = สิ้นเดือนก่อนเดือนที่ซื้อ).

    คืน ``(weights, signals)`` — ``weights`` รวมเป็น 1 เสมอ ทุกตัวอยู่ใน [0.2/N, 1.5/N]
    """
    tickers = [str(c) for c in month_end.columns]
    n = len(tickers)
    if n == 0:
        raise ValueError("ไม่มีกองให้คำนวณ — ต้องมีอย่างน้อย 1 กอง")
    siblings = dict(siblings or {})
    parts = {t: _signal_parts(month_end[t]) for t in tickers}
    have = [t for t in tickers if parts[t] is not None]
    z = {t: 0.0 for t in tickers}
    if len(have) >= 2:
        x = np.array([parts[t][2] for t in have], dtype=float)
        sd = max(float(x.std(ddof=0)), SD_MIN)
        for t, v in zip(have, (x - x.mean()) / sd):
            z[t] = float(v)
    raw = np.array([max(1.0 + K * z[t], 0.0) / n for t in tickers])
    w = floor_project(raw, FLOOR_FRAC / n)
    if CAP_MULT / n < 1.0:
        w = cap_project(w, CAP_MULT / n)
    weights = pd.Series(w, index=tickers, dtype=float)

    signals: list[FundSignal] = []
    for t in tickers:
        s = month_end[t].dropna()
        p = parts[t]
        if p is None:
            reason = f"มีประวัติ {len(s)} เดือน ต้องการ {HISTORY_MONTHS}"
            signals.append(
                FundSignal(t, int(len(s)), None, None, None, None, None, 0.0, float(weights[t]), reason,
                           siblings.get(t), s.index[0].strftime("%Y-%m") if len(s) else None)
            )
            continue
        amp, old, dar, r5, r10 = p
        reason = None if len(have) >= 2 else "มีกองที่มีสัญญาณไม่ถึง 2 กอง — แบ่งเท่ากัน"
        signals.append(
            FundSignal(t, int(len(s)), amp, old, dar, r5, r10, z[t], float(weights[t]), reason,
                       siblings.get(t), s.index[0].strftime("%Y-%m"))
        )
    return weights, signals


# ----------------------------------------------------------------------------- data
FetchFn = Callable[..., pd.DataFrame]

FETCH_ATTEMPTS = 3
_RETRY_SLEEP_SEC = 2.0


def fetch_total_return_history(tickers: Sequence[str], years: int) -> pd.DataFrame:
    """ราคาปิดปรับปันผล (total return) รายวันต่อกอง ผ่าน ``yf.Ticker(...).history`` **ทีละกอง**.

    **ห้ามเปลี่ยนเป็น ``yf.download``** — download() เก็บผลในตัวแปรกลางของ yfinance
    (``shared._DFS``) แล้ววนรอจนจำนวนครบ ถ้าอีกเธรดใน process เดียวกันเรียก download พร้อมกัน
    ตัวแปรนั้นถูกล้างทับ แล้วทั้งสองฝั่งรอไม่มีวันจบ · เจอจริง 2026-09-30: กดเมนู DAR-DCA ขณะหน้า
    Overview ยังดาวน์โหลดราคาอยู่ หน้าค้างที่ "กำลังคำนวณแผน" จน restart dashboard (และแคชของ
    Streamlit ทำให้ทุก session ถัดมารอตามไปด้วย) · ``history()`` ไม่แตะตัวแปรกลางนั้น
    สัญญาเดียวกับ ``data.fetcher``: ลอง ``FETCH_ATTEMPTS`` ครั้งต่อกอง แล้วโยน ``PriceDataUnavailableError``
    """
    import time

    import yfinance as yf

    end = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize() + pd.Timedelta(days=1)
    start = end - pd.DateOffset(years=int(years))
    frames: dict[str, pd.Series] = {}
    for ticker in dict.fromkeys(str(t).strip().upper() for t in tickers if str(t).strip()):
        last_error: Exception | None = None
        for attempt in range(FETCH_ATTEMPTS):
            try:
                hist = yf.Ticker(ticker).history(
                    start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), auto_adjust=True, actions=False
                )
                close = pd.to_numeric(hist["Close"], errors="coerce").dropna() if "Close" in hist else pd.Series(dtype=float)
                close = close[close > 0]
                if close.empty:
                    raise ValueError("ไม่มีราคาในช่วงที่ขอ")
                index = pd.DatetimeIndex(close.index)
                if index.tz is not None:
                    index = index.tz_localize(None)
                frames[ticker] = pd.Series(close.to_numpy(dtype=float), index=index.normalize())
                break
            except Exception as exc:  # noqa: BLE001 - นับทุกความล้มเหลว แล้วล้มดังเมื่อครบรอบ
                last_error = exc
                if attempt < FETCH_ATTEMPTS - 1:
                    time.sleep(_RETRY_SLEEP_SEC)
        else:
            raise PriceDataUnavailableError(
                f"ดึงราคาของ {ticker} ไม่สำเร็จหลังลอง {FETCH_ATTEMPTS} ครั้ง: {last_error}"
            ) from last_error
    if not frames:
        raise PriceDataUnavailableError("ไม่มีกองให้ดึงราคา")
    return pd.DataFrame(frames).sort_index()


def plan_month_of(now: datetime | pd.Timestamp | None = None) -> pd.Period:
    """เดือนของแผน = เดือนปฏิทินปัจจุบัน (ใช้เฉพาะสิ้นเดือนที่ปิดแล้วก่อนหน้า)."""
    ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="Asia/Bangkok")
    return pd.Period(year=ts.year, month=ts.month, freq="M")


def load_month_end_history(
    tickers: Sequence[str],
    plan_month: pd.Period,
    fetch: FetchFn = fetch_total_return_history,
) -> tuple[pd.DataFrame, dict[str, str], pd.DataFrame]:
    """ราคาสิ้นเดือน (รวมปันผล) **ก่อน** ``plan_month`` + ตารางกองพี่ที่ใช้ + ราคารายวันดิบที่ดึงมา.

    กองที่ลิสต์ทีหลังและมีกองพี่ใน ``PROXY_MAP`` ถูกต่อประวัติ โดย **ปรับระดับที่วันเชื่อม**
    (ผลตอบแทนวันเชื่อมเป็นของกองจริง ไม่มีวันกระโดดปลอม — กติกาเดียวกับ proxy_history)
    ดึงไม่ได้/กองไหนไม่มีราคาเลย → ``PriceDataUnavailableError`` (ห้ามคำนวณแผนจากข้อมูลขาด)
    """
    tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        raise ValueError("ไม่มีกองให้คำนวณ")
    siblings = {t: PROXY_MAP[t] for t in tickers if t in PROXY_MAP and PROXY_MAP[t] not in tickers}
    need = list(dict.fromkeys(tickers + sorted(set(siblings.values()))))
    daily = fetch(need, years=FETCH_YEARS)
    if daily is None or daily.empty:
        raise PriceDataUnavailableError(f"ไม่ได้ราคาของ {need} เลย")
    daily = daily.sort_index()
    cutoff = plan_month.start_time
    frames: dict[str, pd.Series] = {}
    used: dict[str, str] = {}
    for t in tickers:
        if t not in daily.columns or daily[t].dropna().empty:
            raise PriceDataUnavailableError(f"ไม่มีราคาของ {t} — คำนวณแผน DAR ไม่ได้")
        own = daily[t].dropna()
        sib = siblings.get(t)
        if sib and sib in daily.columns and not daily[sib].dropna().empty:
            s = daily[sib].dropna()
            join = own.index[0]
            s_at = s.loc[:join]
            if len(s_at) and s.index[0] < join:
                scale = float(own.iloc[0]) / float(s_at.iloc[-1])
                own = pd.concat([s.loc[s.index < join] * scale, own])
                used[t] = sib
        me = own.resample("ME").last().dropna()
        frames[t] = me[me.index < cutoff]
    month_end = pd.DataFrame(frames)
    return month_end, used, daily


# ----------------------------------------------------------------------------- money
def round_to_units(weights: pd.Series, budget_thb: float, unit: int = ALLOCATION_UNIT_THB) -> dict[str, int]:
    """เงินต่อกองเป็นจำนวนเต็มหลัก ``unit`` บาท รวมพอดีงบ (largest remainder) ทุกกอง ≥ 1 หน่วย.

    งบที่ไม่ลงตัวหลักร้อย เศษถูกบอกเป็น "ยังไม่จัดสรร" โดยผู้เรียก ไม่ถูกยัดเข้ากองใด
    """
    if not math.isfinite(float(budget_thb)) or budget_thb <= 0:
        raise ValueError(f"งบต่อเดือนต้องเป็นจำนวนบวก (ได้ {budget_thb!r})")
    n = len(weights)
    units_total = int(float(budget_thb) // unit)
    if units_total < n:
        raise ValueError(
            f"งบ {budget_thb:,.0f} บาทน้อยเกินกว่าจะซื้อครบ {n} กองกองละ {unit} บาท"
        )
    raw = weights.to_numpy(dtype=float) * units_total
    base = np.maximum(np.floor(raw).astype(int), 1)
    # ถ้าการยกขั้นต่ำ 1 หน่วยทำให้เกิน ให้ดึงคืนจากกองที่ได้เกินส่วนมากที่สุด
    while base.sum() > units_total:
        over = raw - base
        candidates = np.where(base > 1)[0]
        j = candidates[np.argmin(over[candidates])]
        base[j] -= 1
    remainder = units_total - int(base.sum())
    if remainder > 0:
        frac = raw - base
        for j in np.argsort(-frac, kind="stable")[:remainder]:
            base[j] += 1
    return {t: int(b) * unit for t, b in zip(weights.index, base)}


@dataclass
class DarPlanLine:
    ticker: str
    amount_thb: int
    weight: float
    price_usd: float | None          # ราคาปิดล่าสุด (โดยประมาณราคาที่จะซื้อ)
    price_date: str | None
    units: float | None              # ≈ หน่วยหลังหักค่าธรรมเนียม
    signal: FundSignal


@dataclass
class DarPlan:
    plan_month: str                  # "2026-10"
    data_through: str | None         # สิ้นเดือนล่าสุดที่ใช้คำนวณ
    budget_thb: float
    unallocated_thb: float
    fx_rate: float
    fx_is_live: bool
    lines: list[DarPlanLine]
    siblings: dict[str, str] = field(default_factory=dict)
    generated_at: str = ""

    @property
    def total_thb(self) -> int:
        return int(sum(line.amount_thb for line in self.lines))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_month": self.plan_month,
            "data_through": self.data_through,
            "budget_thb": self.budget_thb,
            "unallocated_thb": self.unallocated_thb,
            "fx_rate": self.fx_rate,
            "fx_is_live": self.fx_is_live,
            "siblings": dict(self.siblings),
            "lines": [
                {
                    "ticker": ln.ticker,
                    "amount_thb": ln.amount_thb,
                    "weight": ln.weight,
                    "price_usd": ln.price_usd,
                    "price_date": ln.price_date,
                    "units": ln.units,
                    "label": ln.signal.label,
                    "z": ln.signal.z,
                    "dar": ln.signal.dar,
                    "ret_5y_pct": ln.signal.ret_5y_pct,
                    "ret_prior10y_pct": ln.signal.ret_prior10y_pct,
                    "months": ln.signal.months,
                    "neutral_reason": ln.signal.neutral_reason,
                }
                for ln in self.lines
            ],
        }


def estimate_units(amount_thb: float, fx_rate: float, price_usd: float | None) -> float | None:
    """≈ หน่วยที่ได้: บาท → ดอลลาร์ → หักค่าธรรมเนียม Dime → หารราคา (ราคาอ่านไม่ได้ = ``None``)."""
    if price_usd is None or not math.isfinite(price_usd) or price_usd <= 0:
        return None
    if not math.isfinite(fx_rate) or fx_rate <= 0:
        return None
    usd = float(amount_thb) / float(fx_rate)
    return usd / (1.0 + DIME_FEE_RATE) / float(price_usd)


def build_plan(
    tickers: Sequence[str],
    budget_thb: float,
    plan_month: pd.Period | None = None,
    *,
    fetch: FetchFn = fetch_total_return_history,
    fx_fn: Callable[[], Any] | None = None,
    now: datetime | None = None,
) -> DarPlan:
    """แผนซื้อของเดือน: กองไหน กี่บาท ≈ กี่หน่วย — ตัวเลขทั้งหมดจากโค้ด."""
    if fx_fn is None:
        from utils.fx import get_usdthb as fx_fn  # noqa: PLC0415 - โหลดเมื่อใช้ (ยิงเน็ต)
    month = plan_month or plan_month_of(now)
    month_end, used, daily = load_month_end_history(tickers, month, fetch)
    weights, signals = dar_weights(month_end, used)
    amounts = round_to_units(weights, budget_thb)
    fx = fx_fn()
    rate = float(getattr(fx, "rate", fx))
    is_live = bool(getattr(fx, "is_live", True))
    lines: list[DarPlanLine] = []
    by_ticker = {s.ticker: s for s in signals}
    for t in weights.index:
        col = daily[t].dropna() if t in daily.columns else pd.Series(dtype=float)
        price = float(col.iloc[-1]) if len(col) else None
        pdate = col.index[-1].strftime("%Y-%m-%d") if len(col) else None
        lines.append(
            DarPlanLine(t, amounts[t], float(weights[t]), price, pdate,
                        estimate_units(amounts[t], rate, price), by_ticker[t])
        )
    # วันที่ของราคาจริงล่าสุดที่ใช้ ไม่ใช่ป้ายสิ้นเดือน — resample("ME") ติดป้าย "30 ก.ย." ให้ทั้งที่
    # แท่งสุดท้ายอาจเป็นวันที่ 29 (เดือนยังไม่ปิด) ป้ายที่เกินความจริงคือตัวเลขที่กุขึ้น
    used_cols = [t for t in weights.index if t in daily.columns]
    recent = daily.loc[daily.index < month.start_time, used_cols].dropna(how="all")
    data_through = recent.index.max().strftime("%Y-%m-%d") if len(recent) else None
    return DarPlan(
        plan_month=str(month),
        data_through=data_through,
        budget_thb=float(budget_thb),
        unallocated_thb=float(budget_thb) - float(sum(amounts.values())),
        fx_rate=rate,
        fx_is_live=is_live,
        lines=lines,
        siblings=used,
        generated_at=(pd.Timestamp(now) if now else pd.Timestamp.now(tz="Asia/Bangkok")).isoformat(timespec="seconds"),
    )
