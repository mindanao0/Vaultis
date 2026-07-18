# -*- coding: utf-8 -*-
"""ทดสอบ utils/cache.py — TTL memoizer จริงแทน no-op เดิม (AUDIT.md H3).

หลักที่คุม: ความล้มเหลว (exception/ค่าว่าง/data_ok=False) ต้องไม่ค้างใน cache (C1),
ผลลัพธ์ต้องเป็นสำเนา, key ต้องคิดจากเนื้อหา argument ไม่ใช่ identity
"""

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
