# -*- coding: utf-8 -*-
"""AUDIT_2026-08-06 B4 — แผน rebalance ต้องอ่านสัดส่วนเป้าหมายจากแหล่งเดียว
และต้องไม่ตัด ticker ที่ถืออยู่นอกเป้าหมายทิ้งเงียบ ๆ

B4.1 ``rebalance_service.TARGET_WEIGHTS = RISK_PROFILES`` อ่าน preset ดิบ ไม่ผ่าน
     ``portfolio.targets.get_target_weights()`` → ``portfolio.target_weights`` ที่ผู้ใช้ตั้งเอง
     และ ticker ที่เพิ่มจากหน้า Settings ไม่มีผลกับแผน rebalance เลย
     (วัดจริง: VOO ควรได้ 6,000 USD ตามค่าที่ตั้ง แต่แผนให้ 3,500 USD = ต่าง 87,500 บาท)

B4.2 ``_build_actions`` วนเฉพาะ ``target.items()`` แต่ ``total_usd`` นับมูลค่าที่ถือ **ทั้งหมด**
     → ของที่ถืออยู่นอกเป้าหมายถูกนับเข้าตัวตั้งแต่ไม่มีวันได้ action
     (วัดจริง: ถือ VT 10,000 USD งบ 0 → แผนสั่งซื้อรวม 10,000 USD ไม่มีคำสั่งขาย ไม่มีคำเตือน)
"""

from __future__ import annotations

import json

import pytest

from analysis.llm import LLMDisabledError
from backend.services import rebalance_service
from portfolio.targets import RISK_PROFILES, get_target_weights

PRICES = {
    "VOO": 500.0,
    "SCHD": 25.0,
    "QQQM": 200.0,
    "XLV": 150.0,
    "GLDM": 64.0,
    "VT": 100.0,
}
FX_RATE = 35.0


@pytest.fixture
def stub_env(monkeypatch):
    """ราคา/FX/LLM จำลอง — เทสต์ห้ามแตะเน็ตและห้ามจ่ายค่า LLM."""

    state = {"prices": dict(PRICES), "llm_calls": 0, "prompts": []}

    def _fake_prices(tickers):
        return {t: state["prices"][t] for t in tickers if t in state["prices"]}

    def _fake_chat(system, user, *, user_initiated=False, **kwargs):
        if not user_initiated:
            raise LLMDisabledError("AI ถูกปิดไว้เพื่อคุมค่าใช้จ่าย (จำลอง)")
        state["llm_calls"] += 1
        state["prompts"].append(user)
        return "คำอธิบายจำลอง"

    monkeypatch.setattr(rebalance_service, "get_current_prices", _fake_prices)
    monkeypatch.setattr(rebalance_service, "_get_usdthb_rate", lambda: FX_RATE)
    monkeypatch.setattr(rebalance_service, "chat_text", _fake_chat)
    return state


@pytest.fixture
def custom_config(tmp_path, monkeypatch):
    """ชี้ ``config.json`` ไปที่ไฟล์ชั่วคราว — ห้ามแตะ config จริงของผู้ใช้."""

    from utils import config as cfg

    def _write(payload: dict) -> None:
        path = tmp_path / "config.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(cfg, "CONFIG_PATH", path)
        monkeypatch.setattr(cfg, "_cache", None)

    return _write


def _action(result, symbol):
    for a in result["actions"]:
        if a["symbol"] == symbol:
            return a
    return None


class TestTargetWeightsComeFromOneSource:
    """B4.1 — ``portfolio/targets.get_target_weights()`` คือแหล่งเดียวของสัดส่วนเป้าหมาย."""

    def test_plan_follows_the_weights_the_user_configured(self, stub_env, custom_config):
        """ตั้ง VOO 60% ใน config → แผนต้องซื้อ VOO 60% ของงบ ไม่ใช่ 35% ตาม preset ดิบ."""
        custom_config({
            "etf": {"tickers": ["VOO", "SCHD", "QQQM", "XLV", "GLDM", "VT"]},
            "portfolio": {
                "risk_profile": "moderate",
                "target_weights": {
                    "VOO": 0.60, "SCHD": 0.10, "QQQM": 0.10,
                    "XLV": 0.10, "GLDM": 0.05, "VT": 0.05,
                },
            },
        })

        # งบ 350,000 บาท = 10,000 USD ที่ FX 35
        result = rebalance_service.compute_rebalance([], "moderate", 350000.0)

        assert _action(result, "VOO")["usd_amount"] == pytest.approx(6000.0, abs=0.01)
        assert _action(result, "GLDM")["usd_amount"] == pytest.approx(500.0, abs=0.01)

    def test_ticker_added_from_settings_appears_in_the_plan(self, stub_env, custom_config):
        """หน้า Settings เพิ่ม ticker ได้ (``add_ticker()``) — แผน rebalance ต้องเห็นด้วย."""
        custom_config({
            "etf": {"tickers": ["VOO", "SCHD", "QQQM", "XLV", "GLDM", "VT"]},
            "portfolio": {"risk_profile": "moderate", "target_weights": {}},
        })

        result = rebalance_service.compute_rebalance([], "moderate", 350000.0)

        assert _action(result, "VT") is not None, "ticker ที่ระบบติดตามหายจากแผน"
        assert _action(result, "VT")["usd_amount"] == pytest.approx(
            get_target_weights()["VT"] * 10000.0, abs=0.01
        )

    def test_resolved_target_is_exactly_the_shared_definition(self, stub_env):
        assert rebalance_service.resolve_target_weights("moderate") == get_target_weights()

    def test_module_keeps_no_alias_to_the_raw_presets(self):
        """ห้ามมีชื่อในโมดูลที่ชี้ไปที่ ``RISK_PROFILES`` ตรง ๆ — เป็นทางลัดที่ทำให้บั๊กนี้กลับมา."""
        aliases = [name for name, value in vars(rebalance_service).items() if value is RISK_PROFILES]
        assert aliases == [], f"ยังมี alias ของ preset ดิบ: {aliases}"

    def test_profile_that_disagrees_with_config_fails_loudly(self, stub_env):
        """config เป็น moderate แต่ payload ขอ aggressive = สองนิยาม ต้องดัง ไม่ใช่เงียบ."""
        with pytest.raises(rebalance_service.RiskProfileMismatch) as exc:
            rebalance_service.compute_rebalance([], "aggressive", 350000.0)

        message = str(exc.value)
        assert "aggressive" in message and "moderate" in message
        assert "config.json" in message


class TestHoldingOutsideTheTarget:
    """B4.2 — ของที่ถืออยู่นอกสัดส่วนเป้าหมายต้องไม่หายจากแผน."""

    HOLDING = [{"symbol": "VT", "shares": 100.0}]  # 10,000 USD

    def test_untracked_holding_is_not_dropped_from_the_plan(self, stub_env):
        result = rebalance_service.compute_rebalance(self.HOLDING, "moderate", 0.0)

        vt = _action(result, "VT")
        assert vt is not None, "ticker ที่ถืออยู่หายจากแผนทั้งที่มูลค่าถูกนับเข้าตัวตั้งแล้ว"
        assert vt["action"] == "sell"
        assert vt["usd_amount"] == pytest.approx(10000.0, abs=0.01)

    def test_untracked_holding_is_reported_to_the_user(self, stub_env):
        result = rebalance_service.compute_rebalance(self.HOLDING, "moderate", 0.0)

        assert result["untracked_holdings"] == ["VT"]
        assert "VT" in result["detail"]

    def test_plan_never_spends_more_than_the_budget(self, stub_env):
        """เอกลักษณ์ทางบัญชี: ซื้อรวม − ขายรวม = งบ (เดิมซื้อ 10,000 USD ด้วยงบ 0)."""
        result = rebalance_service.compute_rebalance(self.HOLDING, "moderate", 0.0)

        buys = sum(a["usd_amount"] for a in result["actions"] if a["action"] == "buy")
        sells = sum(a["usd_amount"] for a in result["actions"] if a["action"] == "sell")
        assert buys - sells == pytest.approx(0.0, abs=0.05)

    def test_budget_identity_holds_with_a_budget_too(self, stub_env):
        result = rebalance_service.compute_rebalance(self.HOLDING, "moderate", 35000.0)  # 1,000 USD

        buys = sum(a["usd_amount"] for a in result["actions"] if a["action"] == "buy")
        sells = sum(a["usd_amount"] for a in result["actions"] if a["action"] == "sell")
        assert buys - sells == pytest.approx(1000.0, abs=0.05)

    def test_drift_counts_the_holding_that_has_no_target(self, stub_env):
        """ถือ VT 100% ของพอร์ตโดยเป้าหมายเป็น 0% = เบี่ยงเบน 100% ไม่ใช่ 35%."""
        result = rebalance_service.compute_rebalance(self.HOLDING, "moderate", 0.0)

        assert result["max_drift_pct"] == pytest.approx(100.0, abs=0.01)

    def test_tracked_only_portfolio_reports_nothing_untracked(self, stub_env):
        holdings = [
            {"symbol": "VOO", "shares": 70.0},
            {"symbol": "SCHD", "shares": 1000.0},
            {"symbol": "QQQM", "shares": 100.0},
            {"symbol": "XLV", "shares": 70.0},
            {"symbol": "GLDM", "shares": 250.0},
        ]
        result = rebalance_service.compute_rebalance(holdings, "moderate", 0.0)

        assert result["untracked_holdings"] == []
        assert result["detail"] == ""

    def test_small_untracked_holding_is_reported_even_when_no_rebalance_is_needed(self, stub_env):
        """drift ยังไม่ถึงเกณฑ์ = ไม่มี action แต่ "มีของนอกเป้า" ยังเป็นข้อมูลที่ต้องบอก."""
        holdings = [
            {"symbol": "VOO", "shares": 70.0},
            {"symbol": "SCHD", "shares": 1000.0},
            {"symbol": "QQQM", "shares": 100.0},
            {"symbol": "XLV", "shares": 70.0},
            {"symbol": "GLDM", "shares": 165.0},
            {"symbol": "VT", "shares": 1.0},  # 100 USD จากพอร์ต ~100,660 USD
        ]
        result = rebalance_service.compute_rebalance(holdings, "moderate", 0.0)

        assert result["needs_rebalance"] is False
        assert result["actions"] == []
        assert result["untracked_holdings"] == ["VT"]
        assert "VT" in result["detail"]

    def test_ai_prompt_mentions_the_untracked_holding(self, stub_env):
        """AI อธิบายแผนที่คำนวณเสร็จแล้ว — แผนสั่งขาย VT ก็ต้องมี VT อยู่ในบริบท."""
        rebalance_service.compute_rebalance(self.HOLDING, "moderate", 0.0, user_initiated=True)

        assert "VT" in stub_env["prompts"][0]


class TestRebalanceRouteContract:
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.routers import rebalance as router_mod

        app = FastAPI()
        app.include_router(router_mod.router)
        return TestClient(app, raise_server_exceptions=False)

    def _post(self, client, holdings, profile="moderate", budget=0.0):
        return client.post(
            "/api/portfolio/rebalance",
            json={
                "holdings": holdings,
                "risk_profile": profile,
                "available_budget_thb": budget,
            },
        )

    def test_response_exposes_untracked_holdings(self, stub_env):
        resp = self._post(self._client(), [{"symbol": "VT", "shares": 100.0}])

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["untracked_holdings"] == ["VT"]
        assert "VT" in data["detail"]

    def test_profile_mismatch_is_400_not_500(self, stub_env):
        resp = self._post(self._client(), [], profile="aggressive", budget=350000.0)

        assert resp.status_code == 400
        assert "moderate" in resp.json()["detail"]
