# -*- coding: utf-8 -*-
"""FIX_PLAN ข้อ 1.7 — ทุก route ต้อง "เข้าถึงได้จริง" และไฟล์ที่เพิ่งฟื้นต้องล้มแบบไม่กลืนสาเหตุ.

อาการเดิม: ``analysis.router`` ถูก include ก่อน ``backtest.router`` และทั้งคู่ประกาศ
``POST /api/backtest`` FastAPI จับคู่ route แรกที่ path ตรงเสมอ → handler ของ
``backend/routers/backtest.py`` (BacktestEngine vectorbt RSI+MACD, ``optimize()``,
``best_params``, ``?include_ai=true``) กลายเป็นโค้ดตายทั้งไฟล์ ขณะที่ ``/openapi.json``
และ ``/docs`` ยังโฆษณา schema ของตัวที่เข้าไม่ถึง → ใครยิงตามเอกสารได้ 422 ตลอดกาล

ไฟล์นี้กันทั้งตระกูล ไม่ใช่แค่ route เดียว:

1. **path ซ้ำเป๊ะ** — คู่ ``(path, method)`` เดียวกันลงทะเบียนสองครั้ง
2. **path parameter บังกัน** — บั๊กชนิดเดียวกันแต่ string ไม่ตรงกัน เช่น ``/api/etf/{symbol}``
   ลงทะเบียนก่อน ``/api/etf/compare`` ทำให้ ``/api/etf/compare`` เข้าไม่ถึงตลอดกาล
   ข้อ (1) จับไม่ได้เลยเพราะเทียบ string ตรง ๆ จึงต้องถาม router ของ Starlette เองว่า
   URL จริงของแต่ละ route ตกที่ใคร (``TestNoShadowedRoutes`` — มีเคสพิสูจน์ว่า detector
   ตรวจเป็นจริง ไม่ได้เขียวเพราะตรวจไม่เจอ)
3. **เส้นทางความล้มเหลวของ ``routers/backtest.py``** — ไฟล์ที่เพิ่งฟื้นจากสภาพโค้ดตาย
   จึงไม่เคยถูกตรวจ: "ดึงราคาไม่สำเร็จ" ต้องไม่ถูกกลืนรวมกับ "ระบบมีข้อผิดพลาด"
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Match

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ``backend.database`` สร้างไฟล์ SQLite ตอน import — ชี้ไป tmp ไม่ให้เทสต์เขียนลง repo
os.environ.setdefault("VAULTIS_DB_PATH", str(Path(tempfile.gettempdir()) / "vaultis_test_routes.db"))

# หมายเหตุ APScheduler: ``backend.main`` แค่ "สร้าง" AsyncIOScheduler ตอน import
# ส่วน ``scheduler.start()`` กับ job 07:00 อยู่ใน lifespan ซึ่งจะทำงานต่อเมื่อใช้
# TestClient เป็น context manager เท่านั้น — ไฟล์นี้อ่าน ``app.routes`` และยิง request
# แบบไม่เข้า context manager จึงไม่จุด scheduler และไม่ยิง network
from analysis.llm import AI_DISABLED_MESSAGE  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers import backtest as backtest_router  # noqa: E402
from data.fetcher import PriceDataUnavailableError  # noqa: E402

_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_WEBSOCKET = "WEBSOCKET"


def _declared_methods(route) -> list[str]:
    """เมธอดที่ "เรา" ประกาศไว้เอง — ตัด HEAD/OPTIONS ที่ Starlette เติมให้ทิ้ง"""
    methods = getattr(route, "methods", None)
    if not methods:  # WebSocket route ไม่มี methods
        return [_WEBSOCKET]
    return sorted(m for m in methods if m in _HTTP_METHODS)


def _handler_name(route) -> str:
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return "?"
    return f"{getattr(endpoint, '__module__', '?')}.{getattr(endpoint, '__name__', '?')}"


def _route_table(application=app) -> dict[tuple[str, str], list[str]]:
    """คืน {(path, method): [ชื่อ handler แบบเต็ม, ...]} จาก route ทั้งแอป"""
    table: dict[tuple[str, str], list[str]] = defaultdict(list)
    for route in application.routes:
        path = getattr(route, "path", None)
        if path is None or getattr(route, "endpoint", None) is None:
            continue
        for method in _declared_methods(route):
            table[(path, method)].append(_handler_name(route))
    return table


def _endpoints_for(path: str, method: str) -> list[str]:
    return _route_table().get((path, method), [])


# ---------------------------------------------------------------------------
# ตรวจ shadowing: สร้าง URL จริงของแต่ละ route แล้วถาม Starlette ว่า request นั้นตกที่ใคร
# ---------------------------------------------------------------------------

_PARAM_RE = re.compile(r"\{([^}]+)\}")
# ค่าตัวอย่างต่อชนิด convertor — ต้องไม่ตรงกับ literal segment ของ route ไหนในแอป
# ไม่งั้นจะฟ้องผิดตัว (เช่นใช้คำว่า "compare" แทน {symbol} แล้วไปตรงกับ /api/etf/compare)
_PROBE_VALUES = {
    "IntegerConvertor": "1",
    "FloatConvertor": "1.5",
    "UUIDConvertor": "00000000-0000-0000-0000-000000000000",
    "PathConvertor": "vaultisprobe/leaf",
}
_PROBE_DEFAULT = "vaultisprobe"


def _concrete_path(route) -> str:
    """แทน ``{param}`` ด้วยค่าตัวอย่าง → ได้ URL ที่ route นี้ควรเป็นคนรับ"""
    convertors = getattr(route, "param_convertors", {}) or {}

    def _value(match: re.Match) -> str:
        name = match.group(1).split(":")[0]
        return _PROBE_VALUES.get(type(convertors.get(name)).__name__, _PROBE_DEFAULT)

    return _PARAM_RE.sub(_value, route.path)


def _first_full_match(application, path: str, method: str):
    """route ตัวแรกที่ match เต็ม ๆ — ตรรกะเดียวกับที่ Starlette ใช้เลือก handler จริง

    (match แบบ PARTIAL คือ path ตรงแต่เมธอดไม่ตรง ซึ่งไม่ได้บังใคร Starlette จะ
    เดินหา FULL ต่อและใช้ PARTIAL เป็น 405 ก็ต่อเมื่อไม่เจอ FULL เลย)
    """
    scope = {
        "type": "websocket" if method == _WEBSOCKET else "http",
        "path": path,
        "root_path": "",
        "headers": [],
        "query_string": b"",
        "path_params": {},
    }
    if method != _WEBSOCKET:
        scope["method"] = method
    for candidate in application.routes:
        match, _ = candidate.matches(scope)
        if match == Match.FULL:
            return candidate
    return None


def _shadowed_routes(application) -> list[str]:
    """คืนคำอธิบายของ route ที่เข้าไม่ถึง (ว่างเปล่า = ไม่มีใครถูกบัง)"""
    problems: list[str] = []
    for route in application.routes:
        if getattr(route, "path", None) is None:
            continue
        probe = _concrete_path(route)
        for method in _declared_methods(route):
            winner = _first_full_match(application, probe, method)
            if winner is route:
                continue
            if winner is None:  # ไม่ควรเกิด: route ไม่รับ URL ของตัวเอง
                problems.append(f"{method} {route.path} ({_handler_name(route)}) ไม่มี route ไหนรับ {probe} เลย")
                continue
            problems.append(
                f"{method} {route.path} ({_handler_name(route)}) เข้าไม่ถึงตลอดกาล — "
                f"{probe} ตกที่ {getattr(winner, 'path', '?')} ({_handler_name(winner)}) ที่ลงทะเบียนก่อน"
            )
    return problems


def _request_body_properties(schema: dict, path: str) -> dict:
    """คลี่ $ref ของ requestBody ที่ POST <path> แล้วคืน properties ของ schema จริง"""
    ref = schema["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    return schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]["properties"]


class TestNoDuplicateRoutes:
    def test_no_path_method_pair_is_registered_twice(self):
        dupes = {k: v for k, v in _route_table().items() if len(v) > 1}
        assert not dupes, "route ซ้ำ (ตัวหลังเข้าไม่ถึงตลอดกาล): " + "; ".join(
            f"{method} {path} → {handlers}" for (path, method), handlers in sorted(dupes.items())
        )


class TestNoShadowedRoutes:
    """path parameter บังกัน = บั๊กเดียวกับ path ซ้ำ แต่ string ไม่ตรงกันจึงจับด้วยการเทียบชื่อไม่ได้"""

    def test_no_route_is_shadowed_by_an_earlier_pattern(self):
        problems = _shadowed_routes(app)
        assert not problems, "มี route ที่เข้าไม่ถึง:\n" + "\n".join(problems)

    def test_detector_catches_a_shadowed_literal(self):
        """พิสูจน์ว่าเคสข้างบนไม่ได้เขียวเพราะ detector ตรวจไม่เป็น"""
        probe_app = FastAPI()

        @probe_app.get("/api/etf/{symbol}")
        def _by_symbol(symbol: str):  # pragma: no cover - ไม่ได้เรียกจริง
            return {}

        @probe_app.get("/api/etf/compare")
        def _compare():  # pragma: no cover - เข้าไม่ถึงอยู่แล้ว นั่นคือประเด็น
            return {}

        problems = _shadowed_routes(probe_app)
        assert len(problems) == 1, problems
        assert "/api/etf/compare" in problems[0]
        assert "/api/etf/{symbol}" in problems[0]

    def test_detector_accepts_literal_registered_first(self):
        """ลำดับที่ถูก (literal ก่อน parameter) ต้องไม่ถูกฟ้อง — กันเทสต์แดงมั่ว"""
        probe_app = FastAPI()

        @probe_app.get("/api/etf/compare")
        def _compare():  # pragma: no cover
            return {}

        @probe_app.get("/api/etf/{symbol}")
        def _by_symbol(symbol: str):  # pragma: no cover
            return {}

        assert _shadowed_routes(probe_app) == []

    def test_detector_ignores_same_path_different_method(self):
        """DELETE /x/{id} ไม่ได้บัง POST /x/check — เมธอดต่างกันไม่ใช่การบัง"""
        probe_app = FastAPI()

        @probe_app.delete("/api/alerts/{alert_id}")
        def _delete(alert_id: str):  # pragma: no cover
            return {}

        @probe_app.post("/api/alerts/check")
        def _check():  # pragma: no cover
            return {}

        assert _shadowed_routes(probe_app) == []


class TestBacktestRouteOwnership:
    """Backend Router Map ระบุว่า /api/backtest เป็นของ routers/backtest.py (vectorbt RSI+MACD)"""

    def test_api_backtest_reaches_vectorbt_router(self):
        handlers = _endpoints_for("/api/backtest", "POST")
        assert handlers, "ไม่พบ POST /api/backtest เลย"
        assert handlers[0].startswith("backend.routers.backtest."), (
            f"POST /api/backtest ควรไปที่ backend/routers/backtest.py แต่ไปที่ {handlers[0]}"
        )

    def test_api_backtest_accepts_vectorbt_schema(self):
        """schema ที่รับต้องเป็นของ BacktestEngine (symbol/start/end) ไม่ใช่ weights"""
        import typing

        from backend.models.backtest_models import BacktestRequest as VectorbtRequest

        route = next(
            r
            for r in app.routes
            if getattr(r, "path", None) == "/api/backtest" and "POST" in (getattr(r, "methods", None) or set())
        )
        # backtest.py ใช้ ``from __future__ import annotations`` → annotation เป็น str
        # ต้องคลี่ด้วย get_type_hints ไม่งั้นเทียบ class ไม่ติดทั้งที่ถูกแล้ว
        hints = typing.get_type_hints(route.endpoint)
        assert VectorbtRequest in hints.values(), (
            f"POST /api/backtest ไม่ได้รับ backtest_models.BacktestRequest — รับ {hints}"
        )

    def test_portfolio_weight_backtest_moved_under_analysis(self):
        handlers = _endpoints_for("/api/analysis/backtest", "POST")
        assert handlers, "ไม่พบ POST /api/analysis/backtest (backtest แบบน้ำหนักพอร์ต)"
        assert handlers[0].startswith("backend.routers.analysis."), (
            f"POST /api/analysis/backtest ควรอยู่ใน backend/routers/analysis.py แต่ไปที่ {handlers[0]}"
        )

    def test_openapi_documents_the_reachable_handler(self):
        """/docs ต้องโฆษณา schema ตัวที่เรียกได้จริง ไม่งั้นคนทำตามเอกสารได้ 422"""
        schema = app.openapi()
        props = _request_body_properties(schema, "/api/backtest")
        assert "symbol" in props and "weights" not in props, (
            f"openapi ยังโชว์ schema ของ handler ที่เข้าไม่ถึง: {sorted(props)}"
        )

    def test_openapi_documents_the_portfolio_weight_backtest_too(self):
        props = _request_body_properties(app.openapi(), "/api/analysis/backtest")
        assert "weights" in props and "symbol" not in props, (
            f"/api/analysis/backtest ควรรับน้ำหนักพอร์ต แต่ openapi บอกว่า {sorted(props)}"
        )


_FAKE_RESULT = {
    "symbol": "VOO",
    "start": "2020-01-01",
    "end": "2021-01-01",
    "strategy_used": "rsi_macd_3day_window",
    "total_return": 12.3456,
    "sharpe_ratio": 1.2,
    "max_drawdown": -8.5,
    "win_rate": 60.0,
    "num_trades": 5,
    "benchmark_return": 10.0,
    "outperformed": True,
}
_PAYLOAD = {"symbol": "VOO", "start": "2020-01-01", "end": "2021-01-01"}


class TestBacktestRouterFailurePaths:
    """(ข) POST /api/backtest ล้มได้ 3 แบบ ห้ามเอามารวมเป็นข้อความเดียว

    - ดึงราคาไม่สำเร็จ → 503 "ดึงราคา ... ไม่สำเร็จ" (ปัญหาข้อมูลต้นทาง ผู้ใช้ลองใหม่ได้)
    - ข้อมูลไม่พอ/ช่วงวันที่ใช้ไม่ได้ → 400 พร้อมข้อความจากต้นทาง (คำขอนี้ทำไม่ได้)
    - บั๊กจริง → 500 (ระบบมีข้อผิดพลาด)

    ทุกเคสไม่แตะ network และไม่เรียก LLM จริง: ``_engine`` กับ ``generate_summary``
    ถูก stub ไว้หมด
    """

    @pytest.fixture(autouse=True)
    def _no_api_key(self, monkeypatch):
        # ไม่ตั้ง VAULTIS_API_KEY → security.py ยอมให้ localhost (TestClient) เรียกได้
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)

    @staticmethod
    def _client(raise_server_exceptions: bool = True) -> TestClient:
        # ไม่ใช้ ``with`` → ไม่จุด lifespan (APScheduler 07:00)
        return TestClient(app, raise_server_exceptions=raise_server_exceptions)

    def test_price_failure_is_503_not_generic_500(self, monkeypatch):
        def _no_price(*_args, **_kwargs):
            raise PriceDataUnavailableError("ดึงราคา VOO ไม่สำเร็จหลังลอง 3 ครั้ง")

        monkeypatch.setattr(backtest_router._engine, "run", _no_price)
        res = self._client().post("/api/backtest", json=_PAYLOAD)

        assert res.status_code == 503, res.text
        detail = res.json()["detail"]
        assert "ดึงราคา" in detail and "ไม่สำเร็จ" in detail, detail
        assert "ระบบมีข้อผิดพลาด" not in detail, detail

    def test_price_failure_during_optimize_is_503_too(self, monkeypatch):
        def _no_price(*_args, **_kwargs):
            raise PriceDataUnavailableError("ดึงราคา VOO ไม่สำเร็จหลังลอง 3 ครั้ง")

        monkeypatch.setattr(backtest_router._engine, "optimize", _no_price)
        monkeypatch.setattr(backtest_router._engine, "run", lambda *a, **k: dict(_FAKE_RESULT))
        res = self._client().post("/api/backtest", json={**_PAYLOAD, "run_optimization": True})

        assert res.status_code == 503, res.text
        assert "ดึงราคา" in res.json()["detail"]

    def test_not_enough_data_is_400_with_reason_from_engine(self, monkeypatch):
        """ValueError ของ engine อธิบายตัวเองเป็นภาษาไทยแล้ว — ต้องส่งต่อ ไม่ใช่กลบเป็น 500"""

        def _too_short(*_args, **_kwargs):
            raise ValueError("ข้อมูลไม่พอแบ่งช่วง train/test สำหรับการ optimize")

        monkeypatch.setattr(backtest_router._engine, "optimize", _too_short)
        res = self._client().post("/api/backtest", json={**_PAYLOAD, "run_optimization": True})

        assert res.status_code == 400, res.text
        assert "ข้อมูลไม่พอ" in res.json()["detail"]

    def test_real_bug_is_still_500(self, monkeypatch):
        """บั๊กจริงต้องดังเป็น 500 ห้ามถูกเล่าเป็น 'ดึงราคาไม่สำเร็จ'"""

        def _bug(*_args, **_kwargs):
            raise TypeError("unsupported operand type(s)")

        monkeypatch.setattr(backtest_router._engine, "run", _bug)
        res = self._client(raise_server_exceptions=False).post("/api/backtest", json=_PAYLOAD)

        assert res.status_code == 500, res.text
        assert "ดึงราคา" not in res.text

    def test_no_ai_call_unless_asked(self, monkeypatch):
        calls: list[tuple] = []

        def _should_not_run(*args, **kwargs):  # pragma: no cover - ต้องไม่ถูกเรียก
            calls.append((args, kwargs))
            return "เผาเงินโดยไม่ได้ขอ"

        monkeypatch.setattr(backtest_router, "generate_summary", _should_not_run)
        monkeypatch.setattr(backtest_router._engine, "run", lambda *a, **k: dict(_FAKE_RESULT))
        res = self._client().post("/api/backtest", json=_PAYLOAD)

        assert res.status_code == 200, res.text
        assert calls == [], "เรียก LLM ทั้งที่ไม่ได้ส่ง include_ai=true"
        assert res.json()["ai_summary"] == AI_DISABLED_MESSAGE

    def test_llm_failure_keeps_the_computed_numbers_and_says_it_failed(self, monkeypatch):
        """AI ล้ม = คำอธิบายหาย แต่ตัวเลขที่ Python คำนวณแล้วยังถูกต้อง → ต้องคืนพร้อมคำเตือน"""

        def _llm_down(*_args, **_kwargs):
            raise RuntimeError("เรียก LLM ไม่สำเร็จ: ไม่ได้ตั้งค่า ANTHROPIC_API_KEY")

        monkeypatch.setattr(backtest_router, "generate_summary", _llm_down)
        monkeypatch.setattr(backtest_router._engine, "run", lambda *a, **k: dict(_FAKE_RESULT))
        res = self._client().post("/api/backtest?include_ai=true", json=_PAYLOAD)

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total_return"] == _FAKE_RESULT["total_return"]
        assert "ไม่สามารถสร้างสรุป AI ได้" in body["ai_summary"]
        assert "ANTHROPIC_API_KEY" in body["ai_summary"], "ต้องบอกสาเหตุจริง ไม่ใช่ข้อความกำกวม"

    def test_bug_in_summary_is_not_disguised_as_an_ai_failure(self, monkeypatch):
        """KeyError = สัญญาข้อมูลระหว่าง engine กับ summary ผิด (บั๊กของเรา) ต้องดังเป็น 500"""

        def _contract_bug(*_args, **_kwargs):
            raise KeyError("total_return")

        monkeypatch.setattr(backtest_router, "generate_summary", _contract_bug)
        monkeypatch.setattr(backtest_router._engine, "run", lambda *a, **k: dict(_FAKE_RESULT))
        res = self._client(raise_server_exceptions=False).post("/api/backtest?include_ai=true", json=_PAYLOAD)

        assert res.status_code == 500, res.text
        assert "ไม่สามารถสร้างสรุป AI ได้" not in res.text
