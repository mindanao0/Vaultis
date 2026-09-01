# -*- coding: utf-8 -*-
"""แหล่งอัตราแลกเปลี่ยน THB/USD เดียวของทั้งระบบ (AUDIT.md M5).

เดิมมี 3 แหล่งให้ค่าต่างกัน:
- portfolio/tracker      → yfinance THB=X (แต่พังเงียบ ๆ → ตกไปใช้ 33.5 ตลอด)
- networth_service       → config default_fx_rate 33.5 คงที่ (ไม่เคยดึงสด)
- rebalance_service      → yfinance USDTHB=X, fallback 35.0
→ มูลค่าเงินบาทของสินทรัพย์เดียวกันไม่ตรงกันข้ามหน้าจอ

โมดูลนี้: ดึงสด → sanity check → cache → fallback เป็นค่า config
พร้อมบอกที่มาของค่าเสมอ (``is_live``) เพื่อให้ UI เตือนได้เมื่อใช้ค่าสำรอง

**ความล้มเหลวไม่ใช่คำตอบ ห้ามแคชยาวเท่าความสำเร็จ** (AUDIT_2026-08-06 B9)
เดิมเก็บ ``(fallback, False, now)`` ด้วย TTL เดียวกับค่าสด 1 ชม. ⇒ ดึงสดพลาดครั้งเดียว
แล้วแหล่งข้อมูลกลับมาเป็นปกติในนาทีถัดมา ทุกหน้าจอก็ยังคิดเงินบาทด้วยค่าสำรองต่อไป
อีกเกือบชั่วโมงโดยไม่มีทางรู้ (ขัดกับนโยบายของ ``utils/cache.py`` ที่เขียนไว้เองว่า
"ความล้มเหลวต้องเกิดซ้ำ ไม่ค้างเป็นผลลัพธ์") ตอนนี้:

- ค่าสด (``is_live=True``) แคช :data:`CACHE_TTL_SEC` = 1 ชม. เหมือนเดิม
- ค่าสำรอง (``is_live=False``) แคชแค่ :data:`FALLBACK_CACHE_TTL_SEC` = 60 วินาที
  ซึ่งเป็น **ตัวกันยิงซ้ำรัว ๆ** ไม่ใช่การเก็บคำตอบ — พ้นหน้าต่างนี้ต้องลองดึงสดใหม่เสมอ

**ค่าสำรองต้องผ่าน sanity band เดียวกับค่าสด** ``_config_fallback()`` เดิมส่ง
``default_fx_rate`` ออกไปดิบ ๆ (ตั้ง 900 ก็ได้ 900, ตั้ง 0 ก็ได้ 0) ทั้งที่ค่าสดที่ 900
ถูกปัดทิ้งไปแล้ว — ความรับผิดชอบตกหล่นระหว่างไฟล์ (``rebalance_service`` ดักได้แค่
``<= 0`` แล้วเขียนคอมเมนต์ว่า "ช่วงเป็นของ utils/fx") ตอนนี้ค่าสำรองนอกช่วงจะโยน
:class:`FxRateUnavailable` — ไม่มีอัตราที่ใช้ได้ = ต้องดัง ห้ามคำนวณเงินบาทต่อ
"""

from __future__ import annotations

import logging
import math
import time
from typing import NamedTuple

import yfinance as yf

from data.fetcher import normalize_close_series
from utils.config import load_config

logger = logging.getLogger(__name__)

# ช่วงที่สมเหตุสมผลของ THB/USD — นอกช่วงนี้ถือว่าข้อมูลผิด ไม่ใช่ค่าจริง
MIN_RATE, MAX_RATE = 20.0, 50.0

# ค่าสดใช้ได้นาน 1 ชม. · ค่าสำรองใช้ได้แค่ 60 วิ (กันยิงรัว ไม่ใช่แคชคำตอบ)
CACHE_TTL_SEC = 3600
FALLBACK_CACHE_TTL_SEC = 60

# ใช้เมื่ออ่าน config ไม่ได้เลย (คนละเรื่องกับ config ที่ตั้งค่าไว้ผิด — ค่านั้นต้องดัง)
DEFAULT_FALLBACK_RATE = 33.5

_cached: tuple[float, bool, float] | None = None  # (rate, is_live, fetched_at)


class FxRate(NamedTuple):
    rate: float
    is_live: bool  # False = ใช้ค่าสำรองจาก config (ตัวเลข THB อาจคลาดเคลื่อน)


class FxRateUnavailable(RuntimeError):
    """ไม่มีอัตราแลกเปลี่ยนที่ใช้ได้เลย — ดึงสดไม่ได้ **และ** ค่าสำรองก็ใช้ไม่ได้.

    ต้องดังจนถึงผู้ใช้ เพราะทุกตัวเลข "บาท" ที่คำนวณต่อจากนี้จะไม่มีความหมาย
    (เดิมค่าสำรองที่ตั้งผิด เช่น 900 หรือ 0 ไหลเข้าไปคูณ/หารมูลค่าพอร์ตเงียบ ๆ)
    """


def _config_fallback() -> float:
    """ค่าสำรองจาก ``config.json`` ที่ **ผ่าน band เดียวกับค่าสด** แล้วเท่านั้น.

    อ่าน config ไม่ได้ (ไฟล์หาย/คีย์หาย/ไม่ใช่ตัวเลข) → ใช้ :data:`DEFAULT_FALLBACK_RATE`
    ซึ่งอยู่ในช่วงอยู่แล้ว · แต่ค่าที่ผู้ใช้ **ตั้งไว้ผิด** ห้ามใช้ต่อ ต้องโยน
    :class:`FxRateUnavailable` — "ตั้งค่าผิด" กับ "ไม่ได้ตั้ง" คนละเรื่องกัน
    """
    try:
        rate = float(load_config()["display"]["default_fx_rate"])
    except Exception:
        rate = DEFAULT_FALLBACK_RATE

    if not math.isfinite(rate) or not (MIN_RATE <= rate <= MAX_RATE):
        raise FxRateUnavailable(
            "ดึงอัตราแลกเปลี่ยน THB/USD สดไม่ได้ และค่าสำรองใน config.json "
            f"(display.default_fx_rate = {rate!r}) อยู่นอกช่วงที่เป็นไปได้ "
            f"{MIN_RATE:.0f}–{MAX_RATE:.0f} บาท/USD — คำนวณมูลค่าเงินบาทต่อไม่ได้ "
            "แก้ค่าสำรองใน config.json ก่อน"
        )
    return rate


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
    """คืนอัตราแลกเปลี่ยน THB/USD พร้อมสถานะว่าเป็นค่าสดหรือค่าสำรอง.

    :raises FxRateUnavailable: ดึงสดไม่ได้และค่าสำรองใน config อยู่นอกช่วงที่ใช้ได้
    """
    global _cached
    now = time.monotonic()
    if not force_refresh and _cached is not None:
        rate, is_live, fetched_at = _cached
        # ความล้มเหลวหมดอายุเร็วกว่าความสำเร็จมาก — พอแหล่งข้อมูลกลับมาต้องได้ค่าสดเอง
        ttl = CACHE_TTL_SEC if is_live else FALLBACK_CACHE_TTL_SEC
        if now - fetched_at < ttl:
            return FxRate(rate, is_live)

    live = _fetch_live()
    if live is not None:
        _cached = (live, True, now)
        return FxRate(live, True)

    fallback = _config_fallback()  # นอกช่วง = โยน FxRateUnavailable (ไม่มีอะไรให้แคช)
    logger.warning("ใช้อัตราแลกเปลี่ยนสำรองจาก config: %.2f (ตัวเลขบาทอาจคลาดเคลื่อน)", fallback)
    _cached = (fallback, False, now)
    return FxRate(fallback, False)


def source_of(rate: float) -> bool | None:
    """ที่มาของ ``rate`` ที่โมดูลนี้เพิ่งให้ไป — **ไม่ยิงเน็ต** อ่านจากแคชอย่างเดียว.

    มีไว้ให้ผู้เรียกที่รับอัตรามาเป็นตัวเลขเปล่า (:func:`get_usdthb_rate`) ยังรายงาน
    ที่มาต่อไปถึงหน้าจอ/API ได้

    คืน ``True`` = ค่าสด · ``False`` = ค่าสำรอง · ``None`` = **ไม่ทราบ** (อัตรานี้
    ไม่ได้มาจากที่นี่ เช่นผู้เรียกจัดหาเอง) — "ไม่ทราบ" กับ "รู้ว่าเป็นค่าสำรอง"
    คนละความหมายกัน ห้ามยุบเป็นค่าเดียวหรือเดาเป็น ``False``
    """
    cached = _cached
    if cached is None:
        return None
    try:
        asked = float(rate)
    except (TypeError, ValueError):
        return None
    if not math.isclose(cached[0], asked, rel_tol=1e-12, abs_tol=1e-9):
        return None
    return cached[1]


def get_usdthb_rate() -> float:
    """คืนเฉพาะตัวเลขอัตราแลกเปลี่ยน.

    **ผู้เรียกที่ใช้ฟังก์ชันนี้เป็นผู้รับผิดชอบรายงานที่มาเอง** — ค่าสำรองทำให้ตัวเลข
    บาททั้งก้อนคลาดเคลื่อน (วัดได้จริง −1.39% ณ วันตรวจ) ผู้ใช้ต้องเห็นว่ากำลังดูค่าสำรอง
    อยู่ ไม่ใช่รู้กันเองในล็อก: ถามที่มาต่อด้วย :func:`source_of` แล้วส่งขึ้นหน้าจอ/API
    แบบเดียวกับ ``missing_prices`` (AUDIT_2026-08-06 B9)
    """
    return get_usdthb().rate
