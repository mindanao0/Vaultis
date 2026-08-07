from __future__ import annotations

import asyncio
import logging
from typing import Any

import yfinance as yf

from ..models.etf_models import ETFInfo

logger = logging.getLogger(__name__)

ETF_PROFILES: dict[str, str] = {
    "VOO": "S&P 500 Index ETF (Vanguard) — broad market",
    "QQQM": "Nasdaq 100 ETF (Invesco) — tech heavy",
    "SCHD": "Dividend ETF (Schwab) — income focused",
    "XLV": "Healthcare Sector ETF (SPDR) — sector",
    "GLDM": "Gold ETF (SPDR) — commodity / safe haven",
}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _failed(symbol: str, reason: str) -> ETFInfo:
    """ผลลัพธ์ที่บอกว่า "ดึงไม่สำเร็จ" — ห้ามหน้าตาเหมือน "ETF ที่ไม่มีข้อมูล".

    ``data_ok=False`` ทำให้ ``utils.cache.is_cacheable`` ปฏิเสธผลนี้เอง จึงไม่ค้าง
    อยู่ใน ``CacheService`` นาน ``ETF_INFO_TTL`` (6 ชม.) แบบ sentinel เดิม
    (AUDIT_2026-08-06 B5)
    """
    logger.warning("ดึงข้อมูล ETF %s ไม่สำเร็จ: %s", symbol, reason)
    return ETFInfo(symbol=symbol, data_ok=False, error=reason)


class ETFInfoService:
    async def get_info(self, symbol: str) -> ETFInfo:
        sym = symbol.strip().upper()
        try:
            raw = await asyncio.to_thread(lambda: yf.Ticker(sym).info)
            if not isinstance(raw, dict):
                return _failed(
                    sym, f"yfinance คืนข้อมูลผิดรูป (ได้ {type(raw).__name__} ไม่ใช่ dict)"
                )
            if not raw:
                # yfinance โดน rate-limit จะคืน dict ว่างโดยไม่โยน exception —
                # นั่นคือ "ดึงไม่สำเร็จ" ไม่ใช่ "ETF นี้ไม่มีข้อมูล"
                return _failed(sym, "yfinance คืนข้อมูลว่างเปล่า (มักเกิดตอนโดน rate limit)")

            price = _to_float(
                raw.get("currentPrice")
                or raw.get("regularMarketPrice")
                or raw.get("navPrice")
            )
            nav = _to_float(raw.get("navPrice"))
            total_assets = _to_float(raw.get("totalAssets"))
            expense_ratio = _to_float(raw.get("annualReportExpenseRatio"))
            dividend_yield = _to_float(raw.get("dividendYield") or raw.get("yield"))
            trailing_dividend = _to_float(raw.get("trailingAnnualDividendRate"))
            ytd_return = _to_float(raw.get("ytdReturn"))
            three_year_return = _to_float(raw.get("threeYearAverageReturn"))
            five_year_return = _to_float(raw.get("fiveYearAverageReturn"))
            beta = _to_float(raw.get("beta3Year") or raw.get("beta"))
            category = _optional_str(raw.get("category"))
            name = _optional_str(raw.get("longName") or raw.get("shortName"))
            profile = ETF_PROFILES.get(sym)

            # ``profile`` มาจากตารางฮาร์ดโค้ดในไฟล์นี้ ไม่ได้มาจาก yfinance จึงไม่นับ
            # เป็นหลักฐานว่าดึงข้อมูลได้ — ถ้าช่องที่ดึงมาจริงว่างหมด แปลว่าไม่ได้อะไรเลย
            fetched = (
                name,
                price,
                nav,
                total_assets,
                expense_ratio,
                dividend_yield,
                trailing_dividend,
                ytd_return,
                three_year_return,
                five_year_return,
                beta,
                category,
            )
            if all(v is None for v in fetched):
                return _failed(sym, "yfinance ไม่คืนช่องข้อมูลที่ใช้ได้เลยสักช่อง")

            return ETFInfo(
                symbol=sym,
                name=name,
                price=price,
                nav=nav,
                total_assets=total_assets,
                expense_ratio=expense_ratio,
                dividend_yield=dividend_yield,
                trailing_dividend=trailing_dividend,
                ytd_return=ytd_return,
                three_year_return=three_year_return,
                five_year_return=five_year_return,
                beta=beta,
                category=category,
                profile=profile,
            )
        except Exception as exc:
            return _failed(sym, f"เรียก yfinance ไม่สำเร็จ: {type(exc).__name__}: {exc}")
