"""In-process TTL cache สำหรับชั้น analysis/backend (AUDIT.md H3).

เดิม ``cache_data_1h`` เป็น no-op → ทุก request ของ backend ที่ผ่านฟังก์ชันเหล่านี้
ยิง yfinance/FRED ใหม่ทั้งหมด จนโดน rate limit แล้วกลายเป็นสัญญาณปลอม (C1)

หลักการ (สอดคล้อง AUDIT.md C1 — ความล้มเหลวต้องเกิดซ้ำ ไม่ค้างเป็นผลลัพธ์):
- exception ไม่ถูก cache — เรียกครั้งถัดไปได้ลองใหม่เสมอ
- ค่าที่แปลว่า "ไม่มีข้อมูล" ไม่ถูก cache: ``None``, dict/list/DataFrame/Series ว่าง,
  และ dict ที่ ``data_ok=False`` (สถานะ NO DATA กลางของระบบ)
- คืน "สำเนา" เสมอ — ผู้เรียกแก้ผลลัพธ์ได้โดยไม่ทำ cache สกปรกข้าม caller
- key คิดจากเนื้อหา argument (DataFrame/Series ใช้ hash ของค่า ไม่ใช่ identity)
  แบบเดียวกับ ``st.cache_data``; argument ที่แปลงเป็น key ไม่ได้ = เรียกตรงไม่ cache
"""

from __future__ import annotations

import copy
import functools
import hashlib
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

import pandas as pd

F = TypeVar("F", bound=Callable[..., Any])

# แยกเป็นตัวแปร module เพื่อให้เทสต์ monkeypatch เวลาได้โดยไม่ต้องรอจริง
_now = time.monotonic

_registry: list[Callable[[], None]] = []


class _Unkeyable(Exception):
    """argument แปลงเป็น cache key ไม่ได้ — ผู้เรียกต้อง fallback เป็นเรียกตรง."""


def _freeze(value: Any) -> Any:
    """แปลง argument เป็นค่า hashable ที่สะท้อน 'เนื้อหา' ไม่ใช่ identity."""
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return (type(value).__name__, value)
    if isinstance(value, (pd.DataFrame, pd.Series)):
        try:
            row_hashes = pd.util.hash_pandas_object(value, index=True)
        except TypeError as exc:  # dtype แปลก ๆ ที่ hash ไม่ได้
            raise _Unkeyable(str(exc)) from exc
        digest = hashlib.sha1(row_hashes.to_numpy().tobytes()).hexdigest()
        cols = tuple(map(str, value.columns)) if isinstance(value, pd.DataFrame) else (value.name,)
        return ("pd", type(value).__name__, value.shape, cols, digest)
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, tuple(_freeze(v) for v in value))
    if isinstance(value, dict):
        return ("dict", tuple(sorted((str(k), _freeze(v)) for k, v in value.items())))
    raise _Unkeyable(f"ไม่รองรับ argument ชนิด {type(value).__name__}")


def _is_cacheable(value: Any) -> bool:
    """ผลลัพธ์ที่แปลว่า 'ไม่มีข้อมูล' ห้ามค้างใน cache (AUDIT.md C1)."""
    if value is None:
        return False
    if isinstance(value, (pd.DataFrame, pd.Series)):
        return not value.empty
    if isinstance(value, dict):
        if value.get("data_ok") is False:
            return False
        return len(value) > 0
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    return True


def ttl_cache(ttl_seconds: float, maxsize: int = 64) -> Callable[[F], F]:
    """Decorator factory: memoize ผลสำเร็จของฟังก์ชันไว้ ``ttl_seconds`` วินาที."""

    def decorate(func: F) -> F:
        store: dict[Any, tuple[Any, float]] = {}
        lock = threading.Lock()

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                key = (_freeze(args), _freeze(kwargs))
            except _Unkeyable:
                return func(*args, **kwargs)

            now = _now()
            with lock:
                hit = store.get(key)
                if hit is not None and now - hit[1] < ttl_seconds:
                    return copy.deepcopy(hit[0])

            value = func(*args, **kwargs)  # นอก lock: อย่าบล็อกคำขออื่นระหว่างดึงข้อมูล

            if _is_cacheable(value):
                with lock:
                    store[key] = (copy.deepcopy(value), _now())
                    if len(store) > maxsize:
                        cutoff = _now()
                        for k in [k for k, (_, ts) in store.items() if cutoff - ts >= ttl_seconds]:
                            del store[k]
                        while len(store) > maxsize:  # ยังล้น: ตัดตัวเก่าสุดออก
                            del store[min(store, key=lambda k: store[k][1])]
            return value

        def cache_clear() -> None:
            with lock:
                store.clear()

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        _registry.append(cache_clear)
        return wrapper  # type: ignore[return-value]

    return decorate


def clear_all_caches() -> None:
    """ล้างทุก cache ที่สร้างผ่าน ``ttl_cache`` — ใช้ในเทสต์กันสถานะรั่วข้ามเคส."""
    for clear in _registry:
        clear()


def cache_data_1h(func: F) -> F:
    """Cache ผลลัพธ์ 1 ชั่วโมง — ใช้ได้ทั้ง backend/CLI (ไม่พึ่ง Streamlit runtime)."""
    return ttl_cache(3600.0)(func)
