"""In-process TTL cache สำหรับ request path ของ backend (AUDIT.md H3).

เดิม ``utils/cache.cache_data_1h`` เป็น no-op และ CacheService นี้ถูกใช้แค่ในหน้า
ETF analysis เท่านั้น → ทุก request ของ /api/etf/* ยิง yfinance ใหม่ทั้งหมด
(ราคา 10 ปี × 5 ตัว) ทำให้โดน rate limit บ่อยจนกลายเป็นสัญญาณปลอม (C1)

หลักการเดียวกับ ``utils/cache.py`` (FIX_PLAN 1.4) — ตัวกรอง ``is_cacheable``
import มาใช้ร่วมกัน **ห้ามเขียนตัวที่สอง**:

- ค่าที่แปลว่า "ไม่มีข้อมูล" ห้ามค้างใน cache: ``None``, dict/list/DataFrame ว่าง,
  dict ที่ ``data_ok=False`` — เดิม ``{}`` จาก yfinance rate-limit ค้างอยู่ 5 นาที
  แล้ว ``jobs/daily_check.py`` ตกไปเส้นทาง fallback ที่กุ ``+0.00%``
- **ผลบางส่วนก็คือความล้มเหลว** — ขอ 5 ticker ได้ 4 ห้ามแคช (ส่ง ``expect_keys``
  มาให้ตรวจ) ไม่งั้น ticker ที่ดึงไม่สำเร็จหายไปทั้ง TTL แล้วปลายทางอ่านว่า
  "ไม่มีข้อมูล" แทน "ดึงไม่สำเร็จ"
- **ตรวจไม่ได้ = ไม่แคช** (fail-closed) ถ้าผลลัพธ์เป็นชนิดที่นับคีย์ไม่ได้ หรือเป็น
  DataFrame คอลัมน์ MultiIndex ที่ยังไม่ normalize จะไม่ถูกแคช ต้นทุนคือคำนวณซ้ำ
  ซึ่งถูกกว่าการค้าง "ผลที่ตรวจไม่ได้ว่าครบไหม" ไว้ทั้ง TTL
- คืน "สำเนา" เสมอ — ผู้เรียกแก้ผลลัพธ์ได้โดยไม่ทำของในแคชสกปรกข้าม request
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any, Optional, TypeVar

import pandas as pd

from utils.cache import is_cacheable

logger = logging.getLogger(__name__)

ETF_INFO_TTL = 6 * 60 * 60  # 6 hours
TECHNICAL_TTL = 15 * 60  # 15 minutes
PRICE_HISTORY_TTL = 60 * 60  # 1 hour

T = TypeVar("T")

# แยกเป็นตัวแปร module เพื่อให้เทสต์ monkeypatch เวลาได้โดยไม่ต้องรอจริง
# (แบบเดียวกับ utils/cache.py)
_now = time.monotonic


def etf_info_cache_key(symbol: str) -> str:
    return f"etf_info:{symbol.strip().upper()}"


def etf_technical_cache_key(symbol: str) -> str:
    return f"etf_technical:{symbol.strip().upper()}"


def _normalize_key(name: Any) -> str:
    return str(name).strip().upper()


def _has_value(value: Any) -> bool:
    """ช่องนี้ "มีข้อมูลจริง" ไหม — ``None``/``NaN``/ว่างเปล่า/``data_ok=False`` = ไม่มี.

    ``0.0`` และ ``False`` เป็นค่าที่ถูกต้องตามจริง (ผลตอบแทน 0% ก็คือข้อมูล)
    ห้ามนับว่าขาด — เกณฑ์ "ว่างเปล่า" ใช้ ``utils.cache.is_cacheable`` ตัวเดียวกับ
    ทั้งระบบ ไม่เขียนนิยามที่สอง
    """
    if not is_cacheable(value):
        return False
    try:
        flag = pd.isna(value)
    except (TypeError, ValueError):  # ชนิดที่ pandas ไม่รู้จัก = ถือว่ามีข้อมูล
        return True
    if isinstance(flag, bool):
        return not flag
    try:  # array-like: มีข้อมูลถ้ามีค่าที่ไม่ใช่ NaN อย่างน้อยหนึ่ง (เกณฑ์เดียวกับคอลัมน์ DataFrame)
        return bool((~flag).any())
    except (TypeError, ValueError):
        return True


# คีย์ที่บอก "ของใคร / สถานะอะไร" ไม่ใช่ตัวข้อมูล — ผลลัพธ์ที่เหลือแต่คีย์พวกนี้
# แปลว่าไม่ได้ข้อมูลอะไรกลับมาเลย
_IDENTITY_KEYS = frozenset({"symbol", "ticker", "id", "data_ok", "error"})


def _payload_is_all_empty(value: Any) -> bool:
    """dict ที่ไม่มีค่าจริงสักช่อง (ไม่นับคีย์ระบุตัวตน/สถานะ) = ดึงไม่สำเร็จ ห้ามแคช.

    รูรั่วที่ปิด (AUDIT_2026-08-06 B5): ตอน yfinance ล่ม ``ETFInfoService`` เคยคืน
    ``ETFInfo(symbol=sym)`` ซึ่ง dump ออกมาเป็น ``{'symbol': 'VOO', 'name': None,
    'price': None, ...}`` — **ไม่ว่างเปล่า** และ **ไม่มี ``data_ok``** จึงผ่าน
    ``is_cacheable`` ไปค้างอยู่ 6 ชม. ทั้งที่มันคือความล้มเหลว

    ใช้ ``_has_value()`` ตัวเดียวกับที่ตรวจผลบางส่วน — นิยาม "ช่องนี้มีข้อมูลจริงไหม"
    ต้องมีที่เดียว (``0.0``/``False`` ยังนับเป็นข้อมูลตามเดิม)
    """
    if not isinstance(value, dict):
        return False
    payload = [v for k, v in value.items() if str(k).strip().lower() not in _IDENTITY_KEYS]
    return not any(_has_value(v) for v in payload)


def _present_keys(value: Any) -> Optional[set[str]]:
    """คีย์ที่ "มีข้อมูลจริง" ในผลลัพธ์ — ``None`` = ชนิดนี้ตรวจไม่ได้.

    DataFrame: คอลัมน์ที่มีอยู่ **และ** มีค่าที่ไม่ใช่ NaN อย่างน้อยหนึ่งค่า
    (คอลัมน์ที่ว่างทั้งคอลัมน์ = ไม่ได้ข้อมูล ticker นั้น ไม่ใช่ "ได้แล้วเป็นค่าว่าง")
    dict: ใช้เกณฑ์เดียวกัน — "มีคีย์" ไม่พอ ค่าต้องไม่ใช่ ``None``/``NaN`` ด้วย
    ไม่งั้น ``{"GLDM": None}`` จะถูกนับว่าครบแล้วแคชค่าที่ไม่ใช่ราคาไว้ทั้ง TTL
    """
    if isinstance(value, pd.DataFrame):
        if isinstance(value.columns, pd.MultiIndex):
            return None
        has_data = value.notna().any()
        return {_normalize_key(col) for col, ok in has_data.items() if bool(ok)}
    if isinstance(value, dict):
        return {_normalize_key(k) for k, v in value.items() if _has_value(v)}
    return None


def _incomplete_reason(value: Any, expect_keys: Iterable[str]) -> Optional[str]:
    """เหตุผลที่ผลลัพธ์นี้ห้ามแคช — ``None`` = ครบตามที่ขอ แคชได้.

    **ตรวจไม่ได้ก็ไม่ผ่าน** (fail-closed) การแคชของที่ยืนยันความครบไม่ได้ มีโอกาส
    ค้างผลบางส่วนไว้ทั้ง TTL ส่วนการไม่แคชเสียแค่เวลาคำนวณซ้ำ
    """
    wanted = {_normalize_key(k) for k in expect_keys if str(k).strip()}
    if not wanted:
        return None

    if isinstance(value, pd.DataFrame) and isinstance(value.columns, pd.MultiIndex):
        sample = list(value.columns[:3])
        return (
            f"คอลัมน์เป็น MultiIndex {sample} จึงตรวจรายตัวไม่ได้ (ไม่ใช่ว่าดึงไม่ได้ทุกตัว) "
            "— ผู้เรียกต้อง normalize ให้เหลือชื่อ ticker ชั้นเดียวก่อน"
        )

    present = _present_keys(value)
    if present is None:
        return (
            f"ตรวจความครบของผลลัพธ์ชนิด {type(value).__name__} ไม่ได้ "
            "(รองรับเฉพาะ dict/DataFrame) — ไม่แคชไว้ก่อนตามหลัก fail-closed"
        )

    missing = sorted(wanted - present)
    if missing:
        return f"ได้ข้อมูลไม่ครบ ขาด {', '.join(missing)} (ดึงไม่สำเร็จ ≠ ไม่มีข้อมูล)"
    return None


class CacheService:
    """TTL cache แบบ async สำหรับ payload ที่เป็น dict (forecast, ETF info/technical).

    มาตรฐานการรับของเข้าแคชเท่ากับ ``TTLCache`` ทุกข้อ — ค่าว่าง/``data_ok=False``
    ถูกทิ้ง และส่ง ``expect_keys`` มาตรวจผลบางส่วนได้เหมือนกัน
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._expiry: dict[str, datetime] = {}

    async def get(self, key: str) -> Optional[dict[str, Any]]:
        if key in self._cache:
            if datetime.now() < self._expiry[key]:
                return copy.deepcopy(self._cache[key])
            del self._cache[key]
            del self._expiry[key]
        return None

    async def set(
        self,
        key: str,
        value: dict,
        ttl: int,
        expect_keys: Optional[Iterable[str]] = None,
    ) -> None:
        """เก็บเฉพาะผลที่ใช้ได้จริง — ค่าว่าง/``data_ok=False``/ผลบางส่วน ถูกทิ้ง (C1).

        ``expect_keys`` คือคีย์ที่ผู้เรียก "ขอ" ไว้ ถ้าผลที่ได้ขาดตัวใดตัวหนึ่ง
        (หรือตรวจไม่ได้) จะไม่ถูกเก็บ เพื่อให้ครั้งหน้าได้ลองดึงตัวที่หายใหม่
        """
        if not is_cacheable(value):
            logger.warning("ไม่แคช %s: ผลลัพธ์ว่างหรือไม่มีข้อมูล — ต้องดึงใหม่ครั้งหน้า", key)
            return
        if _payload_is_all_empty(value):
            logger.warning(
                "ไม่แคช %s: ทุกช่องไม่มีค่า เหลือแต่คีย์ระบุตัวตน = ดึงไม่สำเร็จ "
                "(ดึงไม่สำเร็จ ≠ ไม่มีข้อมูล) — ต้องดึงใหม่ครั้งหน้า",
                key,
            )
            return
        if expect_keys is not None:
            reason = _incomplete_reason(value, expect_keys)
            if reason:
                logger.warning("ไม่แคช %s: %s", key, reason)
                return
        self._cache[key] = copy.deepcopy(value)
        self._expiry[key] = datetime.now() + timedelta(seconds=ttl)


class TTLCache:
    """Cache แบบ sync ที่ใช้ได้กับค่าใด ๆ (ไม่จำกัด dict) — thread-safe.

    ความล้มเหลวไม่ถูก cache ทุกรูปแบบ (AUDIT.md C1 / FIX_PLAN 1.4):
    ฟังก์ชันที่ raise, ผลว่าง/``data_ok=False`` (ผ่าน ``utils.cache.is_cacheable``)
    และ **ผลบางส่วน** เมื่อผู้เรียกส่ง ``expect_keys`` มาให้ตรวจ
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def get_or_compute(
        self,
        key: str,
        ttl: int,
        compute: Callable[[], T],
        expect_keys: Optional[Iterable[str]] = None,
    ) -> T:
        """คืนค่าจาก cache ถ้ายังไม่หมดอายุ ไม่งั้นเรียก ``compute`` แล้วเก็บถ้าใช้ได้.

        ``expect_keys`` คือคีย์ (ticker) ที่ผู้เรียก "ขอ" ไว้ — ใช้ตรวจว่าผลที่ได้ครบ
        หรือเป็นผลบางส่วน ผลบางส่วนจะถูกคืนให้ผู้เรียกตามปกติแต่ **ไม่ถูกแคช**
        เพื่อให้ครั้งถัดไปได้ลองดึง ticker ที่หายไปใหม่ — ผลที่ตรวจความครบไม่ได้
        (ชนิดอื่นนอกจาก dict/DataFrame) ก็ไม่ถูกแคชเช่นกัน
        """
        now = _now()
        with self._lock:
            hit = self._data.get(key)
            if hit is not None and now - hit[1] < ttl:
                return copy.deepcopy(hit[0])

        value = compute()  # นอก lock: อย่าบล็อกคำขออื่นระหว่างดึง yfinance

        if not is_cacheable(value):
            logger.warning("ไม่แคช %s: ผลลัพธ์ว่างหรือไม่มีข้อมูล — ต้องดึงใหม่ครั้งหน้า", key)
            return value

        if expect_keys is not None:
            reason = _incomplete_reason(value, expect_keys)
            if reason:
                logger.warning("ไม่แคช %s: %s", key, reason)
                return value

        with self._lock:
            self._data[key] = (copy.deepcopy(value), _now())
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


shared_cache = TTLCache()
