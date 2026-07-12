# -*- coding: utf-8 -*-
"""Financial modeling helpers for ETFs: 3-statement-style fundamentals + simplified DCF + scoring."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf

from analysis.ta_compat import ta
from technical import signal_rules


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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
    annual_returns = annual_last.pct_change().dropna()
    return {
        "dividend_per_share": info.get("dividendRate", 0) or 0,
        "5y_avg_return": _safe_float(annual_returns.mean(), 0.0),
        "5y_return_std": _safe_float(annual_returns.std(), 0.0),
        "free_cash_flow": info.get("freeCashflow", 0) or 0,
        "operating_cash_flow": info.get("operatingCashflow", 0) or 0,
    }


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
    annual_returns = annual_last.pct_change().dropna()
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


def _momentum_score(return_1m_pct: float, return_3m_pct: float) -> int:
    """โมเมนตัม (0-20)."""
    score = 0
    if return_1m_pct > 0:
        score += 10
    if return_3m_pct > 0:
        score += 10
    return score


def _dividend_score(div_yield: float) -> int:
    """ปันผล (0-10). ``div_yield`` เป็นสัดส่วน เช่น 0.035 = 3.5%."""
    if div_yield > 0.04:
        return 10
    if div_yield > 0.02:
        return 5
    if div_yield > 0:
        return 2
    return 0


def _dividend_yield(ticker: str) -> float | None:
    """ดึง dividend yield แบบสัดส่วน; ล้มเหลวคืน None (คะแนนปันผลจะถูกตัดออกจาก max)."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    raw = info.get("dividendYield")
    if raw is None:
        return None
    value = _safe_float(raw, -1.0)
    if value < 0:
        return None
    # yfinance เปลี่ยน semantics ข้ามเวอร์ชัน: บางรุ่นคืน 0.035 บางรุ่นคืน 3.5
    return value / 100.0 if value > 1.0 else value


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

    returns = closes.pct_change()
    return_1m = _safe_float(returns.tail(21).sum() * 100, 0.0)
    return_3m = _safe_float(returns.tail(63).sum() * 100, 0.0)

    trend_s = _trend_score(price, ma50, ma200)
    timing_s = _timing_score(price, ma200, rsi)
    mom_s = _momentum_score(return_1m, return_3m)

    max_score = TREND_MAX + TIMING_MAX + MOMENTUM_MAX
    div_s = 0
    if div_yield is not None:
        div_s = _dividend_score(div_yield)
        max_score += DIVIDEND_MAX

    total = trend_s + timing_s + mom_s + div_s
    total_pct = round(total * 100.0 / max_score, 1)
    central = signal_rules.dca_signal(price, ma50, ma200, rsi)

    return {
        "ticker": ticker,
        "data_ok": True,
        "price": round(price, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "rsi": round(rsi, 2),
        "return_1m_pct": round(return_1m, 2),
        "return_3m_pct": round(return_3m, 2),
        "trend_score": trend_s,
        "timing_score": timing_s,
        "momentum_score": mom_s,
        "dividend_score": div_s,
        "dividend_available": div_yield is not None,
        "total_score": total,
        "max_score": max_score,
        "total_pct": total_pct,
        "signal": _signal_label(total_pct),
        "technical_signal": central,
        "technical_signal_th": signal_rules.thai_description(central),
    }


def calculate_signal_score(ticker: str) -> dict[str, Any]:
    """คะแนนกลาง + ข้อมูล DCF ประกอบ (DCF ไม่ถูกนับเป็นคะแนน — AUDIT.md C4)."""
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
    """แปลงคะแนน 0-100 เป็นตัวคูณน้ำหนัก (bounded — ไม่มีวันเป็น 0 หรือพุ่งเกินคุม)."""
    clamped = max(0.0, min(100.0, total_pct))
    return TILT_MIN + (TILT_MAX - TILT_MIN) * (clamped / 100.0)


def calculate_allocation(
    scores: dict[str, Any],
    budget_thb: float,
    target_weights: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """จัดสรรงบ DCA — คำนวณในโค้ดเท่านั้น ห้ามให้ AI คิด (AUDIT.md C3).

    น้ำหนัก = สัดส่วนเป้าหมาย × ตัวคูณจากคะแนน (0.6–1.4) แล้ว normalize
    → ทุก ETF ที่มีข้อมูลได้เงินเสมอ (กระจายความเสี่ยง) แต่ตัวที่สัญญาณดีกว่าได้มากกว่าเป้า

    ticker ที่ ``data_ok=False`` หรือไม่มีคะแนน จะถูกข้าม และน้ำหนักเป้าหมายของมัน
    ถูกกระจายให้ตัวที่เหลือโดยอัตโนมัติ (ห้ามเดาราคา/คะแนนแทน — AUDIT.md C1)

    เศษเงินจากการปัดหลักร้อยถูกแจกด้วยวิธี largest-remainder เพื่อให้ใช้งบครบ
    """
    from portfolio.targets import get_target_weights

    if budget_thb <= 0:
        return {}

    def _pct(v: dict[str, Any]) -> float:
        return _safe_float(v.get("total_pct"), -1.0)

    usable = {
        k: v
        for k, v in scores.items()
        if isinstance(v, dict) and v.get("data_ok", True) and _pct(v) >= 0
    }
    if not usable:
        return {}

    targets = target_weights or get_target_weights(list(usable.keys()))

    weights: dict[str, float] = {}
    for ticker, data in usable.items():
        base = _safe_float(targets.get(ticker), 0.0)
        if base <= 0:
            continue  # ไม่มีสัดส่วนเป้าหมาย → ไม่อยู่ในแผน DCA
        weights[ticker] = base * _score_tilt(_pct(data))

    total_weight = sum(weights.values())
    if total_weight <= 0:
        return {}

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

    return allocation


def run_full_analysis(budget_thb: float = 5000) -> dict[str, Any]:
    tickers = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

    results: dict[str, Any] = {}
    for ticker in tickers:
        print(f"Analyzing {ticker}...")
        try:
            results[ticker] = calculate_signal_score(ticker)
        except Exception as exc:
            # ข้อมูลพัง = ระบุชัดว่า NO DATA — ห้ามกลายเป็นคะแนน 0/สัญญาณ Avoid (AUDIT.md C1)
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
