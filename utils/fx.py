# -*- coding: utf-8 -*-
"""แหล่งอัตราแลกเปลี่ยน THB/USD เดียวของทั้งระบบ (AUDIT.md M5).

เดิมมี 3 แหล่งให้ค่าต่างกัน:
- portfolio/tracker      → yfinance THB=X (แต่พังเงียบ ๆ → ตกไปใช้ 33.5 ตลอด)
- networth_service       → config default_fx_rate 33.5 คงที่ (ไม่เคยดึงสด)
- rebalance_service      → yfinance USDTHB=X, fallback 35.0
→ มูลค่าเงินบาทของสินทรัพย์เดียวกันไม่ตรงกันข้ามหน้าจอ

โมดูลนี้: ดึงสด → sanity check → cache 1 ชม. → fallback เป็นค่า config
พร้อมบอกที่มาของค่าเสมอ (``is_live``) เพื่อให้ UI เตือนได้เมื่อใช้ค่าสำรอง
"""

from __future__ import annotations

import logging
import time
from typing import NamedTuple

import yfinance as yf

from data.fetcher import normalize_close_series
from utils.config import load_config

logger = logging.getLogger(__name__)

# ช่วงที่สมเหตุสมผลของ THB/USD — นอกช่วงนี้ถือว่าข้อมูลผิด ไม่ใช่ค่าจริง
MIN_RATE, MAX_RATE = 20.0, 50.0
_CACHE_TTL_SEC = 3600

_cached: tuple[float, bool, float] | None = None  # (rate, is_live, fetched_at)


class FxRate(NamedTuple):
    rate: float
    is_live: bool  # False = ใช้ค่าสำรองจาก config (ตัวเลข THB อาจคลาดเคลื่อน)


def _config_fallback() -> float:
    try:
        return float(load_config()["display"]["default_fx_rate"])
    except Exception:
        return 33.5


def _fetch_live() -> float | None:
    for symbol in ("THB=X", "USDTHB=X"):
        try:
            df = yf.download(symbol, period="5d", progress=False, auto_adjust=True)
            series = normalize_close_series(df)
            if series.empty:
                continue
            rate = float(series.iloc[-1])
            if MIN_RATE <= rate <= MAX_RATE:
                return rate
            logger.warning("อัตราแลกเปลี่ยนจาก %s ผิดปกติ (%.4f) — ข้าม", symbol, rate)
        except Exception as exc:
            logger.warning("ดึงอัตราแลกเปลี่ยนจาก %s ไม่สำเร็จ: %s", symbol, exc)
    return None


def get_usdthb(force_refresh: bool = False) -> FxRate:
    """คืนอัตราแลกเปลี่ยน THB/USD พร้อมสถานะว่าเป็นค่าสดหรือค่าสำรอง."""
    global _cached
    now = time.monotonic()
    if not force_refresh and _cached is not None and now - _cached[2] < _CACHE_TTL_SEC:
        return FxRate(_cached[0], _cached[1])

    live = _fetch_live()
    if live is not None:
        _cached = (live, True, now)
        return FxRate(live, True)

    fallback = _config_fallback()
    logger.warning("ใช้อัตราแลกเปลี่ยนสำรองจาก config: %.2f (ตัวเลขบาทอาจคลาดเคลื่อน)", fallback)
    _cached = (fallback, False, now)
    return FxRate(fallback, False)


def get_usdthb_rate() -> float:
    """คืนเฉพาะตัวเลขอัตราแลกเปลี่ยน (สำหรับผู้เรียกที่ไม่สนใจที่มา)."""
    return get_usdthb().rate
