# -*- coding: utf-8 -*-
"""ทดสอบ ``backend/services/cache_service.py`` — TTL cache ของ request path (FIX_PLAN 1.4).

หลักที่คุม (เดียวกับ ``utils/cache.py`` และกฎ fail-loud ของโปรเจกต์):
- ความล้มเหลว/"ไม่มีข้อมูล" ต้องไม่ค้างใน cache: ``{}``/``None``/frame ว่าง/``data_ok=False``
- **ผลบางส่วนก็คือความล้มเหลว** — ขอ 5 ticker ได้ 4 ห้ามแคช ไม่งั้น ticker ที่หายไป
  ค้างหายทั้ง TTL แล้วผู้เรียกปลายทางเข้าใจว่า "ไม่มีข้อมูล" แทน "ดึงไม่สำเร็จ"
- ต้องคืน **สำเนา** เสมอ ผู้เรียกแก้ผลลัพธ์แล้วต้องไม่ทำของในแคชสกปรกข้าม request
- ของที่ดีต้องแคชได้จริงและหมดอายุตาม TTL (monkeypatch นาฬิกา ไม่ sleep จริง)
- **ตรวจไม่ได้ = ไม่แคช** (fail-closed) ชนิดผลลัพธ์ที่ตรวจความครบไม่ได้ต้องไม่ถูกแคช
  ต้นทุนคือคำนวณซ้ำ ซึ่งถูกกว่าการค้างผลบางส่วนไว้ทั้ง TTL
"""

import logging

import pandas as pd
import pytest

import backend.services.cache_service as cache_mod
from backend.services.cache_service import CacheService, TTLCache

TICKERS = ["VOO", "QQQM", "SCHD", "XLV", "GLDM"]


def _counting(value_factory):
    """ห่อ compute ให้นับจำนวนครั้งที่ถูกเรียกจริง."""
    calls = []

    def inner():
        calls.append(1)
        return value_factory()

    inner.calls = calls
    return inner


def _frame(tickers, rows=3):
    idx = pd.date_range("2024-01-01", periods=rows, freq="D")
    return pd.DataFrame({t: [100.0 + i for i in range(rows)] for t in tickers}, index=idx)


@pytest.fixture
def clock(monkeypatch):
    """นาฬิกาปลอมสำหรับทดสอบ TTL — ไม่ต้องรอเวลาจริง."""
    now = [1000.0]
    monkeypatch.setattr(cache_mod, "_now", lambda: now[0])
    return now


# ---------------------------------------------------------------- ห้ามแคชความล้มเหลว


def test_empty_dict_is_not_cached():
    """yfinance rate-limit → ``get_current_prices`` คืน ``{}`` ห้ามค้าง 5 นาที."""
    cache = TTLCache()
    compute = _counting(dict)
    assert cache.get_or_compute("latest_prices", 300, compute) == {}
    assert cache.get_or_compute("latest_prices", 300, compute) == {}
    assert len(compute.calls) == 2, "ผลว่างถูกแคชไว้ — ความล้มเหลวต้องเกิดซ้ำทุกครั้ง"


def test_none_is_not_cached():
    cache = TTLCache()
    compute = _counting(lambda: None)
    cache.get_or_compute("k", 300, compute)
    cache.get_or_compute("k", 300, compute)
    assert len(compute.calls) == 2


def test_empty_frame_is_not_cached():
    cache = TTLCache()
    compute = _counting(pd.DataFrame)
    cache.get_or_compute("prices", 3600, compute)
    cache.get_or_compute("prices", 3600, compute)
    assert len(compute.calls) == 2


def test_data_ok_false_is_not_cached():
    """``data_ok=False`` คือสถานะ NO DATA กลางของระบบ — ห้ามแคช."""
    cache = TTLCache()
    compute = _counting(lambda: {"data_ok": False, "signal": "no_data"})
    cache.get_or_compute("tech:VOO", 900, compute)
    cache.get_or_compute("tech:VOO", 900, compute)
    assert len(compute.calls) == 2


def test_exception_is_not_cached():
    cache = TTLCache()

    def boom():
        raise RuntimeError("ดึงราคาไม่สำเร็จ")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            cache.get_or_compute("k", 300, boom)


# ---------------------------------------------------------------- ผลบางส่วน


def test_partial_dict_is_not_cached():
    """ขอ 5 ticker ได้ 4 — ห้ามแคช เพราะตัวที่หายคือ 'ดึงไม่สำเร็จ' ไม่ใช่ 'ไม่มี'."""
    cache = TTLCache()
    partial = {t: 100.0 for t in TICKERS if t != "GLDM"}
    compute = _counting(lambda: dict(partial))

    first = cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    assert set(first) == set(TICKERS) - {"GLDM"}
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 2, "ผลบางส่วนถูกแคช — GLDM จะหายไปทั้ง TTL"


def test_partial_frame_is_not_cached():
    cache = TTLCache()
    compute = _counting(lambda: _frame([t for t in TICKERS if t != "XLV"]))
    cache.get_or_compute("prices_10y", 3600, compute, expect_keys=TICKERS)
    cache.get_or_compute("prices_10y", 3600, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 2


def test_frame_column_all_nan_counts_as_missing():
    """คอลัมน์มีอยู่แต่ว่างทั้งคอลัมน์ = ไม่ได้ข้อมูล ticker นั้น."""
    cache = TTLCache()

    def build():
        df = _frame(TICKERS)
        df["GLDM"] = float("nan")
        return df

    compute = _counting(build)
    cache.get_or_compute("prices_10y", 3600, compute, expect_keys=TICKERS)
    cache.get_or_compute("prices_10y", 3600, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 2


def test_complete_result_is_cached(clock):
    cache = TTLCache()
    compute = _counting(lambda: {t: 100.0 for t in TICKERS})
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 1


def test_expect_keys_is_case_insensitive(clock):
    cache = TTLCache()
    compute = _counting(lambda: {t: 100.0 for t in TICKERS})
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=[t.lower() for t in TICKERS])
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=[t.lower() for t in TICKERS])
    assert len(compute.calls) == 1


# ------------------------------------------------- ตรวจไม่ได้ = ไม่แคช (fail-closed)


def test_uncheckable_list_is_not_cached(clock):
    """ขอ 5 ticker แต่ compute คืน list — ตรวจครบไม่ได้ ห้ามแคช (เดิม fail-open)."""
    cache = TTLCache()
    compute = _counting(lambda: [100.0, 101.0])
    cache.get_or_compute("k", 300, compute, expect_keys=TICKERS)
    cache.get_or_compute("k", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 2, "ชนิดที่ตรวจไม่ได้ถูกแคช — fail-open บนชั้นที่ต้อง fail-closed"


def test_uncheckable_scalar_is_not_cached(clock):
    cache = TTLCache()
    compute = _counting(lambda: 123.0)
    cache.get_or_compute("k", 300, compute, expect_keys=TICKERS)
    cache.get_or_compute("k", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 2


def test_uncheckable_series_is_not_cached(clock):
    cache = TTLCache()
    compute = _counting(lambda: pd.Series([1.0, 2.0], index=["VOO", "GLDM"]))
    cache.get_or_compute("k", 300, compute, expect_keys=TICKERS)
    cache.get_or_compute("k", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 2


def test_uncheckable_log_names_the_type(clock, caplog):
    cache = TTLCache()
    with caplog.at_level(logging.WARNING, logger=cache_mod.__name__):
        cache.get_or_compute("k", 300, lambda: [1.0], expect_keys=TICKERS)
    assert "list" in caplog.text, "log ต้องบอกชนิดที่เจอ ไม่งั้นไล่ต้นเหตุไม่ได้"


def test_uncheckable_type_without_expect_keys_is_still_cached(clock):
    """ไม่ได้ขอคีย์ไว้ = ไม่มีอะไรให้ตรวจ — ยังแคชได้ตามปกติ (อย่าแก้เกิน)."""
    cache = TTLCache()
    compute = _counting(lambda: [100.0, 101.0])
    cache.get_or_compute("k", 300, compute)
    cache.get_or_compute("k", 300, compute)
    assert len(compute.calls) == 1


def test_multiindex_frame_is_not_cached_with_readable_log(clock, caplog):
    """yfinance คืน MultiIndex → ต้องไม่แคช และ log ต้องไม่หลอกว่า "ขาดทุก ticker"."""
    cache = TTLCache()

    def build():
        cols = pd.MultiIndex.from_product([["Close"], TICKERS])
        return pd.DataFrame([[100.0] * len(TICKERS)], columns=cols)

    compute = _counting(build)
    with caplog.at_level(logging.WARNING, logger=cache_mod.__name__):
        cache.get_or_compute("prices_10y", 3600, compute, expect_keys=TICKERS)
        cache.get_or_compute("prices_10y", 3600, compute, expect_keys=TICKERS)

    assert len(compute.calls) == 2
    assert "MultiIndex" in caplog.text, "log ต้องบอกว่ารูปคอลัมน์ผิด ไม่ใช่ 'ดึงไม่ได้ทุกตัว'"
    assert "ขาด VOO" not in caplog.text


# ------------------------------------------------- dict: None/NaN = ขาด (เกณฑ์เดียวกับ DataFrame)


def test_dict_with_none_value_is_not_cached(clock):
    """คีย์มีอยู่แต่ค่าเป็น ``None`` = ไม่ได้ข้อมูล ticker นั้น — เกณฑ์เดียวกับคอลัมน์ NaN ล้วน."""
    cache = TTLCache()

    def build():
        d = {t: 100.0 for t in TICKERS}
        d["GLDM"] = None
        return d

    compute = _counting(build)
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 2, "dict ที่มี None ถูกแคช — GLDM หายไปทั้ง TTL"


def test_dict_with_nan_value_is_not_cached(clock):
    cache = TTLCache()

    def build():
        d = {t: 100.0 for t in TICKERS}
        d["GLDM"] = float("nan")
        return d

    compute = _counting(build)
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 2, "dict ที่มี NaN ถูกแคช — NaN ไม่ใช่ราคา"


def test_dict_with_data_ok_false_entry_is_not_cached(clock):
    """ผลรายตัวที่ ``data_ok=False`` คือสถานะ NO DATA — นับว่าขาด."""
    cache = TTLCache()

    def build():
        d = {t: {"price": 100.0, "data_ok": True} for t in TICKERS}
        d["GLDM"] = {"price": None, "data_ok": False}
        return d

    compute = _counting(build)
    cache.get_or_compute("snapshot", 300, compute, expect_keys=TICKERS)
    cache.get_or_compute("snapshot", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 2


def test_dict_with_zero_and_false_values_is_cached(clock):
    """``0.0``/``False`` เป็นค่าที่ใช้ได้จริง (เช่นผลตอบแทน 0%) ห้ามนับว่าขาด."""
    cache = TTLCache()

    def build():
        d = {t: 100.0 for t in TICKERS}
        d["GLDM"] = 0.0
        d["XLV"] = False
        return d

    compute = _counting(build)
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 1, "ค่า 0.0/False ที่ถูกต้องถูกตัดทิ้ง — ตรวจเข้มเกินไป"


def test_missing_value_log_names_the_ticker(clock, caplog):
    cache = TTLCache()

    def build():
        d = {t: 100.0 for t in TICKERS}
        d["GLDM"] = None
        return d

    with caplog.at_level(logging.WARNING, logger=cache_mod.__name__):
        cache.get_or_compute("latest_prices", 300, build, expect_keys=TICKERS)
    assert "GLDM" in caplog.text


# ---------------------------------------------------------------- คืนสำเนา


def test_returns_copy_of_dict(clock):
    cache = TTLCache()
    compute = _counting(lambda: {t: 100.0 for t in TICKERS})

    first = cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    first["VOO"] = -999.0
    first.pop("SCHD")

    second = cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 1, "ควรยัง cache hit อยู่"
    assert second["VOO"] == 100.0, "ผู้เรียกแก้ผลลัพธ์แล้วของในแคชสกปรกตาม"
    assert "SCHD" in second


def test_returns_copy_of_frame(clock):
    cache = TTLCache()
    compute = _counting(lambda: _frame(TICKERS))

    first = cache.get_or_compute("prices_10y", 3600, compute, expect_keys=TICKERS)
    first.iloc[0, 0] = -1.0

    second = cache.get_or_compute("prices_10y", 3600, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 1
    assert second.iloc[0, 0] == 100.0


def test_nested_dict_is_copied_deeply(clock):
    cache = TTLCache()
    compute = _counting(lambda: {"VOO": {"price": 100.0, "signal": "buy"}})

    first = cache.get_or_compute("snapshot", 300, compute)
    first["VOO"]["price"] = 0.0

    second = cache.get_or_compute("snapshot", 300, compute)
    assert len(compute.calls) == 1
    assert second["VOO"]["price"] == 100.0


# ---------------------------------------------------------------- TTL


def test_entry_expires_after_ttl(clock):
    cache = TTLCache()
    compute = _counting(lambda: {t: 100.0 for t in TICKERS})

    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    clock[0] += 299.0
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 1, "ยังไม่ครบ TTL ต้องเป็น hit"

    clock[0] += 2.0
    cache.get_or_compute("latest_prices", 300, compute, expect_keys=TICKERS)
    assert len(compute.calls) == 2, "ครบ TTL แล้วต้องคำนวณใหม่"


def test_clear_drops_entries(clock):
    cache = TTLCache()
    compute = _counting(lambda: {"VOO": 1.0})
    cache.get_or_compute("k", 300, compute)
    cache.clear()
    cache.get_or_compute("k", 300, compute)
    assert len(compute.calls) == 2


# ---------------------------------------------------------------- CacheService (async)


async def test_cacheservice_does_not_cache_empty_dict():
    svc = CacheService()
    await svc.set("k", {}, 300)
    assert await svc.get("k") is None


async def test_cacheservice_does_not_cache_data_ok_false():
    svc = CacheService()
    await svc.set("k", {"data_ok": False}, 300)
    assert await svc.get("k") is None


async def test_cacheservice_returns_copy():
    svc = CacheService()
    await svc.set("k", {"nested": {"price": 100.0}}, 300)

    got = await svc.get("k")
    got["nested"]["price"] = 0.0

    again = await svc.get("k")
    assert again["nested"]["price"] == 100.0


async def test_cacheservice_rejects_partial_result():
    """สองชั้นแคชต้องมีมาตรฐานเดียวกัน — ผลบางส่วนห้ามค้างในชั้น async ด้วย."""
    svc = CacheService()
    await svc.set("latest", {t: 100.0 for t in TICKERS if t != "GLDM"}, 300, expect_keys=TICKERS)
    assert await svc.get("latest") is None


async def test_cacheservice_rejects_none_and_nan_values():
    svc = CacheService()
    partial = {t: 100.0 for t in TICKERS}
    partial["GLDM"] = None
    await svc.set("latest", partial, 300, expect_keys=TICKERS)
    assert await svc.get("latest") is None

    partial["GLDM"] = float("nan")
    await svc.set("latest", partial, 300, expect_keys=TICKERS)
    assert await svc.get("latest") is None


async def test_cacheservice_caches_complete_result():
    svc = CacheService()
    complete = {t: 100.0 for t in TICKERS}
    await svc.set("latest", complete, 300, expect_keys=TICKERS)
    assert await svc.get("latest") == complete


# ------------------------------------------------- shared_cache ต้องไม่รั่วข้ามเทสต์
# ``shared_cache`` เป็น global ระดับ module (etf_service / market_analysis_service ใช้ร่วม)
# ทำให้สกปรกตั้งแต่ตอน import คือ "ก่อน fixture ใด ๆ ทำงาน" จึงตรวจได้โดยไม่ขึ้นกับลำดับเคส
# (pytest-randomly สลับลำดับ — เทสต์ที่ผูกกันเป็นคู่จะจับ regression ได้แค่บางรอบ)

_LEAK_KEY = "leak-probe:shared_cache"
cache_mod.shared_cache.get_or_compute(_LEAK_KEY, 3600, lambda: {"VOO": 100.0})


def test_shared_cache_is_cleared_before_each_test():
    """fixture ``_isolate_ttl_caches`` ต้องล้าง shared_cache ด้วย ไม่ใช่แค่ ``utils.cache``."""
    compute = _counting(lambda: {"VOO": 100.0})
    cache_mod.shared_cache.get_or_compute(_LEAK_KEY, 3600, compute)
    assert len(compute.calls) == 1, "ของเก่าใน shared_cache รั่วเข้ามา — conftest ต้องล้างให้ด้วย"
