# -*- coding: utf-8 -*-
"""เทสต์ Phase 3 — auth, สัดส่วนเป้าหมาย, walk-forward, config cache.

ไม่ยิง network ทั้งไฟล์
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.security import require_api_key
from portfolio.targets import RISK_PROFILES, get_target_weights


def _app_with_guard() -> FastAPI:
    app = FastAPI()

    @app.get("/open")
    def open_route():
        return {"ok": True}

    @app.get("/guarded", dependencies=[Depends(require_api_key)])
    def guarded():
        return {"ok": True}

    return app


class TestApiKeyGuard:
    """AUDIT.md H1: backend เปิดสาธารณะโดยไม่มี auth — ใครก็ลบธุรกรรม/เผา credit ได้."""

    def test_open_route_needs_no_key(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_API_KEY", "secret123")
        client = TestClient(_app_with_guard())
        assert client.get("/open").status_code == 200

    def test_guarded_route_rejects_missing_key(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_API_KEY", "secret123")
        client = TestClient(_app_with_guard())
        assert client.get("/guarded").status_code == 401

    def test_guarded_route_rejects_wrong_key(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_API_KEY", "secret123")
        client = TestClient(_app_with_guard())
        resp = client.get("/guarded", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_guarded_route_accepts_correct_key(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_API_KEY", "secret123")
        client = TestClient(_app_with_guard())
        resp = client.get("/guarded", headers={"X-API-Key": "secret123"})
        assert resp.status_code == 200

    def test_local_dev_works_without_key(self, monkeypatch):
        """ไม่ตั้งคีย์ + เรียกจากเครื่องเดียวกัน = ผ่าน (dev สะดวก)."""
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)
        client = TestClient(_app_with_guard())  # TestClient host = "testclient" (local)
        assert client.get("/guarded").status_code == 200


class TestTargetWeights:
    """สัดส่วนเป้าหมายต้องมาจากแหล่งเดียว — เดิมมี 2 ชุดที่ไม่ตรงกัน."""

    def test_weights_sum_to_one(self):
        weights = get_target_weights(["VOO", "SCHD", "QQQM", "XLV", "GLDM"])
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_every_configured_ticker_gets_a_target(self):
        weights = get_target_weights(["VOO", "SCHD", "QQQM", "XLV", "GLDM"])
        assert set(weights) == {"VOO", "SCHD", "QQQM", "XLV", "GLDM"}
        assert all(w > 0 for w in weights.values())

    def test_new_ticker_without_preset_still_gets_weight(self):
        """เพิ่ม ETF ใหม่ที่ไม่มีใน preset → ต้องไม่ถูกละเลย (ได้ส่วนแบ่งจากที่เหลือ)."""
        weights = get_target_weights(["VOO", "SCHD", "VTI"])
        assert weights["VTI"] > 0
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_rebalance_and_goals_share_the_same_presets(self):
        from backend.services.goal_service import ALLOCATION_MAP
        from backend.services.rebalance_service import TARGET_WEIGHTS

        assert TARGET_WEIGHTS is RISK_PROFILES
        assert ALLOCATION_MAP is RISK_PROFILES

    def test_all_presets_sum_to_one(self):
        for profile, weights in RISK_PROFILES.items():
            assert sum(weights.values()) == pytest.approx(1.0), f"{profile} ไม่รวมเป็น 100%"


class TestConfigCache:
    def test_cache_invalidates_on_save(self, tmp_path, monkeypatch):
        import json

        from utils import config as cfg

        path = tmp_path / "config.json"
        path.write_text(json.dumps({"dca": {"monthly_budget_thb": 5000}}), encoding="utf-8")
        monkeypatch.setattr(cfg, "CONFIG_PATH", path)
        monkeypatch.setattr(cfg, "_cache", None)

        assert cfg.load_config()["dca"]["monthly_budget_thb"] == 5000.0
        cfg.save_config({"dca": {"monthly_budget_thb": 9000}})
        assert cfg.load_config()["dca"]["monthly_budget_thb"] == 9000.0, "cache ไม่ถูกล้างหลังบันทึก"

    def test_mutating_returned_config_does_not_poison_cache(self, tmp_path, monkeypatch):
        import json

        from utils import config as cfg

        path = tmp_path / "config.json"
        path.write_text(json.dumps({"dca": {"monthly_budget_thb": 5000}}), encoding="utf-8")
        monkeypatch.setattr(cfg, "CONFIG_PATH", path)
        monkeypatch.setattr(cfg, "_cache", None)

        first = cfg.load_config()
        first["dca"]["monthly_budget_thb"] = 1.0
        assert cfg.load_config()["dca"]["monthly_budget_thb"] == 5000.0
