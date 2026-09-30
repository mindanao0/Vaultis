# -*- coding: utf-8 -*-
"""สัดส่วนฐานแบบ ERC (Equal Risk Contribution) — ทุกกองแบกความเสี่ยงของพอร์ตเท่ากัน.

ที่มา: Maillard, Roncalli & Teïletche (2010) "The Properties of Equally Weighted Risk
Contribution Portfolios", *Journal of Portfolio Management* 36(4) · วิธีแก้สมการแบบ cyclical
coordinate descent ตาม Griveau-Billion, Richard & Roncalli (2013)

ทำไมเปลี่ยนมาใช้สูตรนี้ (มติผู้ใช้ 2026-09-30): ผู้ใช้เลิกใช้สัดส่วนตายตัว 35/25/20/10/10
เพราะ ETF ทั้งห้าตัวกระจายความเสี่ยงอยู่แล้วและขึ้นลงตามตลาดเป็นหลัก — สูตรต้องคิดเรื่อง
"การขึ้นลงพร้อมกัน" ไม่ใช่ดูทีละกอง วัดจริง 15 ปี: VOO–QQQ 0.93, VOO–SCHD 0.88,
VOO–XLV 0.78, ทองกับทุกตัว 0.05–0.09 และด้วยสัดส่วนเดิม VOO + QQQM แบกความเสี่ยง
ของพอร์ต 66% ทั้งที่เป็นเดิมพันเกือบก้อนเดียวกัน ERC ให้กองที่ขึ้นลงพร้อมกันแชร์งบ
ความเสี่ยงก้อนเดียว และทุกกองได้น้ำหนักเป็นบวกเสมอ (นโยบาย "ซื้อทุกกองทุกเดือน")

backtest ด้วย harness เดียวกับ ``portfolio/ab_backtest.py`` (point-in-time, 2026-09-30):
ความผันผวนและการร่วงลดลงทั้งสองช่วง (14 ปี: MDD −20.9% → −17.4%, 5 ปี: −19.2% → −14.8%)
ส่วนผลตอบแทนต่างจากสัดส่วนเดิมแบบ **แยกไม่ออกทางสถิติ** (14 ปี −1.0%/ปี, 5 ปี +0.9%/ปี)
— นี่คือสูตรกระจายความเสี่ยง ไม่ใช่สูตรเพิ่มผลตอบแทน

ข้อจำกัดที่ต้องรู้ (อย่าลบทิ้ง):

* ERC มองแต่ละ ETF เป็นสินทรัพย์แยกกัน ไม่รู้ว่า VOO ถือหุ้นของกองอื่นอยู่ข้างใน
  VOO ขึ้นลงพร้อมทุกกองมากที่สุด จึงได้น้ำหนักน้อยกว่า SCHD (วันที่ตัดสินใจ: SCHD 27% · VOO 21%)
* ทองไม่ขึ้นลงตามหุ้น ช่วงที่ทองนิ่ง น้ำหนักทองขึ้นไปได้ถึง ~50% — **เฉลี่ยทั้งช่วง ~30%**
  (backtest วัดเป็นบาท 14 ปี: ทองเฉลี่ย 28.8%) วันที่เปลี่ยนสูตรเป็น 16%
* ERC ไม่เห็นหุ้นข้างในกอง: XLV (สุขภาพ 100%) + SCHD (สุขภาพ 21.5%) ทำให้หุ้นสุขภาพเป็น
  ~34% ของส่วนหุ้น ขณะที่ตลาดโลก (VT) มี 8.6% — ``lookthrough.sector_concentration`` เตือนเรื่องนี้

วัดเป็นเงินบาท (2026-09-30): ความเสี่ยงคิดจากราคา × USDTHB เพราะผู้ใช้วัดผลเป็นบาท บาทมักแข็ง
ตอนหุ้น/ทองขึ้นและอ่อนตอนตลาดร่วง จึงหักล้างกันบางส่วน — backtest เทียบกับวัดเป็น USD
ต่างกันแค่ −0.08 ถึง +0.16%/ปี (CI แคบคร่อมศูนย์)

วิธี ``erc_sector_cap`` (เลือกได้ ไม่ใช่ค่าเริ่มต้น): ERC + เพดาน "เซกเตอร์ใดในส่วนหุ้นหนักได้ไม่เกิน
2 เท่าของตลาดโลก" — เกณฑ์ผ่านล็อกไว้ก่อนรัน backtest (ความผันผวนและ drawdown ต้องไม่แย่กว่า
ERC เกิน 1 จุดทั้งสองช่วง) ผล: **ไม่ผ่าน** — หุ้นสุขภาพลดจาก ~37% เหลือ 17.2% ของส่วนหุ้นตามตั้งใจ
แต่ drawdown แย่ลง ~3 จุดทั้งสองช่วง (14 ปี −10.9% → −14.0%, 5 ปี −6.0% → −9.0%) เพราะเงินที่
ตัดจาก XLV ไหลไป VOO/QQQM ที่ขึ้นลงพร้อมกัน ผลตอบแทน +0.8%/ปี แยกไม่ออกทางสถิติ
(ข้อจำกัดของการทดสอบ: มีสัดส่วนเซกเตอร์ของกองแค่ของวันนี้ ใช้คงที่ตลอดช่วง)

ตัวเลขทุกตัวคำนวณในโค้ด (AI อธิบายเท่านั้น) · ข้อมูลไม่พอ = raise ห้ามเดาน้ำหนักแทน (C1)
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from utils.cache import cache_data_1h

#: ความผันผวน = 1 ปีล่าสุด (ความเสี่ยง "ตอนนี้")
VOL_WINDOW_BARS = 252
#: correlation = ย้อนหลังสูงสุด ~5 ปี (โครงสร้างระยะยาว) — 1 ปีล่าสุดผิดปกติเกินจะใช้ลำพัง:
#: SCHD–VOO 0.30 / XLV–VOO 0.23 ต่ำสุดในรอบ 15 ปี (ค่าเฉลี่ย 0.87 / 0.76)
CORR_WINDOW_BARS = 1260
#: ผลตอบแทนรายวันขั้นต่ำที่ทุกกองต้องมีร่วมกัน — น้อยกว่านี้ประมาณความเสี่ยงไม่ได้
MIN_BARS = 252
#: ดึงเผื่อวันหยุดให้ได้ ≥ CORR_WINDOW_BARS แท่ง
HISTORY_YEARS = 6
#: USDTHB รายวันจาก Yahoo — ใช้แปลงราคาเป็นบาทก่อนวัดความเสี่ยง
FX_TICKER = "THB=X"
TRADING_DAYS_PER_YEAR = 252

_MAX_SWEEPS = 10_000
_STEP_TOL = 1e-13
#: ส่วนแบ่งความเสี่ยงของแต่ละกองห่างจาก 1/n ได้ไม่เกินนี้ ไม่งั้นถือว่าแก้สมการไม่สำเร็จ
_RC_TOL = 1e-6


def erc_weights(cov: np.ndarray) -> np.ndarray:
    """น้ำหนักที่ทำให้ส่วนแบ่งความเสี่ยงของทุกกองเท่ากัน (รวมเป็น 1, ทุกตัว > 0).

    ส่วนแบ่งความเสี่ยงของกอง i = w_i · (Σw)_i ÷ w'Σw — ERC ตั้งให้เท่ากับ 1/n ทุกกอง
    แก้ด้วยปัญหา convex ``min ½ y'Σy − (1/n)·Σ ln y_i`` ซึ่งคำตอบ normalize แล้วคือ ERC
    พิกัดละตัว: σ_ii·y_i² + c_i·y_i − 1/n = 0  →  รากบวกของสมการกำลังสอง (ปิดรูปเป๊ะ)

    covariance ที่ใช้ไม่ได้ (ไม่จัตุรัส/ไม่สมมาตร/มีค่าไม่จำกัด/ความแปรปรวน ≤ 0) หรือแก้สมการ
    ไม่ลู่เข้า → ``ValueError`` — ห้ามคืนน้ำหนักที่ไม่ผ่านการตรวจ
    """
    cov = np.asarray(cov, dtype=float)
    if cov.ndim != 2 or cov.shape[0] != cov.shape[1] or cov.shape[0] == 0:
        raise ValueError(f"covariance ต้องเป็นเมทริกซ์จัตุรัส (ได้ {cov.shape})")
    if not np.all(np.isfinite(cov)):
        raise ValueError("covariance มีค่า NaN/inf — ประมาณความเสี่ยงไม่ได้")
    if not np.allclose(cov, cov.T, rtol=1e-9, atol=1e-12):
        raise ValueError("covariance ไม่สมมาตร")
    diag = np.diag(cov)
    if np.any(diag <= 0):
        raise ValueError("มีกองที่ความแปรปรวน ≤ 0 — ราคาไม่ขยับเลย ประมาณความเสี่ยงไม่ได้")

    n = cov.shape[0]
    budget = 1.0 / n
    y = 1.0 / np.sqrt(diag)  # เริ่มจาก inverse-vol
    for _ in range(_MAX_SWEEPS):
        largest_step = 0.0
        for i in range(n):
            c = float(cov[i] @ y - cov[i, i] * y[i])
            new = (-c + math.sqrt(c * c + 4.0 * cov[i, i] * budget)) / (2.0 * cov[i, i])
            largest_step = max(largest_step, abs(new - y[i]) / max(abs(y[i]), 1e-300))
            y[i] = new
        if largest_step < _STEP_TOL:
            break

    weights = y / y.sum()
    shares = risk_contributions(cov, weights)
    if not np.all(weights > 0) or np.max(np.abs(shares - budget)) > _RC_TOL:
        raise ValueError(
            "แก้สมการ ERC ไม่ลู่เข้า (ส่วนแบ่งความเสี่ยงไม่เท่ากัน) — ไม่คืนน้ำหนักที่ไม่ผ่านการตรวจ"
        )
    return weights


def erc_weights_capped(
    cov: np.ndarray, exposures: np.ndarray, caps: np.ndarray, basis: np.ndarray | None = None
) -> tuple[np.ndarray, list[int]]:
    """ERC ภายใต้เพดานเซกเตอร์ — ปัญหาเดียวกับ :func:`erc_weights` บวกข้อจำกัดเชิงเส้น.

    ``exposures`` (S×n): สัดส่วนเซกเตอร์ s ในกอง i · ``caps`` (S): เพดานของแต่ละเซกเตอร์
    ``basis`` (n): ส่วนของกองที่นับเป็นฐานของเพดาน — ค่าเริ่มต้นคือทั้งกอง (1)
    ผู้เรียกส่ง "สัดส่วนหุ้นในกอง" มาเพื่อวัดเพดาน **ภายในส่วนหุ้น** (ทองไม่มีเซกเตอร์ ถ้านับ
    เป็นฐานด้วย ทองที่เพิ่มขึ้นจะเจือจางตัวเลขจนความกระจุกตัวของหุ้นหายไปจากสายตา)
    แก้ ``min ½ y'Σy − (1/n)·Σ ln y_i`` ภายใต้ ``(cap_s·basis − E_s)·y ≥ 0`` ซึ่งยัง convex
    (risk budgeting แบบมีข้อจำกัด — Roncalli 2013) · ไม่มีเพดานไหนถูกชน = ได้ ERC เป๊ะ
    คืน ``(weights, ดัชนีเซกเตอร์ที่ชนเพดาน)`` · แก้ไม่ได้/ผิดเงื่อนไข → ``ValueError``
    """
    base = erc_weights(cov)
    exposures = np.asarray(exposures, dtype=float)
    caps = np.asarray(caps, dtype=float)
    basis = np.ones(base.size) if basis is None else np.asarray(basis, dtype=float)
    if (
        exposures.ndim != 2
        or exposures.shape[1] != base.size
        or exposures.shape[0] != caps.size
        or basis.shape != (base.size,)
    ):
        raise ValueError("ขนาดของ exposures/caps/basis ไม่ตรงกับจำนวนกอง")
    constraint = caps[:, None] * basis[None, :] - exposures  # ผลรวมถ่วงด้วย y ต้อง ≥ 0
    if np.all(constraint @ base >= -1e-12):
        return base, []

    from scipy.optimize import minimize

    cov = np.asarray(cov, dtype=float)
    n = base.size
    budget = 1.0 / n
    start = base / math.sqrt(float(base @ cov @ base))
    result = minimize(
        lambda y: 0.5 * y @ cov @ y - budget * np.sum(np.log(y)),
        start,
        jac=lambda y: cov @ y - budget / y,
        bounds=[(1e-12, None)] * n,
        constraints=[{"type": "ineq", "fun": lambda y: constraint @ y, "jac": lambda y: constraint}],
        method="SLSQP",
        options={"ftol": 1e-15, "maxiter": 5000},
    )
    weights = result.x / result.x.sum()
    slack = constraint @ weights
    if not result.success or not np.all(weights > 0) or np.any(slack < -1e-6):
        raise ValueError(
            "หาสัดส่วนที่ทั้งกระจายความเสี่ยงและอยู่ใต้เพดานเซกเตอร์ไม่ได้ "
            f"({result.message}) — ไม่คืนน้ำหนักที่ไม่ผ่านการตรวจ"
        )
    binding = [s for s in range(caps.size) if slack[s] < 1e-6]
    return weights, binding


def risk_contributions(cov: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """ส่วนแบ่งความเสี่ยงของพอร์ตที่แต่ละกองแบก (รวมเป็น 1)."""
    cov = np.asarray(cov, dtype=float)
    weights = np.asarray(weights, dtype=float)
    contrib = weights * (cov @ weights)
    return contrib / contrib.sum()


def estimate_covariance(prices: pd.DataFrame) -> tuple[np.ndarray, dict[str, Any]]:
    """covariance รายปีจากราคา: ความผันผวน 1 ปีล่าสุด × correlation ย้อนหลังสูงสุด ~5 ปี.

    ใช้เฉพาะวันที่ **ทุกกองมีราคาจริง** (``dropna`` ไม่ ``ffill`` — เติมราคาเท่ากับสร้างวันที่
    ผลตอบแทน 0% ขึ้นมาเอง กดความผันผวนและ correlation ให้ผิดจริง)
    คืน ``(cov, meta)`` — ``meta`` บอกช่วงข้อมูลที่ใช้จริง หน้าจอต้องแสดงได้
    """
    frame = prices.sort_index()
    returns = frame.pct_change(fill_method=None).iloc[1:].dropna(how="any")
    if len(returns) < MIN_BARS:
        raise ValueError(
            f"ผลตอบแทนรายวันที่ทุกกองมีร่วมกันมีแค่ {len(returns)} วัน "
            f"(ต้องมีอย่างน้อย {MIN_BARS}) — ประมาณความเสี่ยงไม่ได้"
        )
    corr_part = returns.tail(CORR_WINDOW_BARS)
    vol_part = returns.tail(VOL_WINDOW_BARS)
    vol = vol_part.std(ddof=1).to_numpy() * math.sqrt(TRADING_DAYS_PER_YEAR)
    corr = corr_part.corr().to_numpy()
    cov = np.outer(vol, vol) * corr
    meta = {
        "vol_window": _window(vol_part),
        "corr_window": _window(corr_part),
        "vol_pct": {str(t): round(float(v) * 100.0, 2) for t, v in zip(returns.columns, vol)},
    }
    return cov, meta


def _window(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "start": pd.Timestamp(frame.index[0]).strftime("%Y-%m-%d"),
        "end": pd.Timestamp(frame.index[-1]).strftime("%Y-%m-%d"),
        "days": int(len(frame)),
    }


def erc_from_prices(
    prices: pd.DataFrame, tickers: list[str], sector_inputs: dict[str, Any] | None = None
) -> dict[str, Any]:
    """ERC ของ ``tickers`` จากเฟรมราคา — ตัวที่ไม่มีคอลัมน์/ไม่มีราคาเลย = raise ห้ามข้าม.

    ``sector_inputs`` (จาก :func:`sector_cap_inputs`) = ใช้ ERC แบบมีเพดานเซกเตอร์
    คืน weights · risk_share (ส่วนแบ่งความเสี่ยง — เท่ากันหมดเมื่อไม่มีเพดานชน เพราะนั่นคือนิยาม)
    · risk_per_pct (ความเสี่ยงที่เพิ่มต่อเงิน 1% — ตัวที่อธิบายว่า "ทำไมได้เงินไม่เท่ากัน") · meta
    """
    missing = [t for t in tickers if t not in prices.columns or prices[t].dropna().empty]
    if missing:
        raise ValueError(f"ไม่มีข้อมูลราคาของ {', '.join(missing)}")
    if len(tickers) == 1:
        only = tickers[0]
        return {"weights": {only: 1.0}, "risk_share": {only: 1.0}, "risk_per_pct": {}, "meta": {}}
    frame = prices[list(tickers)]
    cov, meta = estimate_covariance(frame)
    binding: list[str] = []
    if sector_inputs is not None:
        weights, hit = erc_weights_capped(
            cov, sector_inputs["exposures"], sector_inputs["caps"], sector_inputs["basis"]
        )
        binding = [sector_inputs["sectors"][i] for i in hit]
        meta = {
            **meta,
            "binding_sectors_th": [sector_inputs["labels"].get(s, s) for s in binding],
            "cap_multiple": sector_inputs["multiple"],
        }
    else:
        weights = erc_weights(cov)
    shares = risk_contributions(cov, weights)
    port_vol = math.sqrt(float(weights @ cov @ weights))
    marginal = (cov @ weights) / port_vol
    meta = {**meta, "portfolio_vol_pct": round(port_vol * 100.0, 2), "binding_sectors": binding}
    return {
        "weights": {t: float(w) for t, w in zip(tickers, weights)},
        "risk_share": {t: float(v) for t, v in zip(tickers, shares)},
        "risk_per_pct": {t: round(float(v) * 100.0, 2) for t, v in zip(tickers, marginal)},
        "meta": meta,
    }


def sector_cap_inputs(tickers: list[str]) -> dict[str, Any]:
    """เมทริกซ์สัดส่วนเซกเตอร์ของแต่ละกอง + เพดาน = ``CONCENTRATION_MULTIPLE`` × ตลาดโลก.

    เพดานวัด **ภายในส่วนหุ้น** (``basis`` = สัดส่วนหุ้นในกอง; ทองเป็น 0) — เหตุผลเดียวกับ
    ``lookthrough.sector_concentration`` · กองไหน/ตลาดโลกดึงไม่ได้ → ``ValueError`` (ใช้เพดาน
    ครึ่ง ๆ กลาง ๆ = เพดานที่ไม่รู้ว่ากันอะไรได้จริง)
    """
    from portfolio.lookthrough import CONCENTRATION_MULTIPLE, SECTOR_TH, WORLD_PROXY, _fund_data

    _h, world, error = _fund_data(WORLD_PROXY)
    if error or not world:
        raise ValueError(f"ดึงสัดส่วนเซกเตอร์ของตลาดโลก ({WORLD_PROXY}) ไม่ได้: {error or 'ไม่มีข้อมูล'}")
    sectors = sorted(str(k) for k in world)
    exposures = np.zeros((len(sectors), len(tickers)))
    for j, ticker in enumerate(tickers):
        _h, fund_sectors, error = _fund_data(ticker)
        if error:
            raise ValueError(f"ดึงสัดส่วนเซกเตอร์ของ {ticker} ไม่ได้: {error}")
        for i, sector in enumerate(sectors):
            exposures[i, j] = float((fund_sectors or {}).get(sector, 0.0) or 0.0)
    caps = np.array([CONCENTRATION_MULTIPLE * float(world[s]) for s in sectors])
    return {
        "sectors": sectors,
        "exposures": exposures,
        "caps": caps,
        "basis": exposures.sum(axis=0),
        "labels": {s: SECTOR_TH.get(s, s) for s in sectors},
        "multiple": CONCENTRATION_MULTIPLE,
    }


def to_thb(prices_usd: pd.DataFrame) -> pd.DataFrame:
    """ราคา USD → บาท ด้วย USDTHB รายวัน — ผู้ใช้วัดผลเป็นบาท ความเสี่ยงจึงต้องวัดเป็นบาท.

    วันที่ตลาด FX ไม่มีราคาแต่ตลาดหุ้นมี ใช้อัตราล่าสุดไม่เกิน 3 วัน (FX เปิด 5 วันเหมือนกัน
    ช่องว่างคือวันหยุดคนละประเทศ) เกินนั้นเป็น NaN แล้วถูกตัดทิ้งตอนประมาณ covariance
    ดึงไม่ได้ → ``PriceDataUnavailableError`` ให้ผู้เรียกตัดสินเอง
    """
    from data.fetcher import fetch_adjusted_close_data

    fx = fetch_adjusted_close_data(tickers=[FX_TICKER], years=HISTORY_YEARS)[FX_TICKER]
    return prices_usd.mul(fx.reindex(prices_usd.index).ffill(limit=3), axis=0)


def build_erc(tickers: list[str], sector_cap: bool = False) -> dict[str, Any]:
    """ดึงราคาแล้วคำนวณ ERC **ในรูปเงินบาท** (ไม่ cache — ผู้เรียกทั่วไปใช้ :func:`compute_erc_weights`).

    ``sector_cap=True`` = วิธี ``erc_sector_cap`` (เลือกได้ใน Settings — ไม่ใช่ค่าเริ่มต้น เพราะ
    backtest ที่ล็อกเกณฑ์ไว้ก่อนไม่ผ่าน: drawdown แย่ลง ~3 จุดทั้งสองช่วง — ดู docstring โมดูล)
    ดึง USDTHB ย้อนหลังไม่ได้ → คำนวณเป็น USD แล้ว **บอก** ใน ``meta["currency"]``/``meta["fx_error"]``
    (วัดเป็น USD ให้น้ำหนักแทบเท่ากัน: backtest ต่างกัน −0.08 ถึง +0.16%/ปี) ไม่ใช่ล้มทั้งแผน
    ราคากองดึงไม่ได้ → ``PriceDataUnavailableError`` · ข้อมูลเซกเตอร์ดึงไม่ได้ → ``ValueError``
    """
    from data.fetcher import PriceDataUnavailableError, fetch_adjusted_close_data

    symbols = list(tickers)
    prices = fetch_adjusted_close_data(tickers=symbols, years=HISTORY_YEARS)[symbols]
    currency, fx_error = "THB", ""
    try:
        prices = to_thb(prices)
    except PriceDataUnavailableError as exc:
        currency, fx_error = "USD", str(exc)
    sector_inputs = sector_cap_inputs(symbols) if sector_cap else None
    result = erc_from_prices(prices, symbols, sector_inputs)
    result["meta"] = {**result["meta"], "currency": currency, "fx_error": fx_error, "sector_cap": sector_cap}
    return result


@cache_data_1h
def compute_erc_weights(tickers: tuple[str, ...], sector_cap: bool = False) -> dict[str, Any]:
    """:func:`build_erc` แบบ cache 1 ชม. ต่อชุด ticker (ความล้มเหลวไม่ถูก cache ตามกติกา utils.cache)."""
    return build_erc(list(tickers), sector_cap)
