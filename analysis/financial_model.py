# -*- coding: utf-8 -*-
"""Financial modeling helpers for ETFs: 3-statement-style fundamentals + simplified DCF + scoring."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf

from analysis.returns import RETURN_WINDOWS, period_return_pct
from analysis.ta_compat import ta
from technical import signal_rules
from utils.cache import cache_data_1h


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_or_none(value: float, digits: int) -> float | None:
    """ปัดเศษ แต่ถ้าค่าเป็น ``NaN`` คืน ``None`` — "คำนวณไม่ได้" ต้องไม่กลายเป็นตัวเลข (C1)."""
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _close_series(hist: pd.DataFrame, ticker: str) -> pd.Series:
    if hist.empty:
        raise ValueError(f"No price history for {ticker}")
    if "Close" in hist.columns:
        close = hist["Close"]
    elif "Adj Close" in hist.columns:
        close = hist["Adj Close"]
    else:
        raise ValueError(f"No Close column for {ticker}")
    if isinstance(close, pd.DataFrame):
        if ticker in close.columns:
            s = close[ticker]
        else:
            s = close.iloc[:, 0]
    else:
        s = close
    return pd.to_numeric(s, errors="coerce").dropna().sort_index()


def _download_close(ticker: str, period: str) -> pd.Series:
    # auto_adjust=True: ราคา adjusted มาตรฐานเดียวทั้งระบบ (AUDIT.md M1)
    hist = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    return _close_series(hist, ticker)


def get_rsi(ticker: str, length: int = 14) -> float:
    close = _download_close(ticker, "1y")
    if len(close) < length + 1:
        raise ValueError(f"Not enough data for RSI ({ticker})")
    rsi_series = ta.rsi(close, length=length)
    return _safe_float(rsi_series.iloc[-1], 50.0)


def calculate_income_statement(ticker: str) -> dict[str, Any]:
    t = yf.Ticker(ticker)
    info = t.info or {}
    return {
        "dividend_yield": info.get("dividendYield", 0) or 0,
        "trailing_eps": info.get("trailingEps", 0) or 0,
        "revenue_growth": info.get("revenueGrowth", 0) or 0,
        "profit_margin": info.get("profitMargins", 0) or 0,
        "operating_margin": info.get("operatingMargins", 0) or 0,
        "return_on_equity": info.get("returnOnEquity", 0) or 0,
        "return_on_assets": info.get("returnOnAssets", 0) or 0,
    }


def calculate_balance_sheet(ticker: str) -> dict[str, Any]:
    t = yf.Ticker(ticker)
    info = t.info or {}
    total_assets = info.get("totalAssets", 0) or 0
    return {
        "price_to_book": info.get("priceToBook", 0) or 0,
        "debt_to_equity": info.get("debtToEquity", 0) or 0,
        "current_ratio": info.get("currentRatio", 0) or 0,
        "total_assets": total_assets,
        "nav": info.get("navPrice", 0) or 0,
        "aum": total_assets,
        "expense_ratio": info.get("annualReportExpenseRatio", 0) or 0,
    }


def calculate_cash_flow(ticker: str) -> dict[str, Any]:
    t = yf.Ticker(ticker)
    info = t.info or {}
    close = _download_close(ticker, "5y")
    annual_last = close.resample("YE").last()
    # fill_method=None (B11): ปีที่ไม่มีราคาเลยต้องเป็น NaN แล้วถูก dropna ทิ้ง
    # ไม่ใช่ ffill จนกลายเป็นผลตอบแทน 0.00% ที่ไปถ่วง 5y_avg_return / 5y_return_std
    annual_returns = annual_last.pct_change(fill_method=None).dropna()
    return {
        "dividend_per_share": info.get("dividendRate", 0) or 0,
        "5y_avg_return": _safe_float(annual_returns.mean(), 0.0),
        "5y_return_std": _safe_float(annual_returns.std(), 0.0),
        "free_cash_flow": info.get("freeCashflow", 0) or 0,
        "operating_cash_flow": info.get("operatingCashflow", 0) or 0,
    }


@cache_data_1h
def dcf_valuation(ticker: str, years: int = 10) -> dict[str, Any]:
    t = yf.Ticker(ticker)
    info = t.info or {}
    hist = yf.download(ticker, period="10y", progress=False, auto_adjust=False)
    close = _close_series(hist, ticker)

    try:
        current_price = _safe_float(t.fast_info["last_price"], 0.0)
    except Exception:
        current_price = _safe_float(close.iloc[-1], 0.0)
    if current_price <= 0:
        current_price = _safe_float(close.iloc[-1], 0.0)

    annual_last = close.resample("YE").last()
    annual_returns = annual_last.pct_change(fill_method=None).dropna()  # B11
    tail_mean = _safe_float(annual_returns.tail(3).mean(), 0.0) if len(annual_returns) else 0.0
    growth_rate_high = min(tail_mean, 0.12)
    growth_rate_high = max(growth_rate_high, 0.04)

    terminal_growth = 0.03
    risk_free_rate = 0.043
    equity_risk_premium = 0.065
    beta_raw = info.get("beta3Year") or info.get("beta") or 1.0
    beta = _safe_float(beta_raw, 1.0) or 1.0
    wacc = risk_free_rate + beta * equity_risk_premium

    if wacc - terminal_growth < 0.005:
        terminal_growth = max(0.01, wacc - 0.01)

    dividend = _safe_float(info.get("dividendRate"), 0.0)
    pe_ratio = _safe_float(info.get("trailingPE"), 0.0)
    if pe_ratio <= 0:
        # AUDIT.md C4: ห้ามใช้ PE default แต่งกระแสเงินสดให้สินทรัพย์ที่ไม่มีกำไร
        # (เช่น GLDM กองทองคำ) — intrinsic value ที่ได้จะไม่มีความหมายแต่ดูน่าเชื่อ
        raise ValueError(
            f"{ticker} ไม่มีข้อมูล P/E (สินทรัพย์ไม่มีกำไร เช่น กองทองคำ) — ข้ามการทำ DCF"
        )
    earnings_per_price = 1 / pe_ratio
    base_cf = current_price * earnings_per_price + dividend

    cash_flows: list[dict[str, Any]] = []
    for year in range(1, years + 1):
        if year <= 5:
            cf = base_cf * (1 + growth_rate_high) ** year
        else:
            cf = base_cf * (1 + growth_rate_high) ** 5 * (1 + terminal_growth) ** (year - 5)
        pv = cf / (1 + wacc) ** year
        cash_flows.append({"year": year, "cash_flow": round(cf, 2), "present_value": round(pv, 2)})

    last_cf = cash_flows[-1]["cash_flow"]
    terminal_value = last_cf * (1 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / (1 + wacc) ** years

    intrinsic_value = sum(_safe_float(cf["present_value"], 0.0) for cf in cash_flows) + pv_terminal
    if intrinsic_value <= 0:
        intrinsic_value = current_price

    margin_of_safety = (intrinsic_value - current_price) / intrinsic_value * 100 if intrinsic_value else 0.0

    signal = (
        "Strong Buy"
        if margin_of_safety > 30
        else "Buy"
        if margin_of_safety > 15
        else "Fair Value"
        if margin_of_safety > 0
        else "Overvalued"
        if margin_of_safety > -15
        else "Avoid"
    )

    return {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "intrinsic_value": round(float(intrinsic_value), 2),
        "margin_of_safety": round(float(margin_of_safety), 2),
        "wacc": round(wacc * 100, 2),
        "growth_rate": round(growth_rate_high * 100, 2),
        "terminal_growth": round(terminal_growth * 100, 2),
        "beta": round(beta, 2),
        "cash_flows": cash_flows,
        "signal": signal,
    }


# ---------------------------------------------------------------------------
# ระบบให้คะแนนเดียวของทั้งระบบ (AUDIT.md C2)
#
# เดิมมี 2 สูตรที่ขัดกันเอง: calculate_signal_score ให้คะแนนสูงเมื่อ RSI ต่ำ
# ส่วน _pipeline_score_ticker (ที่ advisor ใช้) ให้คะแนนสูงเมื่อ RSI อยู่กลาง ๆ
# → VOO วันเดียวกันได้ 100% "Strong Buy" ในหน้าหนึ่ง และ 47% "Neutral" ในอีกหน้า
#
# ปรัชญาเดียวที่ใช้ตอนนี้ (สอดคล้องกับ technical/signal_rules.py):
#   แนวโน้มยาว (MA200) เป็นเงื่อนไขตั้งต้น → จังหวะย่อในขาขึ้นคือโอกาสสะสมของ DCA
#   overbought = ไม่ให้คะแนนจังหวะ | ต่ำกว่า MA200 = คะแนนแนวโน้มต่ำ
#
# DCF ไม่อยู่ในคะแนนอีกต่อไป — DCF ของ ETF เป็น earnings-yield proxy ไม่ใช่ DCF
# ของกิจการจริง (AUDIT.md C4) จึงแสดงเป็นข้อมูลประกอบเท่านั้น ไม่ตัดสินซื้อ/ขาย
# ---------------------------------------------------------------------------

TREND_MAX, TIMING_MAX, MOMENTUM_MAX, DIVIDEND_MAX = 40, 30, 20, 10


def _trend_score(price: float, ma50: float, ma200: float) -> int:
    """แนวโน้มระยะยาว (0-40) — เหนือ MA200 คือเงื่อนไขตั้งต้นของการสะสม."""
    if price >= ma200:
        return 40 if price >= ma50 else 30
    return 10 if price >= ma50 else 0


def _timing_score(price: float, ma200: float, rsi: float) -> int:
    """จังหวะเข้า (0-30) ตามนิยามใน signal_rules — ย่อในขาขึ้นได้คะแนนเต็ม."""
    zone = signal_rules.rsi_zone(rsi)
    uptrend = price >= ma200
    if zone == "oversold":
        return 30 if uptrend else 10  # ย่อในขาลง = ยังไม่ใช่จังหวะ
    if zone == "overbought":
        return 0
    return 20 if rsi < 50 else 10


def _momentum_score(return_1m_pct: float, return_3m_pct: float) -> tuple[int | None, int]:
    """โมเมนตัม (0-20) พร้อมเพดานที่ใช้ได้จริง — คืน ``(คะแนน, เพดาน)``.

    หน้าต่างที่คำนวณไม่ได้ (``NaN``) ถูกตัดออกจาก**ทั้งคะแนนและเพดาน**
    แบบเดียวกับคะแนนปันผลที่หายไป — ไม่ใช่นับเป็น "ไม่บวก" ซึ่งเท่ากับเดา (C1)
    คืน ``(None, 0)`` เมื่อไม่มีหน้าต่างไหนใช้ได้เลย
    """
    half = MOMENTUM_MAX // 2
    score = 0
    max_pts = 0
    for value in (return_1m_pct, return_3m_pct):
        if value is None or pd.isna(value):
            continue
        max_pts += half
        if value > 0:
            score += half
    if max_pts == 0:
        return None, 0
    return score, max_pts


def _dividend_score(div_yield: float) -> int:
    """ปันผล (0-10). ``div_yield`` เป็นสัดส่วน เช่น 0.035 = 3.5%."""
    if div_yield > 0.04:
        return 10
    if div_yield > 0.02:
        return 5
    if div_yield > 0:
        return 2
    return 0


# yield เกินค่านี้ = ข้อมูลผิด ไม่ใช่ ETF จริง → ตัดคะแนนปันผลออกแทนการเดา (C1)
_MAX_PLAUSIBLE_DIVIDEND_PCT = 100.0


def _normalize_dividend_yield(raw: Any) -> float | None:
    """แปลง ``dividendYield`` ของ yfinance (หน่วยเปอร์เซ็นต์) เป็นสัดส่วน — 3.3 → 0.033.

    **ห้ามกลับไปเดาหน่วยเอง** (AUDIT.md M15): เดิมใช้ ``value / 100 if value > 1 else value``
    ซึ่งแยก "0.43 = 43%" กับ "0.43 = 0.43%" ไม่ออก → ETF ที่ yield ต่ำกว่า 1% (QQQM)
    ถูกอ่านเป็น 43% แล้วได้คะแนนปันผลเต็ม 10 แทนที่จะได้ 2 ทำให้คะแนนรวมข้ามเส้น
    "Strong Buy" (70) ทั้งที่ควรเป็น "Buy"

    ``yfinance==0.2.61`` ที่ pin ไว้คืนเปอร์เซ็นต์เสมอ (VOO=1.07, SCHD=3.3, QQQM=0.43)
    ถ้าอัปเวอร์ชันต้องตรวจ semantics ใหม่แล้วแก้ที่ฟังก์ชันนี้จุดเดียว

    คืน ``None`` เมื่อไม่มีข้อมูล/ค่าเพี้ยน → คะแนนปันผลถูกตัดออกจาก max ทั้งก้อน
    """
    if raw is None:
        return None
    value = _safe_float(raw, -1.0)
    if value < 0 or value > _MAX_PLAUSIBLE_DIVIDEND_PCT:
        return None
    return value / 100.0


def _dividend_yield(ticker: str) -> float | None:
    """ดึง dividend yield แบบสัดส่วน; ล้มเหลวคืน None (คะแนนปันผลจะถูกตัดออกจาก max)."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    return _normalize_dividend_yield(info.get("dividendYield"))


def _signal_label(total_pct: float) -> str:
    if total_pct >= 70:
        return "Strong Buy"
    if total_pct >= 55:
        return "Buy"
    if total_pct >= 40:
        return "Neutral"
    if total_pct >= 25:
        return "Caution"
    return "Avoid"


def score_from_prices(
    ticker: str,
    closes: pd.Series,
    div_yield: float | None = None,
) -> dict[str, Any]:
    """คะแนนกลางจากอนุกรมราคา — ใช้ร่วมกันทุก entry point ของระบบ.

    ต้องมีข้อมูลอย่างน้อย 200 วันเทรด ไม่งั้น raise (ผู้เรียกต้องแปลงเป็น NO DATA)
    """
    closes = pd.to_numeric(closes, errors="coerce").dropna()
    if len(closes) < 200:
        raise ValueError(f"{ticker}: ข้อมูลราคาน้อยกว่า 200 วันเทรด")

    price = float(closes.iloc[-1])
    ma50 = float(ta.sma(closes, length=50).iloc[-1])
    ma200 = float(ta.sma(closes, length=200).iloc[-1])
    rsi = float(ta.rsi(closes, length=14).iloc[-1])
    if any(pd.isna(v) for v in (price, ma50, ma200, rsi)):
        raise ValueError(f"{ticker}: คำนวณตัวชี้วัด MA/RSI ไม่ได้")

    # ผลตอบแทนทบต้นของช่วง ไม่ใช่ผลรวมรายวัน (FIX_PLAN ข้อ 1.5)
    # หน้าต่างและสูตรมาจาก analysis/returns.py แหล่งเดียวกับตาราง Returns (C7)
    return_1m = period_return_pct(closes, RETURN_WINDOWS["1M"])
    return_3m = period_return_pct(closes, RETURN_WINDOWS["3M"])

    trend_s = _trend_score(price, ma50, ma200)
    timing_s = _timing_score(price, ma200, rsi)
    mom_s, mom_max = _momentum_score(return_1m, return_3m)

    max_score = TREND_MAX + TIMING_MAX + mom_max
    div_s = 0
    if div_yield is not None:
        div_s = _dividend_score(div_yield)
        max_score += DIVIDEND_MAX

    # โมเมนตัมที่คำนวณไม่ได้ (``mom_s is None``) บวก 0 เข้าผลรวมได้ **เพราะ
    # ``mom_max`` เป็น 0 ไปแล้ว ตัวหารจึงหดตามพร้อมกัน** — ไม่ใช่เพราะ 0 คือคำตอบ
    # เขียนเป็น ``if ... is not None`` ไม่ใช่ ``or 0``: ``or`` กลืน 0 ที่เป็นคะแนนจริง
    # (มีข้อมูลแต่ติดลบทั้งสองหน้าต่าง) เข้ากับ None ที่แปลว่าไม่มีข้อมูล — แยกไม่ออก
    # และเป็นสำนวนเดียวกับที่ทำให้ ``or 0.0`` ดัก NaN ไม่ได้ในข้อ 1.6
    total = trend_s + timing_s + (mom_s if mom_s is not None else 0) + div_s
    total_pct = round(total * 100.0 / max_score, 1)
    central = signal_rules.dca_signal(price, ma50, ma200, rsi)

    return {
        "ticker": ticker,
        "data_ok": True,
        "price": round(price, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "rsi": round(rsi, 2),
        # NaN = คำนวณไม่ได้ → คืน None ("ไม่มีข้อมูล") ไม่ใช่ 0 ซึ่งอ่านเป็น "ไม่ขึ้นไม่ลง"
        "return_1m_pct": _round_or_none(return_1m, 2),
        "return_3m_pct": _round_or_none(return_3m, 2),
        "trend_score": trend_s,
        "timing_score": timing_s,
        "momentum_score": mom_s,
        "momentum_available": mom_s is not None,
        "momentum_max": mom_max,
        "dividend_score": div_s,
        "dividend_available": div_yield is not None,
        "total_score": total,
        "max_score": max_score,
        "total_pct": total_pct,
        "signal": _signal_label(total_pct),
        "technical_signal": central,
        "technical_signal_th": signal_rules.thai_description(central),
    }


@cache_data_1h
def calculate_signal_score(ticker: str) -> dict[str, Any]:
    """คะแนนกลาง + ข้อมูล DCF ประกอบ (DCF ไม่ถูกนับเป็นคะแนน — AUDIT.md C4).

    ผลสำเร็จ cache 1 ชม. ราย ticker; ล้มเหลว = raise และไม่ถูก cache (AUDIT.md C1)
    → ticker ที่ข้อมูลพังถูกลองใหม่ทุกคำขอ ตัวที่ดีไม่ต้องยิง yfinance ซ้ำ
    """
    closes = _download_close(ticker, "2y")
    result = score_from_prices(ticker, closes, div_yield=_dividend_yield(ticker))

    try:
        dcf = dcf_valuation(ticker)
        dcf_available = True
    except ValueError as exc:
        dcf = {"ticker": ticker, "signal": "N/A", "error": str(exc)}
        dcf_available = False

    result["dcf"] = dcf
    result["dcf_available"] = dcf_available
    result["dcf_note"] = (
        "DCF เป็นข้อมูลประกอบ ไม่ถูกนับเป็นคะแนน (เป็น earnings-yield proxy ของ ETF ไม่ใช่ DCF กิจการ)"
    )
    time.sleep(1)  # เว้นจังหวะเฉพาะรอบที่ยิง yfinance จริง กัน rate limit — cache hit ไม่ต้องรอ
    return result


# --- การจัดสรรงบ DCA: สัดส่วนเป้าหมายเป็นฐาน + คะแนนเป็นตัวปรับน้ำหนัก ---
#
# ปรัชญา (ผู้ใช้เลือกแบบนี้): เป็น DCA ระยะยาว → **ซื้อทุกสินทรัพย์ในพอร์ตทุกเดือน**
# รักษาการกระจายความเสี่ยงไว้ แล้วใช้คะแนนเพียง "เอียงน้ำหนัก" เข้าหาตัวที่สัญญาณดีกว่า
#
# ไม่ใช้คะแนนล้วนแบบเดิม เพราะจะตัดสินทรัพย์ที่อยู่ในขาลงออกจากพอร์ตทั้งตัว
# (เช่น GLDM ต่ำกว่า MA200 → ไม่ได้เงินเลย) ซึ่งเป็นการ market timing ที่ขัดกับหลัก DCA
#
# ตัวคูณ: คะแนน 0 → 0.6 เท่าของเป้าหมาย | คะแนน 50 → 1.0 เท่า | คะแนน 100 → 1.4 เท่า
TILT_MIN, TILT_MAX = 0.6, 1.4
ALLOCATION_UNIT_THB = 100  # ปัดจำนวนเงินเป็นหลักร้อย


def _score_tilt(total_pct: float) -> float:
    """แปลงคะแนน 0-100 เป็นตัวคูณน้ำหนัก (bounded — ไม่มีวันเป็น 0 หรือพุ่งเกินคุม).

    การ clamp 0–100 **คือสิ่งที่บังคับใช้** กรอบ ``TILT_MIN``–``TILT_MAX`` ที่ CLAUDE.md
    ประกาศเป็นนโยบายจัดสรร DCA ("ทุก ETF ที่มีข้อมูลได้เงินเสมอ") ไม่ใช่การตกแต่ง:
    ถอด clamp ออกแล้วคะแนน 150 ให้ tilt 1.8 และคะแนน −40 ให้ 0.28 = เกือบไม่ซื้อ
    ซึ่งเป็น market timing ที่นโยบายห้ามไว้ (AUDIT_ROUND2_2026-08-07 · T5 M12 —
    ถอด clamp แล้วชุดเทสต์เดิมยังเขียวทั้งชุด จึงมี
    ``tests/test_score_tilt_bounds.py`` ตรึงไว้)

    วันนี้ ``total_pct`` มาจาก ``score_from_prices()`` ทางเดียวซึ่งอยู่ใน [0,100] เสมอ
    แต่ ``calculate_allocation()`` รับ dict จากผู้เรียกภายนอกได้ (ai_advisor, dashboard)
    ด่านนี้จึงต้องอยู่ต่อไป

    ``NaN``/``None`` = **"ไม่รู้คะแนน" ไม่ใช่คะแนน** → ``ValueError`` เพราะ
    ``max(0.0, min(100.0, nan))`` คืน ``nan`` เงียบ ๆ แล้วไหลต่อไปเป็นน้ำหนักเงินจริง
    (ผู้เรียกต้องคัด ``data_ok=False`` ออกก่อน — นั่นคือหน้าที่ของ ``usable`` ใน
    ``calculate_allocation()``)
    """
    try:
        value = float(total_pct)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"คะแนนที่ใช้คำนวณตัวคูณน้ำหนักไม่ใช่ตัวเลข: {total_pct!r}"
        ) from exc
    if math.isnan(value):
        raise ValueError(
            "คะแนนเป็น NaN — 'คำนวณคะแนนไม่ได้' ต้องถูกคัดออกก่อนถึงขั้นจัดสรรเงิน "
            "ห้ามกลายเป็นตัวคูณน้ำหนัก"
        )
    clamped = max(0.0, min(100.0, value))
    return TILT_MIN + (TILT_MAX - TILT_MIN) * (clamped / 100.0)


# --- เหตุผลที่ ticker หลุดจากแผน DCA — สามอย่างนี้ "คนละเรื่องกัน" ห้ามยุบรวม ---
#: ดึงราคา/คะแนนไม่สำเร็จรอบนี้ (``data_ok=False`` หรือไม่มี ``total_pct``)
EXCLUDED_NO_DATA = "no_data"
#: เป้าหมายของกองนี้เป็น 0% — เจตนาของผู้ใช้ ไม่ใช่ความล้มเหลว
EXCLUDED_ZERO_TARGET = "zero_target"
#: มีเป้าหมายและมีข้อมูล แต่ส่วนแบ่งไม่ถึงหนึ่งก้อน (``ALLOCATION_UNIT_THB``)
EXCLUDED_ROUNDED_TO_ZERO = "rounded_to_zero"


@dataclass(frozen=True)
class ExcludedTicker:
    """ticker ที่ไม่ได้เงินรอบนี้ พร้อมเหตุผลที่ **เครื่องอ่านได้** และข้อความไทยสำหรับหน้าจอ.

    ``reason`` เป็นค่าคงที่ ``EXCLUDED_*`` (ปลายทางเลือกวิธีแสดงเองได้)
    ``detail`` เป็นประโยคไทยที่เขียนไว้ที่เดียว — หน้าจอห้ามแต่งเหตุผลเอง
    """

    ticker: str
    reason: str
    detail: str


@dataclass(frozen=True)
class AllocationPlan:
    """แผน DCA พร้อม "สิ่งที่ไม่ได้อยู่ในแผนและเพราะอะไร" (AUDIT_ROUND2_2026-08-07 T7).

    เดิม ``calculate_allocation()`` คืนเฉพาะ dict ของ ticker ที่ได้เงิน ⇒ ETF ที่ผู้ใช้
    ตั้งเป้าไว้ 0% หายจากแผนโดยไม่เหลือร่องรอย ทั้งที่ ``portfolio/targets.py`` รู้เหตุผล
    อยู่แล้วและเขียนเป็น ``notes`` ภาษาไทยไว้ให้ — แล้วหน้า Scorecard ก็พิมพ์คำโปรยว่า
    "ซื้อทุกตัวที่มีข้อมูล ... ไม่มีการตัดตัวไหนออก" คู่กับแผนที่ตัดออกไปแล้วจริง ๆ

    ``allocation``  ticker → รายละเอียดเงินที่จะซื้อ (รูปแบบเดิมทุกช่อง)
    ``excluded``    ticker ที่ไม่ได้เงิน พร้อมเหตุผล (ดู :class:`ExcludedTicker`)
    ``notes``       หมายเหตุจาก ``get_target_weights_with_status()`` เช่น
                    "น้ำหนักที่ตั้งไว้ใช้ครบ 100% แล้ว — XLV, GLDM จึงได้ 0%"
    """

    allocation: dict[str, dict[str, Any]]
    excluded: list[ExcludedTicker] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def tickers_excluded_by(self, reason: str) -> list[str]:
        """รายชื่อ ticker ที่หลุดแผนด้วยเหตุผลนั้น (เรียงตามลำดับที่พบ)."""
        return [item.ticker for item in self.excluded if item.reason == reason]


def _no_data_detail(ticker: str, score: Any) -> str:
    """ข้อความไทยของ ticker ที่ข้อมูลไม่พร้อม — ยกเหตุผลจริงจากผลคะแนนมาเสมอ."""
    reason = ""
    if isinstance(score, dict):
        reason = str(score.get("error") or "").strip()
    return (
        f"{ticker}: ไม่ได้เข้าแผนรอบนี้เพราะข้อมูลไม่พร้อม"
        + (f" ({reason})" if reason else "")
        + " — ไม่ใช่การตัดสินว่าไม่น่าซื้อ"
    )


def calculate_allocation(
    scores: dict[str, Any],
    budget_thb: float,
    target_weights: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """เหมือน :func:`calculate_allocation_with_status` แต่คืนเฉพาะแผนที่ได้เงิน.

    รูปแบบเดียวกับ ``get_target_weights()`` / ``get_target_weights_with_status()`` และ
    ``get_news()`` / ``get_news_with_status()`` — ผู้เรียกที่ต้องแสดง **เหตุผลที่บางกอง
    ไม่อยู่ในแผน** ต้องเรียกตัว ``_with_status`` ไม่ใช่ตัวนี้
    """
    return calculate_allocation_with_status(
        scores, budget_thb, target_weights=target_weights
    ).allocation


def calculate_allocation_with_status(
    scores: dict[str, Any],
    budget_thb: float,
    target_weights: dict[str, float] | None = None,
) -> AllocationPlan:
    """จัดสรรงบ DCA — คำนวณในโค้ดเท่านั้น ห้ามให้ AI คิด (AUDIT.md C3).

    น้ำหนัก = สัดส่วนเป้าหมาย × ตัวคูณจากคะแนน (0.6–1.4) แล้ว normalize
    → ทุก ETF ที่มีข้อมูลได้เงินเสมอ (กระจายความเสี่ยง) แต่ตัวที่สัญญาณดีกว่าได้มากกว่าเป้า

    ticker ที่ ``data_ok=False`` หรือไม่มีคะแนน จะถูกข้าม และน้ำหนักเป้าหมายของมัน
    ถูกกระจายให้ตัวที่เหลือโดยอัตโนมัติ (ห้ามเดาราคา/คะแนนแทน — AUDIT.md C1)

    เศษเงินจากการปัดหลักร้อยถูกแจกด้วยวิธี largest-remainder เพื่อให้ใช้งบครบ

    งบน้อยกว่าหนึ่งก้อน (``ALLOCATION_UNIT_THB``) → ``ValueError``: เดิมคืน ``{}``
    ซึ่งหน้าตาเหมือน "ไม่มี ETF ที่มีข้อมูล" ทำให้ปลายทางรายงานสาเหตุผิดเป็น
    "ดึงข้อมูลไม่ได้" ทั้งที่ข้อมูลครบทุกตัว (AUDIT_2026-08-06 D3.10)

    ข้อผิดพลาดเรื่องเป้าหมายแยกเป็นสองชนิด ปลายทางต้องแยกข้อความให้ผู้ใช้ด้วย
    (AUDIT_ROUND2_2026-08-07 G1):

    * ``InvalidTargetWeights`` — ``portfolio.target_weights`` ผิดจริง แก้ config.json
    * ``NoTargetForSubset`` — คอนฟิกถูก แต่ ticker ที่ถือน้ำหนักดึงราคาไม่สำเร็จรอบนี้
      เหลือแต่ตัวที่ตั้งเป้าไว้ 0 → ห้ามชวนผู้ใช้ไปแก้คอนฟิกที่ไม่ได้ผิด

    **ticker ที่ไม่ได้เงินต้องมีชื่ออยู่ใน ``excluded`` เสมอ ห้ามหายเงียบ**
    (AUDIT_ROUND2_2026-08-07 T7) — สามสาเหตุแยกกันชัดเจน ``no_data`` (ดึงข้อมูล
    ไม่สำเร็จ) · ``zero_target`` (ตั้งใจไม่ถือ เป้าหมาย 0%) · ``rounded_to_zero``
    (งบไม่พอปัดเป็นก้อนละ 100 บาท) เพราะเป็นคนละเรื่องกันสำหรับผู้ใช้
    """
    from portfolio.targets import get_target_weights_with_status

    if budget_thb < ALLOCATION_UNIT_THB:
        raise ValueError(
            f"งบ {budget_thb:,.0f} บาท น้อยกว่าหน่วยจัดสรรขั้นต่ำ {ALLOCATION_UNIT_THB} บาท "
            "— จัดสรรไม่ได้ (ไม่เกี่ยวกับความพร้อมของข้อมูล)"
        )

    def _pct(v: dict[str, Any]) -> float:
        return _safe_float(v.get("total_pct"), -1.0)

    usable = {
        k: v
        for k, v in scores.items()
        if isinstance(v, dict) and v.get("data_ok", True) and _pct(v) >= 0
    }

    # ticker ที่ถูกคัดออกเพราะข้อมูลไม่พร้อม — บอก targets.py ว่ารายชื่อนี้เป็นชุดย่อย
    # ที่กรองมาแล้ว ไม่ใช่จักรวาลที่ผู้ใช้ตั้งไว้ ไม่งั้น "ดึงราคาไม่สำเร็จ" จะถูกรายงาน
    # เป็น "คอนฟิกผิด" (AUDIT_ROUND2_2026-08-07 G1) · ตัวที่เหลือน้ำหนักไม่พอจัดสรร
    # จะโยน NoTargetForSubset ขึ้นไปให้ปลายทางแยกสาเหตุได้ — ห้ามกลืนเป็น {} เพราะ
    # {} แปลว่า "ไม่มี ETF ที่มีข้อมูล" ซึ่งคนละเรื่องกัน (AUDIT_2026-08-06 D3.10)
    unusable = [k for k in scores if k not in usable]
    excluded = [
        ExcludedTicker(ticker, EXCLUDED_NO_DATA, _no_data_detail(ticker, scores[ticker]))
        for ticker in unusable
    ]
    if not usable:
        return AllocationPlan(allocation={}, excluded=excluded)

    notes: list[str] = []
    if target_weights:
        targets = dict(target_weights)
    else:
        status = get_target_weights_with_status(list(usable.keys()), partial=bool(unusable))
        targets, notes = status.weights, list(status.notes)

    weights: dict[str, float] = {}
    for ticker, data in usable.items():
        base = _safe_float(targets.get(ticker), 0.0)
        if base <= 0:
            # ไม่มีสัดส่วนเป้าหมาย → ไม่อยู่ในแผน DCA แต่ **ต้องบอกว่าทำไม**:
            # 0% คือเจตนา "ตั้งใจไม่ถือ" ของผู้ใช้ ไม่ใช่ข้อมูลขาด (T7)
            excluded.append(
                ExcludedTicker(
                    ticker,
                    EXCLUDED_ZERO_TARGET,
                    f"{ticker}: เป้าหมายเป็น 0% จึงไม่อยู่ในแผนเดือนนี้ "
                    "— เป็นการตั้งใจไม่ถือตาม portfolio.target_weights ไม่ใช่ข้อมูลขาด",
                )
            )
            continue
        weights[ticker] = base * _score_tilt(_pct(data))

    total_weight = sum(weights.values())
    if total_weight <= 0:
        return AllocationPlan(allocation={}, excluded=excluded, notes=notes)

    # แบ่งเป็นก้อนละ 100 บาท แล้วแจกเศษให้ตัวที่เศษมากสุด (ใช้งบครบ ไม่หายเงียบ)
    total_units = int(budget_thb // ALLOCATION_UNIT_THB)
    exact = {t: (w / total_weight) * total_units for t, w in weights.items()}
    units = {t: int(v) for t, v in exact.items()}
    leftover = total_units - sum(units.values())
    if leftover > 0:
        by_remainder = sorted(
            exact, key=lambda t: (exact[t] - units[t], _pct(usable[t])), reverse=True
        )
        for ticker in by_remainder[:leftover]:
            units[ticker] += 1

    allocation: dict[str, dict[str, Any]] = {}
    for ticker in sorted(weights, key=lambda t: units[t], reverse=True):
        amount = units[ticker] * ALLOCATION_UNIT_THB
        if amount <= 0:
            # มีเป้าหมายและมีข้อมูล แต่ส่วนแบ่งไม่ถึงหนึ่งก้อน — "งบไม่พอ" คนละเรื่องกับ
            # "ไม่ถือ" และ "ดึงข้อมูลไม่ได้" ผู้ใช้แก้ได้ด้วยการเพิ่มงบเท่านั้น
            excluded.append(
                ExcludedTicker(
                    ticker,
                    EXCLUDED_ROUNDED_TO_ZERO,
                    f"{ticker}: ส่วนแบ่งจากงบ {budget_thb:,.0f} บาท ไม่ถึงก้อนละ "
                    f"{ALLOCATION_UNIT_THB} บาท จึงยังไม่ได้เงินรอบนี้ (เพิ่มงบแล้วจะได้)",
                )
            )
            continue
        target_pct = _safe_float(targets.get(ticker), 0.0) * 100.0
        actual_pct = amount / budget_thb * 100.0
        allocation[ticker] = {
            "amount_thb": amount,
            "percent": round(actual_pct, 1),
            "target_percent": round(target_pct, 1),
            # ผลจาก "คะแนน" ล้วน ๆ — ไม่ปนเอฟเฟกต์การปัดหลักร้อย ไม่งั้นผู้ใช้จะเข้าใจผิด
            # ว่าคะแนนดันน้ำหนักขึ้น ทั้งที่จริงเป็นแค่เศษการปัด
            "tilt": round(_score_tilt(_pct(usable[ticker])), 2),
            "group": _signal_label(_pct(usable[ticker])),
            "score": _pct(usable[ticker]),
        }

    return AllocationPlan(allocation=allocation, excluded=excluded, notes=notes)


def run_full_analysis(budget_thb: float = 5000) -> dict[str, Any]:
    tickers = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

    results: dict[str, Any] = {}
    for ticker in tickers:
        print(f"Analyzing {ticker}...")
        try:
            results[ticker] = calculate_signal_score(ticker)
        except Exception as exc:
            # ข้อมูลพัง = ระบุชัดว่า NO DATA — ห้ามกลายเป็นคะแนน 0/สัญญาณ Avoid (AUDIT.md C1)
            # ไม่ถูก cache → คำขอถัดไปได้ลองใหม่; sleep เพราะรอบนี้ยิง yfinance ไปแล้วจริง
            results[ticker] = {
                "ticker": ticker,
                "data_ok": False,
                "error": str(exc),
                "total_score": None,
                "total_pct": None,
                "signal": "NO DATA",
            }
            time.sleep(1)

    allocation = calculate_allocation(results, budget_thb)

    return {
        "analysis": results,
        "allocation": allocation,
        "timestamp": datetime.now().isoformat(),
    }


# --- Multi-ticker scores for advisor / jobs pipeline ---
# ใช้ score_from_prices ตัวเดียวกับ calculate_signal_score — คะแนนของ ETF ตัวเดียวกัน
# ต้องเท่ากันทุกหน้าจอเสมอ (AUDIT.md C2)

DEFAULT_ETF_TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]


def _yf_close_series(ticker: str) -> pd.Series:
    """ดึงราคาปิดรายวันย้อนหลัง 2 ปี (พอสำหรับ MA200); ล้มเหลวคืน series ว่าง."""
    try:
        df = yf.download(
            tickers=ticker,
            period="2y",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if df.empty or "Close" not in df.columns:
            return pd.Series(dtype=float)
        close_data = df["Close"]
        if isinstance(close_data, pd.DataFrame):
            if close_data.empty:
                return pd.Series(dtype=float)
            close_series = close_data.iloc[:, 0]
        else:
            close_series = close_data
        return close_series.dropna().sort_index()
    except Exception:
        return pd.Series(dtype=float)


def _no_data(ticker: str, reason: str) -> dict[str, Any]:
    """ผลลัพธ์เมื่อข้อมูลไม่พร้อม — สถานะชัดเจน ไม่ใช่คะแนน 0/สัญญาณ Avoid (AUDIT.md C1)."""
    return {
        "ticker": ticker,
        "price": None,
        "ma50": None,
        "ma200": None,
        "rsi": None,
        "total_score": None,
        "total_pct": None,
        "signal": "NO DATA",
        "data_ok": False,
        "error": reason,
    }


def _score_ticker(ticker: str) -> dict[str, Any]:
    closes = _yf_close_series(ticker)
    if closes.empty:
        return _no_data(ticker, "ดึงราคาไม่สำเร็จ")
    try:
        return score_from_prices(ticker, closes, div_yield=_dividend_yield(ticker))
    except ValueError as exc:
        return _no_data(ticker, str(exc))


def build_etf_scores(tickers: list[str] | None = None) -> list[dict[str, Any]]:
    """คำนวณคะแนนและสัญญาณต่อ ETF (คะแนนกลางเดียวกับ calculate_signal_score).

    ถ้า ``tickers`` เป็น ``None`` ใช้ค่าเริ่มต้น
    ``VOO``, ``SCHD``, ``QQQM``, ``XLV``, ``GLDM``.
    """
    symbols = DEFAULT_ETF_TICKERS if tickers is None else list(tickers)
    return [_score_ticker(sym.strip().upper()) for sym in symbols if sym.strip()]
