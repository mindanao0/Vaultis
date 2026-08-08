"""In-process TTL cache สำหรับชั้น analysis/backend (AUDIT.md H3).

เดิม ``cache_data_1h`` เป็น no-op → ทุก request ของ backend ที่ผ่านฟังก์ชันเหล่านี้
ยิง yfinance/FRED ใหม่ทั้งหมด จนโดน rate limit แล้วกลายเป็นสัญญาณปลอม (C1)

หลักการ (สอดคล้อง AUDIT.md C1 — ความล้มเหลวต้องเกิดซ้ำ ไม่ค้างเป็นผลลัพธ์):
- exception ไม่ถูก cache — เรียกครั้งถัดไปได้ลองใหม่เสมอ
- ค่าที่แปลว่า "ไม่มีข้อมูล" ไม่ถูก cache: ``None``, dict/list/DataFrame/Series ว่าง,
  และ dict ที่ ``data_ok=False`` (สถานะ NO DATA กลางของระบบ)
- คืน "สำเนาลึก" เสมอ ทั้งเส้นทาง hit และ miss — ผู้เรียกแก้ผลลัพธ์ได้โดยไม่ทำ cache
  สกปรกข้าม caller (เส้นทาง hit เคยไม่มีเทสต์คุ้มกันเลย: ถอด deepcopy ออกแล้วทั้งชุด
  ยังเขียว 1297 ตัว — AUDIT_ROUND2_2026-08-07; ตาข่ายตอนนี้อยู่ที่
  ``tests/test_cache.py::test_mutating_value_from_cache_hit_does_not_poison_store``
  และเพื่อน ๆ ในบล็อก "สำเนาฝั่ง cache hit")
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


_MISS = object()  # sentinel: แยก "ไม่มีใน cache" ออกจากค่า ``None`` ที่ฟังก์ชันคืนได้จริง


def _copy_out(value: Any) -> Any:
    """สำเนาลึกของค่าที่จะข้ามเส้นเข้า/ออกคลัง — จุดเดียวของกติกา "คืนสำเนาเสมอ".

    ต้อง **ลึก** ไม่ใช่ ``dict(...)`` หรือ ``df.copy()`` เพราะผลลัพธ์จริงของระบบเป็น
    dict ที่มี DataFrame/dict/list ซ้อนอยู่ข้างใน (เช่นผลคะแนนที่พ่วงตารางราคา)
    shallow copy จะปล่อยให้ผู้เรียกแก้ของข้างในแล้วเลอะถึงคลัง
    ข้อจำกัดที่รู้ตัว: ``deepcopy`` ของ DataFrame/Series เรียก ``copy(deep=True)``
    ซึ่ง **ไม่** สำเนาค่าข้างในเซลล์ dtype=object (ทุกฟังก์ชันที่ถูกครอบวันนี้คืนตาราง
    ตัวเลขล้วน จึงยังไม่กระทบ — ถ้าจะแคชตารางที่เก็บ list/dict ในเซลล์ ต้องแก้ตรงนี้)
    """
    return copy.deepcopy(value)


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


# ชื่อสาธารณะของตัวกรองเดียวกัน — ``backend/services/cache_service.py`` ใช้ตัวนี้ร่วม
# (นิยาม "ผลลัพธ์ที่แคชได้" ต้องมีที่เดียวทั้งระบบ ห้ามเขียนตัวที่สอง)
is_cacheable = _is_cacheable


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
                fresh = hit is not None and now - hit[1] < ttl_seconds
                cached_value = hit[0] if fresh else _MISS

            if cached_value is not _MISS:
                # สำเนา **นอก lock** โดยตั้งใจ: ของในคลังไม่เคยถูกแก้ในที่ (มีแต่ถูกแทน
                # ทั้งก้อนหรือถูกลบ) เราถือ reference ไว้แล้วจึงปลอดภัย ถ้าสำเนาใต้ lock
                # ผู้เรียกทุกคนของฟังก์ชันเดียวกันจะรอตามกันเป็นแถวตามขนาดของก้อนข้อมูล
                # (วัดจริง: dict คะแนน/ตาราง 5 ETF ≈ 0.015 ms, ตาราง 25,000 แถว ≈ 0.05 ms,
                #  dict ที่มี 50 ตาราง ≈ 0.6 ms — ตาข่าย: test_hit_copy_does_not_block_other_callers)
                return _copy_out(cached_value)

            value = func(*args, **kwargs)  # นอก lock: อย่าบล็อกคำขออื่นระหว่างดึงข้อมูล

            if _is_cacheable(value):
                with lock:
                    # เก็บ "สำเนา" ไว้ในคลัง แล้วคืน ``value`` ตัวจริงให้ผู้เรียกที่เป็นคน
                    # คำนวณ — ผู้เรียกแก้ของตัวเองได้โดยไม่แตะคลัง (1 สำเนาต่อ 1 call)
                    store[key] = (_copy_out(value), _now())
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
