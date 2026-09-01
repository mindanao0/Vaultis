# -*- coding: utf-8 -*-
"""AUDIT_2026-08-06 B5 — ความล้มเหลวของ ``.info`` ห้ามค้างในแคช 6 ชั่วโมง.

อาการเดิม: ``ETFInfoService.get_info()`` ปิดท้ายด้วย ``except Exception: return
ETFInfo(symbol=sym)`` = sentinel ที่ทุกฟิลด์เป็น ``None`` แต่ **ไม่มีคีย์ ``data_ok``**
และ **ไม่ว่างเปล่า** → ``is_cacheable()`` มองว่าแคชได้ แล้วเก็บไว้ ``ETF_INFO_TTL``
(6 ชม.) ⇒ yfinance สะดุดวินาทีเดียว = "กองทุนไม่มีชื่อ" ไปอีก 6 ชั่วโมงเต็ม
โดยผู้ใช้แยกไม่ออกว่า "ดึงไม่สำเร็จ" หรือ "ไม่มีข้อมูล"

รูปที่สองของอาการเดียวกัน (ไม่ผ่าน ``except`` เลย): yfinance โดน rate-limit แล้วคืน
``.info == {}`` — ทุกฟิลด์เป็น ``None`` เหมือนกัน แต่ ``profile`` มาจากตารางฮาร์ดโค้ด
ในไฟล์เอง จึงไม่ใช่ None ⇒ เกณฑ์ "ทุกค่าเป็น None" อย่างเดียวจับไม่ได้ ต้องมีธง

ที่คุมในไฟล์นี้
- ``get_info()`` ต้องรายงานความล้มเหลวออกมา (``data_ok=False`` + เหตุผล) ไม่ใช่ปั้น
  sentinel ที่หน้าตาเหมือน "ETF ที่ไม่มีข้อมูลอะไรเลย"
- ``CacheService.set`` ต้องไม่รับ payload ที่ไม่มีค่าจริงสักช่อง (ไม่นับคีย์ระบุตัวตน)
- ผลของ technical ที่ด่าน ``_technical_fetch_failed`` จะปฏิเสธ ต้องไม่ถูกแคชก่อน
- ของที่ดีต้องยังถูกแคชเหมือนเดิม (ห้ามแก้จนแคชตายทั้งชั้น)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import backend.services.etf_info_service as info_mod  # noqa: E402
from backend.models.etf_models import TechnicalIndicators  # noqa: E402
from backend.routers import etf_analysis  # noqa: E402
from backend.services.cache_service import (  # noqa: E402
    ETF_INFO_TTL,
    CacheService,
    etf_info_cache_key,
)

_GOOD_INFO = {
    "longName": "Vanguard S&P 500 ETF",
    "regularMarketPrice": 520.0,
    "navPrice": 519.5,
    "annualReportExpenseRatio": 0.0003,
    "dividendYield": 1.24,
    "beta3Year": 1.0,
    "category": "Large Blend",
}


class _RateLimited:
    """yfinance โดน rate-limit: ไม่โยน exception แต่คืน dict ว่าง."""


class _TickerFactory:
    """แทน ``yf.Ticker`` — คืนพฤติกรรมตามคิว แล้วนับจำนวนครั้งที่ ``.info`` ถูกแตะจริง."""

    def __init__(self, *behaviours):
        self._behaviours = list(behaviours)
        self.calls = 0

    def __call__(self, symbol):
        return _FakeTicker(self, symbol)

    def _next(self):
        self.calls += 1
        idx = min(self.calls - 1, len(self._behaviours) - 1)
        return self._behaviours[idx]


class _FakeTicker:
    def __init__(self, factory, symbol):
        self._factory = factory
        self._symbol = symbol

    @property
    def info(self):
        behaviour = self._factory._next()
        if isinstance(behaviour, BaseException):
            raise behaviour
        if behaviour is _RateLimited:
            return {}
        return behaviour


def _install_ticker(monkeypatch, *behaviours) -> _TickerFactory:
    factory = _TickerFactory(*behaviours)
    monkeypatch.setattr(info_mod.yf, "Ticker", factory)
    return factory


def _technical(price: float) -> TechnicalIndicators:
    return TechnicalIndicators(
        symbol="VOO",
        price=price,
        rsi=55.0,
        ma50=500.0,
        ma200=480.0,
        signal="bullish",
    )


# ------------------------------------------------------------------ ETFInfoService


async def test_get_info_reports_failure_when_yfinance_raises(monkeypatch):
    """yfinance ล่ม = "ดึงไม่สำเร็จ" ต้องติดธง ไม่ใช่ ETF เปล่า ๆ ที่หน้าตาเหมือนไม่มีข้อมูล."""
    _install_ticker(monkeypatch, RuntimeError("yfinance ล่ม (จำลอง)"))
    info = await info_mod.ETFInfoService().get_info("VOO")

    assert info.data_ok is False, "ดึงไม่สำเร็จแต่ผลลัพธ์ยังบอกว่าข้อมูลใช้ได้"
    assert info.error, "ต้องบอกเหตุผลที่ดึงไม่สำเร็จให้ผู้ใช้/ผู้ดูแลเห็น"
    assert info.name is None and info.price is None


async def test_get_info_reports_failure_when_info_is_empty(monkeypatch):
    """``.info == {}`` (rate-limit) ไม่ได้โยน exception — แต่ก็ไม่ใช่ข้อมูล."""
    _install_ticker(monkeypatch, _RateLimited)
    info = await info_mod.ETFInfoService().get_info("VOO")

    assert info.data_ok is False, "yfinance คืน dict ว่าง แต่ถูกนับเป็นผลสำเร็จ"
    assert info.price is None


async def test_get_info_reports_failure_when_info_is_not_a_dict(monkeypatch):
    _install_ticker(monkeypatch, "ไม่ใช่ dict")
    info = await info_mod.ETFInfoService().get_info("VOO")

    assert info.data_ok is False


async def test_get_info_marks_success(monkeypatch):
    """ทางสำเร็จต้องไม่เปลี่ยน — ``data_ok`` เป็น True และค่าจริงครบ."""
    _install_ticker(monkeypatch, _GOOD_INFO)
    info = await info_mod.ETFInfoService().get_info("VOO")

    assert info.data_ok is True
    assert info.error is None
    assert info.name == "Vanguard S&P 500 ETF"
    assert info.price == 520.0


@pytest.mark.parametrize("behaviour", [RuntimeError("boom"), _RateLimited, "ไม่ใช่ dict"])
async def test_failed_info_is_never_cached(monkeypatch, behaviour):
    """หัวใจของ B5: ผลที่ล้มเหลวห้ามค้างใน CacheService นาน 6 ชม."""
    _install_ticker(monkeypatch, behaviour)
    info = await info_mod.ETFInfoService().get_info("VOO")

    cache = CacheService()
    key = etf_info_cache_key("VOO")
    await cache.set(key, info.model_dump(mode="json"), ETF_INFO_TTL)

    assert await cache.get(key) is None, "ความล้มเหลวถูกแคชไว้ 6 ชม. — ต้องดึงใหม่ครั้งหน้า"


# ------------------------------------------------- CacheService: payload ที่ไม่มีค่าจริง


async def test_all_none_payload_is_not_cached():
    """sentinel รูปเดิม (ไม่มีคีย์ ``data_ok`` เลย) ก็ยังต้องไม่ถูกแคช."""
    cache = CacheService()
    await cache.set("etf_info:VOO", {"symbol": "VOO", "name": None, "price": None}, 300)
    assert await cache.get("etf_info:VOO") is None


async def test_identity_only_payload_is_not_cached():
    cache = CacheService()
    await cache.set("etf_info:VOO", {"symbol": "VOO"}, 300)
    assert await cache.get("etf_info:VOO") is None


async def test_payload_with_one_real_value_is_cached():
    """มีค่าจริงแม้ช่องเดียวก็ยังเป็นข้อมูล — ห้ามตัดทิ้ง."""
    cache = CacheService()
    payload = {"symbol": "VOO", "name": "Vanguard S&P 500 ETF", "price": None}
    await cache.set("etf_info:VOO", payload, 300)
    assert await cache.get("etf_info:VOO") == payload


async def test_zero_value_still_counts_as_data():
    """ผลตอบแทน 0.0% คือข้อมูล ไม่ใช่ช่องว่าง (เกณฑ์เดียวกับ ``_has_value``)."""
    cache = CacheService()
    payload = {"symbol": "VOO", "ytd_return": 0.0, "name": None}
    await cache.set("etf_info:VOO", payload, 300)
    assert await cache.get("etf_info:VOO") == payload


async def test_ticker_keyed_payload_is_unaffected():
    """dict ที่คีย์เป็น ticker (ไม่ใช่คีย์ระบุตัวตน) ต้องแคชได้เหมือนเดิม."""
    cache = CacheService()
    payload = {"VOO": 520.0, "GLDM": 60.0}
    await cache.set("latest_prices", payload, 300)
    assert await cache.get("latest_prices") == payload


# ------------------------------------------------------------------ ระดับ endpoint


@pytest.fixture
def client(monkeypatch):
    """แอปเล็กที่มีเฉพาะ router นี้ + แคชสะอาด + กันเส้นทาง AI ไว้ทั้งหมด."""
    monkeypatch.setattr(etf_analysis, "_cache", CacheService())

    async def _no_ai(*_args, **_kwargs):  # เงินจริง — ห้ามมีเส้นทางไหนแตะ
        raise AssertionError("เทสต์นี้ต้องไม่เรียก AI")

    monkeypatch.setattr(etf_analysis._analysis_service, "get_ai_summary", _no_ai)

    app = FastAPI()
    app.include_router(etf_analysis.router)
    return TestClient(app, raise_server_exceptions=False)


def _stub_technical(monkeypatch, *prices):
    calls = []

    async def _get_technical(symbol):
        calls.append(symbol)
        idx = min(len(calls) - 1, len(prices) - 1)
        return _technical(prices[idx])

    monkeypatch.setattr(etf_analysis._technical_service, "get_technical", _get_technical)
    return calls


async def test_endpoint_retries_info_after_failure(monkeypatch, client):
    """.info ล้มรอบแรก + history ยังดึงได้ → รอบสองต้องได้ชื่อจริง ไม่ใช่ค่าที่ค้างจากรอบที่ล้ม."""
    ticker = _install_ticker(monkeypatch, RuntimeError("yfinance ล่ม (จำลอง)"), _GOOD_INFO)
    _stub_technical(monkeypatch, 520.0)

    first = client.get("/api/etf/VOO")
    assert first.status_code == 200
    assert first.json()["info"]["data_ok"] is False, "ความล้มเหลวต้องถูกรายงานให้ผู้ใช้เห็น"

    second = client.get("/api/etf/VOO")
    assert second.status_code == 200
    assert second.json()["info"]["name"] == "Vanguard S&P 500 ETF", (
        "ผลที่ล้มเหลวถูกแคชไว้ — กองทุนไม่มีชื่อไปอีก 6 ชม. ทั้งที่ yfinance กลับมาแล้ว"
    )
    assert ticker.calls == 2, "ไม่ได้ลองดึงใหม่เลยหลังความล้มเหลว"


async def test_endpoint_still_caches_successful_info(monkeypatch, client):
    """ห้ามแก้จนแคชตาย — ผลที่ดีต้องยังใช้ซ้ำได้."""
    ticker = _install_ticker(monkeypatch, _GOOD_INFO)
    _stub_technical(monkeypatch, 520.0)

    assert client.get("/api/etf/VOO").status_code == 200
    assert client.get("/api/etf/VOO").status_code == 200
    assert ticker.calls == 1, "ผลที่ดีไม่ถูกแคช — ยิง yfinance ซ้ำทุก request"


async def test_failed_technical_is_not_cached(monkeypatch, client):
    """``price == 0.0`` คือค่าที่ด่านของ router ปฏิเสธอยู่แล้ว — ห้ามเก็บเข้าแคชก่อนถึงด่าน."""
    _install_ticker(monkeypatch, _GOOD_INFO)
    tech_calls = _stub_technical(monkeypatch, 0.0, 520.0)

    first = client.get("/api/etf/VOO")
    assert first.status_code == 500

    second = client.get("/api/etf/VOO")
    assert len(tech_calls) == 2, "ผล technical ที่ล้มเหลวถูกแคช 15 นาที — ไม่ได้ลองใหม่"
    assert second.status_code == 200
    assert second.json()["technical"]["price"] == 520.0


# ============================================================ AUDIT_ROUND2 G5 — NaN
# ``_to_float()`` เคยเป็นตัวเดียวใน 6 ตัวแปลง float ของโปรเจกต์ที่ไม่กรอง NaN
# (``float(nan)`` สำเร็จ ไม่โยน exception) ⇒ ``ETFInfo.ytd_return = nan`` พร้อม
# ``data_ok=True`` แล้ว FastAPI serialize ด้วย ``json.dumps(allow_nan=False)``
# **นอก** ตัว handler ⇒ ``except Exception`` ในเราเตอร์ดักไม่ทัน ผู้ใช้ได้ 500 เปล่า
# และเพราะติดธง data_ok=True ค่านั้นถูกแคชค้างไว้ ``ETF_INFO_TTL`` = 6 ชม.


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_to_float_rejects_non_finite(value):
    """NaN/inf ไม่ใช่ตัวเลขที่ใช้ได้ — ต้องคืน ``None`` เหมือน ``technical_service._scalar_float``."""
    assert info_mod._to_float(value) is None


@pytest.mark.parametrize("value,expected", [(0.0, 0.0), (-3.5, -3.5), ("1.25", 1.25), (7, 7.0)])
def test_to_float_keeps_real_numbers(value, expected):
    """ห้ามกรองจนตัวเลขจริงหาย — ``0.0`` คือข้อมูล (ผลตอบแทน 0% ก็คือคำตอบ)."""
    assert info_mod._to_float(value) == expected


def test_optional_str_rejects_nan():
    """ช่องข้อความจาก pandas เป็น NaN ได้ — ``str(nan)`` = ``"nan"`` คือการกุข้อมูล."""
    assert info_mod._optional_str(float("nan")) is None


async def test_nan_price_falls_through_to_next_source(monkeypatch):
    """NaN เป็น truthy ⇒ สำนวน ``a or b`` เลือก NaN ทิ้งราคาจริงที่อยู่ช่องถัดไป."""
    _install_ticker(
        monkeypatch,
        {**_GOOD_INFO, "currentPrice": float("nan"), "regularMarketPrice": 520.0},
    )
    info = await info_mod.ETFInfoService().get_info("VOO")

    assert info.price == 520.0, "ราคาจริงในช่องถัดไปถูก NaN บังไว้ — ตัดข้อมูลทิ้งเงียบ ๆ"


async def test_nan_long_name_falls_through_to_short_name(monkeypatch):
    """ชื่อสำรองก็ถูก NaN บังได้ด้วยสำนวนเดียวกัน — และ ``str(nan)`` = ``"nan"``."""
    info_raw = {k: v for k, v in _GOOD_INFO.items() if k != "longName"}
    _install_ticker(monkeypatch, {**info_raw, "longName": float("nan"), "shortName": "VOO ETF"})
    info = await info_mod.ETFInfoService().get_info("VOO")

    assert info.name == "VOO ETF", "ชื่อจริงในช่องสำรองหายไปเพราะ NaN ในช่องแรก"


async def test_zero_price_is_not_a_price(monkeypatch):
    """0 คือ "ไม่มีค่า" ของ yfinance ไม่ใช่ราคา — ห้ามกลายเป็นราคา $0.00."""
    _install_ticker(
        monkeypatch,
        {**_GOOD_INFO, "currentPrice": 0.0, "regularMarketPrice": 0.0, "navPrice": 0.0},
    )
    info = await info_mod.ETFInfoService().get_info("VOO")

    assert info.price is None, "ราคา 0 ถูกรายงานเป็นราคาจริง"


async def test_all_fields_nan_is_reported_as_fetch_failure(monkeypatch):
    """ทุกช่องเป็น NaN = ไม่ได้ข้อมูลอะไรเลย ⇒ ต้องเป็น "ดึงไม่สำเร็จ" ไม่ใช่ ETF ว่าง ๆ."""
    nan = float("nan")
    _install_ticker(
        monkeypatch,
        {"longName": nan, "regularMarketPrice": nan, "ytdReturn": nan, "beta3Year": nan},
    )
    info = await info_mod.ETFInfoService().get_info("VOO")

    assert info.data_ok is False
    assert info.error


async def test_nan_field_becomes_null_instead_of_blank_500(monkeypatch, client):
    """หัวใจของ G5: ช่องเสริมช่องเดียวเป็น NaN ต้องไม่ทำให้ทั้ง endpoint ตาย."""
    _install_ticker(monkeypatch, {**_GOOD_INFO, "ytdReturn": float("nan")})
    _stub_technical(monkeypatch, 520.0)

    resp = client.get("/api/etf/VOO")
    assert resp.status_code == 200, (
        f"NaN ช่องเดียวทำให้ /api/etf/VOO ตอบ {resp.status_code} เปล่า ๆ (json.dumps ตายนอก handler)"
    )
    payload = resp.json()["info"]
    assert payload["ytd_return"] is None, "ช่องที่อ่านไม่ได้ต้องเป็น null ไม่ใช่ NaN"
    assert payload["name"] == "Vanguard S&P 500 ETF", "ช่องที่ดีต้องยังอยู่ครบ"
    assert payload["price"] == 520.0


async def test_compare_endpoint_survives_nan_field(monkeypatch, client):
    _install_ticker(monkeypatch, {**_GOOD_INFO, "ytdReturn": float("nan")})
    _stub_technical(monkeypatch, 520.0)

    resp = client.get("/api/etf/compare?symbols=VOO")
    assert resp.status_code == 200, "หน้าเปรียบเทียบล่มทั้งหน้าเพราะ NaN ช่องเดียว"
    assert resp.json()["analyses"][0]["info"]["ytd_return"] is None


async def test_dropped_nan_field_is_logged(monkeypatch, caplog):
    """"ค่านี้อ่านไม่ได้" ต้องไม่หายเงียบ — null ในคำตอบบอกผู้ใช้ log บอกผู้ดูแล."""
    _install_ticker(monkeypatch, {**_GOOD_INFO, "ytdReturn": float("nan")})

    with caplog.at_level("WARNING", logger=info_mod.logger.name):
        info = await info_mod.ETFInfoService().get_info("VOO")

    assert info.ytd_return is None
    assert any("ytdReturn" in rec.getMessage() for rec in caplog.records), (
        "ช่องที่ถูกทิ้งเพราะ NaN ไม่ถูกรายงานที่ไหนเลย"
    )


async def test_nan_payload_is_never_cached():
    """ชั้นกันสอง: ถ้ามี NaN หลุดเข้ามาอีก ห้ามค้างในแคช 6 ชม. (endpoint จะ 500 ทุกครั้งจนหมดอายุ)."""
    cache = CacheService()
    key = etf_info_cache_key("VOO")
    payload = {"symbol": "VOO", "data_ok": True, "name": "Vanguard S&P 500 ETF", "ytd_return": float("nan")}

    await cache.set(key, payload, ETF_INFO_TTL)

    assert await cache.get(key) is None, "payload ที่ serialize เป็น JSON ไม่ได้ ถูกแคชไว้ 6 ชม."


async def test_nested_non_finite_payload_is_never_cached():
    """NaN ที่ซ่อนใน dict ซ้อน (เช่นผล backtest ของ forecast) ก็ทำให้ทั้งคำตอบ serialize ไม่ได้."""
    cache = CacheService()
    await cache.set("forecast:VOO", {"symbol": "VOO", "backtest": {"mae": float("nan")}}, 300)
    assert await cache.get("forecast:VOO") is None
