# -*- coding: utf-8 -*-
"""ทดสอบ utils/cache.py — TTL memoizer จริงแทน no-op เดิม (AUDIT.md H3).

หลักที่คุม: ความล้มเหลว (exception/ค่าว่าง/data_ok=False) ต้องไม่ค้างใน cache (C1),
ผลลัพธ์ต้องเป็นสำเนา, key ต้องคิดจากเนื้อหา argument ไม่ใช่ identity
"""

import threading

import pandas as pd
import pytest

import utils.cache as cache_mod
from utils.cache import cache_data_1h, clear_all_caches, ttl_cache


def _counting(fn):
    """ห่อฟังก์ชันให้นับจำนวนครั้งที่ถูกคำนวณจริง."""
    calls = []

    def inner(*args, **kwargs):
        calls.append((args, kwargs))
        return fn(*args, **kwargs)

    inner.calls = calls
    return inner


def test_hit_within_ttl_computes_once():
    compute = _counting(lambda x: x * 2)
    cached = cache_data_1h(compute)
    assert cached(21) == 42
    assert cached(21) == 42
    assert len(compute.calls) == 1
    # argument ต่างกัน = คนละ entry
    assert cached(5) == 10
    assert len(compute.calls) == 2


def test_expiry_recomputes(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(cache_mod, "_now", lambda: clock[0])
    compute = _counting(lambda: "ok")
    cached = ttl_cache(10.0)(compute)

    assert cached() == "ok"
    clock[0] = 9.9
    assert cached() == "ok"
    assert len(compute.calls) == 1  # ยังไม่หมดอายุ

    clock[0] = 10.1
    assert cached() == "ok"
    assert len(compute.calls) == 2  # หมดอายุแล้วต้องคำนวณใหม่


def test_exception_not_cached():
    state = {"fail": True}
    calls = []

    @cache_data_1h
    def flaky():
        calls.append(1)
        if state["fail"]:
            raise RuntimeError("ดึงข้อมูลไม่สำเร็จ")
        return {"data_ok": True, "score": 7}

    with pytest.raises(RuntimeError):
        flaky()
    with pytest.raises(RuntimeError):
        flaky()  # ความล้มเหลวต้องเกิดซ้ำ ไม่ถูก cache
    assert len(calls) == 2

    state["fail"] = False
    assert flaky()["score"] == 7
    assert flaky()["score"] == 7
    assert len(calls) == 3  # สำเร็จแล้วค่อย cache


@pytest.mark.parametrize(
    "empty_value",
    [None, {}, [], pd.DataFrame(), pd.Series(dtype=float), {"data_ok": False, "signal": "NO DATA"}],
)
def test_no_data_results_not_cached(empty_value):
    compute = _counting(lambda: empty_value)
    cached = cache_data_1h(compute)
    cached()
    cached()
    assert len(compute.calls) == 2  # ค่าที่แปลว่า "ไม่มีข้อมูล" ต้องคำนวณใหม่ทุกครั้ง


def test_dataframe_key_is_content_based():
    compute = _counting(lambda df: float(df["a"].sum()))
    cached = cache_data_1h(compute)
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0]}, index=pd.bdate_range("2024-01-01", periods=3))

    assert cached(df) == 6.0
    assert cached(df.copy()) == 6.0  # คนละ object เนื้อหาเดียวกัน = hit
    assert len(compute.calls) == 1

    changed = df.copy()
    changed.iloc[0, 0] = 99.0
    assert cached(changed) == 104.0  # เนื้อหาเปลี่ยน = miss
    assert len(compute.calls) == 2


def test_returned_value_is_a_copy():
    """ค่าที่ได้จาก **miss** ถูกแก้ ต้องไม่สะสมเข้าคลัง (คุมสำเนาฝั่ง store)."""

    @cache_data_1h
    def build():
        return {"data_ok": True, "items": [1, 2]}

    first = build()
    first["items"].append(999)
    second = build()
    assert second["items"] == [1, 2]  # การแก้ผลลัพธ์ฝั่ง caller ต้องไม่สะสมใน cache


def test_returned_dataframe_is_a_copy():
    @cache_data_1h
    def build():
        return pd.DataFrame({"a": [1.0, 2.0]})

    first = build()
    first.iloc[0, 0] = 555.0
    assert build().iloc[0, 0] == 1.0


# --- สำเนาฝั่ง "cache hit" ---------------------------------------------------
# เทสต์สองตัวข้างบนแก้ค่าที่ได้จาก *miss* ซึ่งเป็นค่าที่ return ตรงจากฟังก์ชัน
# ไม่ใช่ของในคลัง → ถอด deepcopy ของเส้นทาง hit ออกก็ยังเขียว (AUDIT_ROUND2 ข้อ
# "cache hit คืน object ตัวจริง ไม่ใช่สำเนา": mutation `return hit[0]` รอดทั้ง 1297 ตัว)
# ชุดล่างนี้จึงแก้ค่าที่ได้จาก **hit** โดยเฉพาะ


def test_cache_hit_returns_a_new_object_every_time():
    compute = _counting(lambda: {"data_ok": True, "items": [1, 2]})
    cached = cache_data_1h(compute)

    first = cached()  # miss
    second = cached()  # hit
    third = cached()  # hit

    assert len(compute.calls) == 1  # ต้องเป็น hit จริง ไม่ใช่คำนวณใหม่
    assert second is not first
    assert third is not second
    assert third["items"] is not second["items"]  # ต้องลึก ไม่ใช่ shallow copy


def test_mutating_value_from_cache_hit_does_not_poison_store():
    """ผู้เรียกแก้ dict ที่ได้จาก cache hit → ผู้เรียกรายถัดไปต้องได้ของเดิม."""
    compute = _counting(lambda: {"data_ok": True, "price": 500.0, "items": [1, 2]})
    cached = cache_data_1h(compute)

    cached()  # miss — คลังเก็บสำเนาของค่านี้ไว้
    from_hit = cached()  # hit — เส้นทางที่เคยคืน object ตัวจริง
    from_hit["price"] = -999.0
    from_hit["items"].append(999)

    again = cached()
    assert again["price"] == 500.0  # ราคาที่ถูกแก้ต้องไม่ค้างในคลัง 1 ชม.
    assert again["items"] == [1, 2]
    assert len(compute.calls) == 1


def test_dataframe_from_cache_hit_is_a_copy():
    compute = _counting(lambda: pd.DataFrame({"a": [1.0, 2.0]}))
    cached = cache_data_1h(compute)

    cached()  # miss
    from_hit = cached()  # hit
    from_hit.iloc[0, 0] = 555.0

    assert cached().iloc[0, 0] == 1.0
    assert len(compute.calls) == 1


def test_series_from_cache_hit_is_a_copy():
    compute = _counting(lambda: pd.Series([1.0, 2.0], name="close"))
    cached = cache_data_1h(compute)

    cached()  # miss
    from_hit = cached()  # hit
    from_hit.iloc[0] = 555.0

    assert cached().iloc[0] == 1.0
    assert len(compute.calls) == 1


def test_nested_dataframe_and_dict_from_cache_hit_are_deep_copied():
    """DataFrame/dict ที่ซ้อนอยู่ข้างในต้องถูกสำเนาด้วย — shallow copy ไม่พอ."""
    compute = _counting(
        lambda: {
            "data_ok": True,
            "prices": pd.DataFrame({"VOO": [500.0]}),
            "meta": {"tickers": ["VOO"], "scores": {"VOO": 7.0}},
        }
    )
    cached = cache_data_1h(compute)

    cached()  # miss
    from_hit = cached()  # hit
    from_hit["prices"].iloc[0, 0] = -1.0
    from_hit["meta"]["tickers"].append("EVIL")
    from_hit["meta"]["scores"]["VOO"] = 0.0

    again = cached()
    assert again["prices"].iloc[0, 0] == 500.0
    assert again["meta"]["tickers"] == ["VOO"]
    assert again["meta"]["scores"] == {"VOO": 7.0}
    assert len(compute.calls) == 1


def test_hit_copy_does_not_block_other_callers():
    """การสำเนาตอน hit ต้องอยู่นอก lock — ของก้อนใหญ่ไม่ควรทำให้ผู้เรียกอื่นรอตามกัน."""
    armed = threading.Event()  # ติดอาวุธหลัง miss เสร็จ → บล็อกเฉพาะสำเนาของเส้นทาง hit
    in_copy = threading.Event()
    release = threading.Event()

    class _SlowToCopy:
        def __deepcopy__(self, memo):
            if armed.is_set() and not in_copy.is_set():
                in_copy.set()
                assert release.wait(timeout=10), "เทสต์ค้าง: ไม่มีใครปล่อย gate"
            return _SlowToCopy()

    cached = cache_data_1h(lambda: _SlowToCopy())
    cached()  # miss — เก็บสำเนาเข้าคลัง
    armed.set()

    slow = threading.Thread(target=cached, daemon=True)  # hit ที่สำเนาช้า
    slow.start()
    other = None
    try:
        assert in_copy.wait(timeout=10), "cache hit ไม่ได้สำเนาค่าออกมาเลย"
        other = threading.Thread(target=cached, daemon=True)  # hit ของผู้เรียกอีกคน
        other.start()
        other.join(timeout=5)
        blocked = other.is_alive()
    finally:
        release.set()
        slow.join(timeout=10)
        if other is not None:
            other.join(timeout=10)

    assert not blocked, "ผู้เรียกอีกคนถูกบล็อกระหว่างสำเนาของ cache hit (สำเนาอยู่ใต้ lock)"


def test_unkeyable_argument_calls_through():
    compute = _counting(lambda obj: len(obj))
    cached = cache_data_1h(compute)
    assert cached({1, 2, 3}) == 3  # set แปลงเป็น key ไม่ได้ → เรียกตรง ไม่พัง
    assert cached({1, 2, 3}) == 3
    assert len(compute.calls) == 2


def test_kwargs_order_irrelevant():
    compute = _counting(lambda a=0, b=0: a + b)
    cached = cache_data_1h(compute)
    assert cached(a=1, b=2) == 3
    assert cached(b=2, a=1) == 3
    assert len(compute.calls) == 1


def test_maxsize_evicts_oldest(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(cache_mod, "_now", lambda: clock[0])
    compute = _counting(lambda x: x)
    cached = ttl_cache(1000.0, maxsize=2)(compute)

    cached(1)
    clock[0] = 1.0
    cached(2)
    clock[0] = 2.0
    cached(3)  # ล้น maxsize → ตัด entry เก่าสุด (ของ 1)
    clock[0] = 3.0
    cached(3)
    cached(2)
    assert len(compute.calls) == 3  # 2 กับ 3 ยังอยู่
    cached(1)
    assert len(compute.calls) == 4  # 1 ถูกตัดไปแล้วต้องคำนวณใหม่


def test_cache_clear_and_clear_all():
    compute = _counting(lambda: {"data_ok": True})
    cached = cache_data_1h(compute)
    cached()
    cached.cache_clear()
    cached()
    assert len(compute.calls) == 2

    cached()
    clear_all_caches()
    cached()
    assert len(compute.calls) == 3


def test_production_functions_are_wrapped():
    """จุดร้อนของ AUDIT H3 ต้องถูกครอบ cache จริง (มี cache_clear จาก ttl_cache)."""
    from analysis.financial_model import calculate_signal_score, dcf_valuation
    from analysis.macro import get_macro_data

    for fn in (calculate_signal_score, dcf_valuation, get_macro_data):
        assert hasattr(fn, "cache_clear")
