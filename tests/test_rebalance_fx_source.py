# -*- coding: utf-8 -*-
"""K5 (AUDIT_2026-08-06 B9 / C1.5 / L-NW-2) — แผน rebalance ต้องพก "ที่มาของอัตราแลกเปลี่ยน"
ออกไปถึงผู้เรียกด้วย ห้ามให้แผนที่คิดจาก **ค่าสำรอง** ดูเหมือนคิดจาก **อัตราสด**

B9 แก้ ``utils/fx.py`` ให้รายงาน ``is_live`` แล้ว (``get_usdthb()`` คืน
``FxRate(rate, is_live)`` · ``source_of(rate)`` ถามที่มาจากแคชโดยไม่ยิงเน็ต) แต่
``rebalance_service._get_usdthb_rate()`` เรียกแล้ว **ทิ้งธงทิ้ง**

อาการที่วัดได้ก่อนแก้ (ดึงสดล้ม → ค่าสำรอง 33.5 จาก config)::

    utils/fx  -> FxRate(rate=33.5, is_live=False)
    keys      -> ['actions', 'ai_comment', 'detail', 'max_drift_pct',
                  'missing_prices', 'needs_rebalance', 'total_fee_thb', 'untracked_holdings']
    has fx_is_live?  False        ← ธงหายระหว่างทาง
    has fx_rate_thb? False        ← ไม่รู้ด้วยซ้ำว่าคูณด้วยเลขอะไร
    VOO -> {'usd_amount': 3656.72, 'thb_amount': 122500.0, 'fee_thb': 183.75}

ทั้ง ``thb_amount`` ทุกช่อง ``fee_thb`` ทุกช่อง และ **จำนวนหน่วยที่สั่งซื้อ**
(งบบาท ÷ อัตรา = งบ USD) คิดจากอัตราตัวนั้นทั้งหมด — ค่าสำรองคลาดจากค่าสดจริงราว 1.4%
ณ วันตรวจ ผู้ใช้ต้องเห็นว่ากำลังดูแผนที่คิดจากค่าสำรอง ไม่ใช่รู้กันเองในล็อก

"ค่าสด" (``True``) · "ค่าสำรอง" (``False``) · "ไม่ทราบที่มา" (``None``)
เป็นคนละความหมายกันทั้งสามอย่าง ห้ามยุบรวมกันหรือเดาเป็น ``False``
"""

from __future__ import annotations

import json
import types

import pytest

from analysis.llm import LLMDisabledError
from backend.services import rebalance_service
from utils import fx

LIVE_RATE = 32.1
CONFIG_RATE = 33.5

PRICES = {"VOO": 500.0, "SCHD": 25.0, "QQQM": 200.0, "XLV": 150.0, "GLDM": 64.0}

# พอร์ตที่ยังไม่ถึงเกณฑ์ drift 5% (สูงสุด ~0.45%) — ใช้ทดสอบเส้นทาง "ไม่ต้อง rebalance"
BALANCED = [
    {"symbol": "VOO", "shares": 70.0},
    {"symbol": "SCHD", "shares": 1000.0},
    {"symbol": "QQQM", "shares": 100.0},
    {"symbol": "XLV", "shares": 70.0},
    {"symbol": "GLDM", "shares": 165.0},
]


@pytest.fixture(autouse=True)
def _reset_fx_cache():
    """แคชของ ``utils/fx`` เป็น global ระดับโมดูล — ต้องล้างและคืนค่าทุกเคส."""
    saved = fx._cached
    fx._cached = None
    yield
    fx._cached = saved


@pytest.fixture
def clock(monkeypatch):
    """นาฬิกาปลอมของ ``utils/fx`` — คุม TTL ได้โดยไม่ต้อง sleep จริง."""
    state = {"t": 1_000.0}
    monkeypatch.setattr(fx, "time", types.SimpleNamespace(monotonic=lambda: state["t"]))
    return state


@pytest.fixture
def live(monkeypatch):
    """ควบคุมผลของการดึงสด — เทสต์ห้ามแตะเน็ต."""
    state = {"value": None, "calls": 0}

    def _fetch() -> float | None:
        state["calls"] += 1
        return state["value"]

    monkeypatch.setattr(fx, "_fetch_live", _fetch)
    monkeypatch.setattr(fx, "load_config", lambda: {"display": {"default_fx_rate": CONFIG_RATE}})
    return state


@pytest.fixture
def stub_env(monkeypatch, tmp_path):
    """ราคา/LLM/config จำลอง — ห้ามแตะเน็ต ห้ามจ่ายค่า LLM ห้ามแตะ config จริง."""
    from utils import config as cfg

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "etf": {"tickers": ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]},
                "portfolio": {"risk_profile": "moderate", "target_weights": {}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CONFIG_PATH", path)
    monkeypatch.setattr(cfg, "_cache", None)

    state = {"prices": dict(PRICES)}

    def _fake_prices(tickers):
        return {t: state["prices"][t] for t in tickers if t in state["prices"]}

    def _fake_chat(system, user, *, user_initiated=False, **kwargs):
        if not user_initiated:
            raise LLMDisabledError("AI ถูกปิดไว้เพื่อคุมค่าใช้จ่าย (จำลอง)")
        return "คำอธิบายจำลอง"

    monkeypatch.setattr(rebalance_service, "get_current_prices", _fake_prices)
    monkeypatch.setattr(rebalance_service, "chat_text", _fake_chat)
    return state


def _action(result, symbol):
    for a in result["actions"]:
        if a["symbol"] == symbol:
            return a
    return None


class TestPlanCarriesTheFxSource:
    """ธงที่ ``utils/fx`` อุตส่าห์รายงาน ต้องไม่ตกหล่นที่ ``rebalance_service``."""

    def test_live_rate_is_reported_and_raises_no_warning(self, clock, live, stub_env):
        live["value"] = LIVE_RATE

        result = rebalance_service.compute_rebalance([], "moderate", 350000.0)

        assert result["fx_is_live"] is True
        assert result["fx_rate_thb"] == pytest.approx(LIVE_RATE)
        assert "สำรอง" not in result["detail"], "อัตราสดไม่ควรมีคำเตือน"

    def test_fallback_rate_travels_out_with_the_flag(self, clock, live, stub_env):
        """หัวใจของ K5: ดึงสดไม่ได้ → แผนทั้งใบคิดจากค่าสำรอง ผู้เรียกต้องรู้."""
        live["value"] = None

        result = rebalance_service.compute_rebalance([], "moderate", 350000.0)

        assert result["fx_is_live"] is False, (
            "แผนคิดจากอัตราสำรอง แต่ผลลัพธ์ไม่มีอะไรบอก = หน้าจอเข้าใจว่าเป็นอัตราสด"
        )
        assert result["fx_rate_thb"] == pytest.approx(CONFIG_RATE)
        assert "สำรอง" in result["detail"], "ผู้เรียกที่แสดงแค่ detail ก็ต้องเห็นคำเตือน"
        assert f"{CONFIG_RATE:.2f}" in result["detail"]

    def test_reported_rate_is_the_rate_actually_used(self, clock, live, stub_env):
        """เลขที่รายงานต้องเป็นเลขเดียวกับที่คูณ/หารเข้าไปในแผน ไม่ใช่ดึงมาใหม่คนละครั้ง."""
        live["value"] = None

        result = rebalance_service.compute_rebalance([], "moderate", 350000.0)

        rate = float(result["fx_rate_thb"])
        voo = _action(result, "VOO")
        # rel (ไม่ใช่ abs) เพราะ ``usd_amount`` ถูกปัดเป็นสตางค์ก่อน ส่วน ``thb_amount``
        # คิดจากค่าเต็ม — ต่างกันได้ระดับเศษสตางค์ แต่ห้ามต่างกันระดับ "คนละอัตรา"
        assert voo["thb_amount"] == pytest.approx(voo["usd_amount"] * rate, rel=1e-5)
        # งบบาททั้งก้อนถูกแปลงเป็น USD ด้วยอัตราเดียวกัน
        total_usd = sum(a["usd_amount"] for a in result["actions"])
        assert total_usd == pytest.approx(350000.0 / rate, abs=0.05)

    def test_untracked_warning_and_fx_warning_coexist(self, clock, live, stub_env):
        """คำเตือนคนละเรื่องต้องอยู่ร่วมกันได้ — ห้ามอันหนึ่งเบียดอีกอันตกขอบ."""
        live["value"] = None
        stub_env["prices"]["VT"] = 100.0  # ถืออยู่นอกสัดส่วนเป้าหมาย แต่ราคาดึงได้

        result = rebalance_service.compute_rebalance(
            [{"symbol": "VT", "shares": 100.0}], "moderate", 0.0
        )

        assert result["untracked_holdings"] == ["VT"]
        assert "VT" in result["detail"]
        assert "สำรอง" in result["detail"]
        assert result["fx_is_live"] is False

    def test_no_rebalance_needed_still_carries_the_source(self, clock, live, stub_env):
        """drift ต่ำกว่าเกณฑ์ = ไม่มี action แต่คำตอบยังคิดจากอัตราตัวนั้น."""
        live["value"] = None

        result = rebalance_service.compute_rebalance(BALANCED, "moderate", 0.0)

        assert result["needs_rebalance"] is False
        assert result["fx_is_live"] is False
        assert result["fx_rate_thb"] == pytest.approx(CONFIG_RATE)

    def test_missing_prices_keeps_the_keys_as_unknown(self, clock, live, stub_env):
        """ไม่มีแผน = ไม่มีตัวเลขบาทให้เตือน แต่คีย์ต้องไม่หาย และต้องเป็น ``None`` ไม่ใช่ ``False``."""
        live["value"] = None
        stub_env["prices"].pop("VOO")

        result = rebalance_service.compute_rebalance(BALANCED, "moderate", 0.0)

        assert result["needs_rebalance"] is None
        assert result["missing_prices"] == ["VOO"]
        assert result["fx_rate_thb"] is None
        assert result["fx_is_live"] is None, "ไม่ได้ใช้อัตราเลย = ไม่ทราบ ห้ามรายงานว่าเป็นค่าสำรอง"

    def test_unknown_source_is_none_not_false(self, monkeypatch, stub_env):
        """ผู้เรียกที่จัดหาอัตรามาเอง = **ไม่ทราบที่มา** ห้ามเดาว่าเป็นค่าสำรอง."""
        monkeypatch.setattr(rebalance_service, "_get_usdthb_rate", lambda: 34.0)

        result = rebalance_service.compute_rebalance([], "moderate", 350000.0)

        assert result["fx_rate_thb"] == pytest.approx(34.0)
        assert result["fx_is_live"] is None
        assert "สำรอง" not in result["detail"]

    def test_module_has_no_flag_dropping_shortcut_left(self):
        """ห้ามเหลือ helper ที่คืนอัตราเปล่า ๆ ให้เส้นทางแผนเรียก — เป็นทางที่ทำให้บั๊กนี้กลับมา."""
        assert not hasattr(rebalance_service, "_usable_fx_rate"), (
            "ยังมีทางเข้า FX ที่ทิ้งธง is_live"
        )


class TestRebalanceRouteCarriesTheFxSource:
    """ธงต้องเดินทางถึงผู้เรียกจริง (HTTP) ไม่ใช่แค่ dict ภายใน."""

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.routers import rebalance as router_mod

        app = FastAPI()
        app.include_router(router_mod.router)
        return TestClient(app, raise_server_exceptions=False)

    def _post(self, client, holdings, budget=0.0):
        return client.post(
            "/api/portfolio/rebalance",
            json={
                "holdings": holdings,
                "risk_profile": "moderate",
                "available_budget_thb": budget,
            },
        )

    def test_api_response_exposes_the_fallback_flag(self, clock, live, stub_env):
        live["value"] = None

        resp = self._post(self._client(), [], budget=350000.0)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["fx_is_live"] is False
        assert data["fx_rate_thb"] == pytest.approx(CONFIG_RATE)

    def test_api_response_exposes_a_live_flag(self, clock, live, stub_env):
        live["value"] = LIVE_RATE

        resp = self._post(self._client(), [], budget=350000.0)

        data = resp.json()["data"]
        assert data["fx_is_live"] is True
        assert data["fx_rate_thb"] == pytest.approx(LIVE_RATE)
