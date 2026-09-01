from __future__ import annotations

import asyncio
import logging
import math
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
    """แปลงเป็น float ที่ใช้ได้จริง — ``None``/NaN/inf/แปลงไม่ได้ คืน ``None``.

    ``float(nan)`` **สำเร็จ** ไม่โยน exception ตัวแปลงเดิมจึงปล่อย NaN ออกไปเป็นค่าของ
    ``ETFInfo.ytd_return``/``beta``/... พร้อม ``data_ok=True`` แล้ว FastAPI serialize
    ด้วย ``json.dumps(allow_nan=False)`` **นอก** ตัว handler ⇒ ``except Exception`` ใน
    เราเตอร์ดักไม่ทัน ผู้ใช้ได้ 500 เปล่าไม่มีเหตุผลภาษาไทย และค่านั้นค้างในแคช 6 ชม.
    (AUDIT_ROUND2_2026-08-07 G5) — เกณฑ์เดียวกับ ``technical_service._scalar_float``
    และ ``utils.pdf_export._to_float`` ที่กรองถูกอยู่แล้ว
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _first_float(*values: Any) -> float | None:
    """ค่าตัวแรกที่เป็น "ตัวเลขจริง" — ห้ามใช้สำนวน ``a or b`` แทน.

    ``nan or b`` คืน ``nan`` เพราะ NaN เป็น truthy ⇒ ค่าจริงในช่องถัดไปถูกบังทิ้ง
    (ตัดข้อมูลทิ้งเงียบ ๆ ผิดพอกับกุตัวเลข) · ตรงข้ามกับ ``0.0 or b`` ที่กระโดดข้าม
    ``0.0`` ทั้งที่ 0% เป็นคำตอบจริงของ dividend yield / beta
    """
    for value in values:
        f = _to_float(value)
        if f is not None:
            return f
    return None


def _first_price(*values: Any) -> float | None:
    """ราคาตัวแรกที่มากกว่า 0 — yfinance ใช้ ``0`` แทน "ไม่มีค่า" ในช่องราคา.

    ราคา 0/ติดลบไม่ใช่ราคา ปล่อยผ่านแล้วปลายทางจะตีมูลค่าเป็นศูนย์ (กฎเดียวกับ
    ``rebalance_service._usable_price``) — ที่นี่รายงานเป็น ``None`` = ไม่รู้ราคา
    """
    for value in values:
        f = _to_float(value)
        if f is not None and f > 0:
            return f
    return None


def _optional_str(value: Any) -> str | None:
    """ข้อความที่ใช้ได้จริง — ``None``/ว่าง/NaN คืน ``None``.

    ช่องข้อความของ yfinance มาจาก pandas จึงเป็น ``nan`` ได้ และ ``str(nan)`` คือ
    สตริง ``"nan"`` ซึ่งจะกลายเป็น "ชื่อกองทุน" หรือ "หมวด" ปลอมบนหน้าจอ
    """
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    s = str(value).strip()
    return s if s else None


def _first_str(*values: Any) -> str | None:
    """ข้อความตัวแรกที่ใช้ได้ — เหตุผลเดียวกับ ``_first_float``.

    ``nan or shortName`` คืน ``nan`` (NaN เป็น truthy) ⇒ ชื่อสำรองที่ใช้ได้ถูกบังทิ้ง
    """
    for value in values:
        s = _optional_str(value)
        if s is not None:
            return s
    return None


# ช่องที่ ``get_info()`` อ่านจริง — ใช้ไล่ดูว่ามีช่องไหนถูกทิ้งเพราะ NaN/inf บ้าง
# (ช่องอื่นใน ``.info`` มีเป็นร้อย ไม่ได้ใช้ จึงไม่ต้องรายงาน)
_READ_KEYS: tuple[str, ...] = (
    "longName",
    "shortName",
    "category",
    "currentPrice",
    "regularMarketPrice",
    "navPrice",
    "totalAssets",
    "annualReportExpenseRatio",
    "dividendYield",
    "yield",
    "trailingAnnualDividendRate",
    "ytdReturn",
    "threeYearAverageReturn",
    "fiveYearAverageReturn",
    "beta3Year",
    "beta",
)


def _log_dropped_non_finite(symbol: str, raw: dict) -> None:
    """บอกให้รู้ว่าช่องไหนถูกทิ้งเพราะเป็น NaN/inf — "ค่านี้อ่านไม่ได้" ต้องไม่เงียบ.

    ค่าที่อ่านไม่ได้กลายเป็น ``null`` ในคำตอบ (= ไม่รู้) ซึ่งถูกแล้ว แต่ต้องไม่ปิดทั้ง
    endpoint เพราะช่องเสริมช่องเดียว (กฎ fail-closed ต้องไม่ทำให้ทั้งระบบใช้ไม่ได้)
    ผู้ดูแลจึงต้องเห็นใน log ว่า yfinance ส่งอะไรมาให้ — ไม่ใช่เดาจากคำตอบที่ว่าง
    """
    dropped = [
        k
        for k in _READ_KEYS
        if isinstance(raw.get(k), float) and not math.isfinite(raw.get(k))
    ]
    if dropped:
        logger.warning(
            "ETF %s: yfinance คืนค่าที่ไม่ใช่ตัวเลขจริง (NaN/inf) ในช่อง %s "
            "— รายงานเป็น null (ไม่รู้) ห้ามแปลงเป็น 0",
            symbol,
            ", ".join(dropped),
        )


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

            _log_dropped_non_finite(sym, raw)

            price = _first_price(
                raw.get("currentPrice"),
                raw.get("regularMarketPrice"),
                raw.get("navPrice"),
            )
            nav = _first_price(raw.get("navPrice"))
            total_assets = _to_float(raw.get("totalAssets"))
            expense_ratio = _to_float(raw.get("annualReportExpenseRatio"))
            dividend_yield = _first_float(raw.get("dividendYield"), raw.get("yield"))
            trailing_dividend = _to_float(raw.get("trailingAnnualDividendRate"))
            ytd_return = _to_float(raw.get("ytdReturn"))
            three_year_return = _to_float(raw.get("threeYearAverageReturn"))
            five_year_return = _to_float(raw.get("fiveYearAverageReturn"))
            beta = _first_float(raw.get("beta3Year"), raw.get("beta"))
            category = _optional_str(raw.get("category"))
            name = _first_str(raw.get("longName"), raw.get("shortName"))
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
