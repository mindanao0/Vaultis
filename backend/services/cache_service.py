"""In-process TTL cache สำหรับ request path ของ backend (AUDIT.md H3).

เดิม ``utils/cache.cache_data_1h`` เป็น no-op และ CacheService นี้ถูกใช้แค่ในหน้า
ETF analysis เท่านั้น → ทุก request ของ /api/etf/* ยิง yfinance ใหม่ทั้งหมด
(ราคา 10 ปี × 5 ตัว) ทำให้โดน rate limit บ่อยจนกลายเป็นสัญญาณปลอม (C1)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Optional, TypeVar

ETF_INFO_TTL = 6 * 60 * 60  # 6 hours
TECHNICAL_TTL = 15 * 60  # 15 minutes
PRICE_HISTORY_TTL = 60 * 60  # 1 hour

T = TypeVar("T")


def etf_info_cache_key(symbol: str) -> str:
    return f"etf_info:{symbol.strip().upper()}"


def etf_technical_cache_key(symbol: str) -> str:
    return f"etf_technical:{symbol.strip().upper()}"


class CacheService:
    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._expiry: dict[str, datetime] = {}

    async def get(self, key: str) -> Optional[dict[str, Any]]:
        if key in self._cache:
            if datetime.now() < self._expiry[key]:
                return self._cache[key]
            del self._cache[key]
            del self._expiry[key]
        return None

    async def set(self, key: str, value: dict, ttl: int) -> None:
        self._cache[key] = value
        self._expiry[key] = datetime.now() + timedelta(seconds=ttl)


class TTLCache:
    """Cache แบบ sync ที่ใช้ได้กับค่าใด ๆ (ไม่จำกัด dict) — thread-safe.

    ค่าที่ผลิตจากฟังก์ชันที่ raise จะไม่ถูก cache (ความล้มเหลวต้องเกิดใหม่ทุกครั้ง
    ไม่ใช่ค้างเป็นผลลัพธ์ — AUDIT.md C1)
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key: str, ttl: int, compute: Callable[[], T]) -> T:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit is not None and now - hit[1] < ttl:
                return hit[0]

        value = compute()  # นอก lock: อย่าบล็อกคำขออื่นระหว่างดึง yfinance

        with self._lock:
            self._data[key] = (value, time.monotonic())
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


shared_cache = TTLCache()
