# -*- coding: utf-8 -*-
"""โมดูลดึงข้อมูล Macro Economics จาก FRED และ yfinance."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from fredapi import Fred

from utils.cache import cache_data_1h

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)

_FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "cpi": "CPIAUCSL",
}

_YF_SYMBOLS = {
    "us10y_yield": "^TNX",
    "dxy": "DX-Y.NYB",
    "vix": "^VIX",
}


def _cpi_yoy_percent(cpi_series: pd.Series) -> pd.Series:
    """แปลงดัชนี CPI เป็นอัตราเงินเฟ้อ YoY (%) — ตัวเลขที่คนหมายถึงจริง ๆ.

    (AUDIT.md H7: เดิมรายงาน "inflation_cpi.value" เป็น **ระดับดัชนี** (~320)
    ซึ่งขึ้นแทบตลอด → เทรนด์เป็น "ขาขึ้น" เสมอ ข้อความ "เงินเฟ้อชะลอ" แทบไม่มีวันโผล่
    แม้เงินเฟ้อจะลดลงจริง)
    """
    cleaned = pd.to_numeric(cpi_series, errors="coerce").dropna()
    if len(cleaned) < 13:
        return pd.Series(dtype=float)
    return (cleaned.pct_change(periods=12) * 100.0).dropna()


def _to_float(value: Any) -> float | None:
    """แปลงค่าเป็น float แบบปลอดภัย."""
    if value is None or pd.isna(value):
        return None
    return float(value)


def _fred_latest_fed_funds_rate() -> float | None:
    """ดึง Federal Funds Rate ล่าสุดจาก FRED (FEDFUNDS); ล้มเหลวคืน None."""
    try:
        key = os.getenv("FRED_API_KEY", "").strip()
        if not key or key == "your_key_here":
            return None
        fred = Fred(api_key=key)
        series = fred.get_series("FEDFUNDS").dropna().sort_index()
        if series.empty:
            return None
        val = _to_float(series.iloc[-1])
        return None if val is None else round(val, 2)
    except Exception:
        return None


def _yfinance_last_close(symbol: str) -> float | None:
    """ดึงราคาปิดล่าสุดจาก yfinance; ล้มเหลวคืน None (ไม่แจ้งเตือน UI)."""
    try:
        df = yf.download(
            tickers=symbol,
            period="5d",
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
        if df.empty or "Close" not in df.columns:
            return None
        close_data = df["Close"]
        if isinstance(close_data, pd.DataFrame):
            if close_data.empty:
                return None
            close_series = close_data.iloc[:, 0]
        else:
            close_series = close_data
        cleaned = close_series.dropna().sort_index()
        if cleaned.empty:
            return None
        val = _to_float(cleaned.iloc[-1])
        return None if val is None else round(val, 2)
    except Exception:
        return None


def get_macro_snapshot() -> dict[str, float | bool | None]:
    """สรุป macro ล่าสุดสำหรับ advisor: Fed rate, VIX, DXY และ flag ความผันผวน.

    โหลด ``.env`` ด้วย python-dotenv ทุกครั้งที่เรียก ค่าตัวเลขปัดทศนิยม 2 ตำแหน่ง
    แหล่ง fed_rate/vix/dxy ล้มเหลวคืน ``None`` สำหรับฟิลด์นั้น (ไม่ throw)
    ``vix_warning`` เป็น True เมื่อ VIX > 25; ถ้าไม่มีค่า VIX ใช้ False
    """
    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)
    fed_rate = _fred_latest_fed_funds_rate()
    vix = _yfinance_last_close("^VIX")
    dxy = _yfinance_last_close("DX-Y.NYB")
    vix_warning = False if vix is None else bool(vix > 25)
    return {
        "fed_rate": fed_rate,
        "vix": vix,
        "dxy": dxy,
        "vix_warning": vix_warning,
    }


def _compute_trend(series: pd.Series, lookback: int = 3) -> str:
    """คำนวณแนวโน้มจากค่าเฉลี่ยล่าสุดเทียบกับช่วงก่อนหน้า."""
    cleaned = series.dropna()
    if len(cleaned) < lookback * 2:
        return "ทรงตัว"

    recent_avg = float(cleaned.tail(lookback).mean())
    previous_avg = float(cleaned.iloc[-(lookback * 2) : -lookback].mean())

    if previous_avg == 0:
        return "ทรงตัว"

    pct_change = ((recent_avg - previous_avg) / abs(previous_avg)) * 100.0
    if pct_change > 1.0:
        return "ขาขึ้น"
    if pct_change < -1.0:
        return "ขาลง"
    return "ทรงตัว"


def _fetch_fred_series(fred: Fred, series_id: str) -> pd.Series:
    """ดึงข้อมูลรายเดือนล่าสุดจาก FRED."""
    series = fred.get_series(series_id).dropna().sort_index()
    if series.empty:
        raise ValueError(f"ไม่พบข้อมูล FRED สำหรับ {series_id}")
    return series


def _fetch_yf_series(symbol: str, period: str = "6mo", interval: str = "1d") -> pd.Series:
    """ดึงราคาปิดล่าสุดจาก yfinance; ล้มเหลวคืน series ว่าง (ผู้เรียกตรวจเอง)."""
    from data.fetcher import normalize_close_series

    try:
        df = yf.download(
            tickers=symbol,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
        )
    except Exception as exc:
        logger.warning("ดึง %s จาก yfinance ไม่สำเร็จ: %s", symbol, exc)
        return pd.Series(dtype=float)

    series = normalize_close_series(df)
    if series.empty:
        logger.warning("ไม่พบข้อมูล Close ของ %s", symbol)
    return series.sort_index()


@cache_data_1h
def get_macro_data() -> dict[str, Any]:
    """ดึงข้อมูล Macro ทั้งหมดและสรุปค่าปัจจุบันกับแนวโน้ม."""
    try:
        fred_api_key = os.getenv("FRED_API_KEY", "").strip()
        if not fred_api_key or fred_api_key == "your_key_here":
            raise ValueError("กรุณาตั้งค่า FRED_API_KEY ในไฟล์ .env")

        fred = Fred(api_key=fred_api_key)

        fed_funds_series = _fetch_fred_series(fred, _FRED_SERIES["fed_funds_rate"])
        cpi_index_series = _fetch_fred_series(fred, _FRED_SERIES["cpi"])
        cpi_yoy_series = _cpi_yoy_percent(cpi_index_series)
        tnx_series = _fetch_yf_series(_YF_SYMBOLS["us10y_yield"])
        dxy_series = _fetch_yf_series(_YF_SYMBOLS["dxy"])
        vix_series = _fetch_yf_series(_YF_SYMBOLS["vix"])
        if tnx_series.empty or dxy_series.empty or vix_series.empty or cpi_yoy_series.empty:
            return {}

        result = {
            "as_of": str(
                max(
                    fed_funds_series.index[-1],
                    cpi_index_series.index[-1],
                    tnx_series.index[-1],
                    dxy_series.index[-1],
                    vix_series.index[-1],
                ).date()
            ),
            "fed_funds_rate": {
                "series_id": _FRED_SERIES["fed_funds_rate"],
                "value": round(_to_float(fed_funds_series.iloc[-1]) or 0.0, 2),
                "trend": _compute_trend(fed_funds_series),
                "unit": "percent",
            },
            "inflation_cpi": {
                "series_id": _FRED_SERIES["cpi"],
                # อัตราเงินเฟ้อ YoY (%) ไม่ใช่ระดับดัชนี — AUDIT.md H7
                "value": round(_to_float(cpi_yoy_series.iloc[-1]) or 0.0, 2),
                "trend": _compute_trend(cpi_yoy_series),
                "unit": "percent_yoy",
                "index_level": round(_to_float(cpi_index_series.iloc[-1]) or 0.0, 2),
            },
            "us10y_treasury_yield": {
                "symbol": _YF_SYMBOLS["us10y_yield"],
                "value": round(_to_float(tnx_series.iloc[-1]) or 0.0, 2),
                "trend": _compute_trend(tnx_series),
            },
            "dxy_dollar_index": {
                "symbol": _YF_SYMBOLS["dxy"],
                "value": round(_to_float(dxy_series.iloc[-1]) or 0.0, 2),
                "trend": _compute_trend(dxy_series),
            },
            "vix_fear_index": {
                "symbol": _YF_SYMBOLS["vix"],
                "value": round(_to_float(vix_series.iloc[-1]) or 0.0, 2),
                "trend": _compute_trend(vix_series),
            },
        }
        return result
    except Exception as exc:
        logger.warning("get_macro_data ล้มเหลว: %s", exc)
        return {}


def get_macro_summary() -> str:
    """สรุปภาวะตลาดจากข้อมูล Macro เป็นข้อความไทยสั้นๆ 2-3 บรรทัด."""
    macro = get_macro_data()
    if not macro:
        return "ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่"

    rate_trend = macro["fed_funds_rate"]["trend"]
    vix_value = macro["vix_fear_index"]["value"]
    dxy_trend = macro["dxy_dollar_index"]["trend"]
    cpi_trend = macro["inflation_cpi"]["trend"]

    if rate_trend == "ขาขึ้น" and vix_value < 20:
        line_1 = "ดอกเบี้ยสูงขึ้น + VIX ยังต่ำ = ตลาดยังสงบแต่มีแรงกดดันต่อหุ้นเติบโต"
    elif rate_trend == "ขาลง" and vix_value < 20:
        line_1 = "ดอกเบี้ยเริ่มลด + VIX ต่ำ = บรรยากาศเสี่ยงเปิดรับมากขึ้น"
    elif vix_value >= 25:
        line_1 = "VIX สูง = ตลาดอยู่ในโหมดระวังความเสี่ยง"
    else:
        line_1 = "ภาพรวมตลาดอยู่ในโหมดกลางๆ ยังไม่มีสัญญาณสุดโต่ง"

    if cpi_trend == "ขาลง":
        line_2 = "เงินเฟ้อชะลอ ช่วยลดแรงกดดันต่อการคุมเข้มนโยบายการเงิน"
    elif cpi_trend == "ขาขึ้น":
        line_2 = "เงินเฟ้อยังเร่งตัว เฟดอาจคงดอกเบี้ยสูงนานกว่าคาด"
    else:
        line_2 = "เงินเฟ้อทรงตัว ตลาดจับตาข้อมูลเศรษฐกิจรอบถัดไป"

    line_3 = (
        "ดอลลาร์แข็งค่าเพิ่มแรงกดดันสินทรัพย์เสี่ยง"
        if dxy_trend == "ขาขึ้น"
        else "ดอลลาร์อ่อน/ทรงตัว ช่วยผ่อนแรงกดดันต่อสินทรัพย์เสี่ยง"
    )

    return f"{line_1}\n{line_2}\n{line_3}"


if __name__ == "__main__":
    print(get_macro_data())
    print(get_macro_summary())
