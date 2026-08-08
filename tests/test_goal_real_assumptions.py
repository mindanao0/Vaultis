# -*- coding: utf-8 -*-
"""ทดสอบ Monte Carlo ผูกพอร์ตจริง (Roadmap Phase 4 ข้อ 15)."""

import numpy as np
import pandas as pd
import pytest

from analysis.risk import portfolio_mu_sigma
from backend.models import InvestmentGoal
from backend.services import goal_service


def _price_df(n: int = 500) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-02", periods=n)
    rng = np.random.default_rng(7)
    a = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.010, n)))
    b = 50 * np.exp(np.cumsum(rng.normal(0.0002, 0.005, n)))
    return pd.DataFrame({"A": a, "B": b}, index=idx)


class TestPortfolioMuSigma:
    def test_mix_mu_is_weighted_average(self):
        df = _price_df()
        mu_a, _ = portfolio_mu_sigma(df[["A"]], {"A": 1.0})
        mu_b, _ = portfolio_mu_sigma(df[["B"]], {"B": 1.0})
        mu_mix, sigma_mix = portfolio_mu_sigma(df, {"A": 60.0, "B": 40.0})
        assert mu_mix == pytest.approx(0.6 * mu_a + 0.4 * mu_b, rel=1e-6)
        assert sigma_mix > 0

    def test_ticker_without_price_is_ignored(self):
        df = _price_df()
        mu_with_ghost, _ = portfolio_mu_sigma(df, {"A": 1.0, "ZZZ": 9.0})
        mu_a_only, _ = portfolio_mu_sigma(df[["A"]], {"A": 1.0})
        assert mu_with_ghost == pytest.approx(mu_a_only)

    def test_all_missing_fails_loud(self):
        with pytest.raises(ValueError):
            portfolio_mu_sigma(_price_df(), {"ZZZ": 1.0})


class TestGoalUsesRealPortfolio:
    def _goal(self) -> InvestmentGoal:
        return InvestmentGoal(
            name="เกษียณ",
            target_amount_thb=1_000_000.0,
            current_amount_thb=100_000.0,
            monthly_contribution_thb=5_000.0,
            target_date="2030-01-01",
            risk_profile="moderate",
        )

    # หมายเหตุ: เดิมสองเทสต์แรก monkeypatch ``real_portfolio_assumptions`` (คืน dict|None)
    # ตอนนี้ ``_build_progress`` อ่านจาก ``real_portfolio_assumptions_with_status`` เพราะต้อง
    # แยก "ไม่มีพอร์ต" ออกจาก "ดึงราคาไม่สำเร็จ" (G4) — จึง patch ตัวที่ถูกเรียกจริงแทน
    def test_real_assumptions_flow_into_progress(self, monkeypatch):
        monkeypatch.setattr(
            goal_service,
            "real_portfolio_assumptions_with_status",
            lambda: {
                "status": "ok",
                "mu": 0.10,
                "sigma": 0.12,
                "source": "พอร์ตจริงจาก ledger (ทดสอบ)",
                "error": None,
                "data_ok": True,
            },
        )
        progress = goal_service._build_progress(self._goal())
        assert progress["assumed_annual_return_pct"] == pytest.approx(10.0)
        assert "พอร์ตจริง" in progress["assumptions_source"]
        assert "12.0%" in progress["assumptions_note"]
        assert 0.0 <= progress["probability_of_success"] <= 1.0
        assert progress["assumptions_status"] == "ok"
        assert progress["assumptions_error"] is None

    def test_fallback_to_preset_when_no_portfolio(self, monkeypatch):
        monkeypatch.setattr(
            goal_service,
            "real_portfolio_assumptions_with_status",
            lambda: {
                "status": "empty",
                "mu": None,
                "sigma": None,
                "source": "ยังไม่มีพอร์ตใน ledger",
                "error": None,
                "data_ok": False,
            },
        )
        progress = goal_service._build_progress(self._goal())
        assert progress["assumed_annual_return_pct"] == pytest.approx(9.0)
        assert "preset" in progress["assumptions_source"]

    def test_assumptions_none_when_ledger_empty(self, monkeypatch):
        import portfolio.tracker as tracker

        goal_service._real_assumptions_cache = None
        monkeypatch.setattr(tracker, "get_portfolio_summary", lambda: pd.DataFrame())
        assert goal_service.real_portfolio_assumptions() is None
        goal_service._real_assumptions_cache = None


def _holdings_df() -> pd.DataFrame:
    """สมุดที่มีของจริง ราคาพร้อมครบ — ไม่ใช่ 'ยังไม่มีพอร์ต' แน่นอน"""
    return pd.DataFrame(
        [
            {"Ticker": "VOO", "Current Value (THB)": 100_000.0, "Price OK": True},
            {"Ticker": "SCHD", "Current Value (THB)": 50_000.0, "Price OK": True},
        ]
    )


def _good_prices() -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-02", periods=1200)
    rng = np.random.default_rng(11)
    return pd.DataFrame(
        {
            "VOO": 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.010, 1200))),
            "SCHD": 50 * np.exp(np.cumsum(rng.normal(0.0003, 0.008, 1200))),
        },
        index=idx,
    )


class TestAssumptionsFailureIsNotMistakenForMissingPortfolio:
    """G4: "ดึงราคาไม่สำเร็จ" ≠ "ยังไม่มีพอร์ต" ≠ "คอนฟิก FX ผิด" — และห้ามแคชความล้มเหลว"""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        goal_service._real_assumptions_cache = None
        yield
        goal_service._real_assumptions_cache = None

    def _goal(self) -> InvestmentGoal:
        return InvestmentGoal(
            name="เกษียณ",
            target_amount_thb=1_000_000.0,
            current_amount_thb=100_000.0,
            monthly_contribution_thb=5_000.0,
            target_date="2030-01-01",
            risk_profile="aggressive",
        )

    def _stub_ledger(self, monkeypatch):
        import portfolio.tracker as tracker

        monkeypatch.setattr(tracker, "get_portfolio_summary", _holdings_df)

    def test_price_failure_reported_as_error_not_missing_portfolio(self, monkeypatch):
        import data.fetcher as fetcher

        self._stub_ledger(monkeypatch)

        def _boom(*a, **k):
            raise fetcher.PriceDataUnavailableError("yahoo ล่ม 3 ครั้งติด")

        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", _boom)

        status = goal_service.real_portfolio_assumptions_with_status()
        assert status["status"] == "error"
        assert "yahoo ล่ม 3 ครั้งติด" in status["error"]
        assert status["data_ok"] is False

    def test_progress_says_preset_is_a_stand_in_when_prices_fail(self, monkeypatch):
        import data.fetcher as fetcher

        self._stub_ledger(monkeypatch)

        def _boom(*a, **k):
            raise fetcher.PriceDataUnavailableError("yahoo ล่ม 3 ครั้งติด")

        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", _boom)

        progress = goal_service._build_progress(self._goal())
        # ยังตอบได้ (fail-closed ต้องไม่ทำให้หน้า Goals ใช้ไม่ได้) แต่ต้องบอกความจริง
        assert progress["assumptions_status"] == "error"
        assert "yahoo ล่ม 3 ครั้งติด" in progress["assumptions_error"]
        assert "ยังไม่มีพอร์ต" not in progress["assumptions_source"]
        assert "ดึง" in progress["assumptions_source"]
        assert "สำเร็จรูป" in progress["assumptions_note"]

    def test_failure_is_not_cached(self, monkeypatch):
        import data.fetcher as fetcher

        self._stub_ledger(monkeypatch)

        def _boom(*a, **k):
            raise fetcher.PriceDataUnavailableError("yahoo ล่ม 3 ครั้งติด")

        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", _boom)
        assert goal_service.real_portfolio_assumptions_with_status()["status"] == "error"

        # ราคากลับมาปกติในนาทีถัดมา — ต้องได้พอร์ตจริงทันที ไม่ใช่ค้าง preset 10 นาที
        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", lambda *a, **k: _good_prices())
        again = goal_service.real_portfolio_assumptions_with_status()
        assert again["status"] == "ok"
        assert again["mu"] == again["mu"]  # ไม่ใช่ NaN

    def test_empty_ledger_is_empty_not_error(self, monkeypatch):
        import portfolio.tracker as tracker

        monkeypatch.setattr(tracker, "get_portfolio_summary", lambda: pd.DataFrame())
        status = goal_service.real_portfolio_assumptions_with_status()
        assert status["status"] == "empty"
        assert status["error"] is None

    def test_empty_ledger_is_not_cached_either(self, monkeypatch):
        import data.fetcher as fetcher
        import portfolio.tracker as tracker

        monkeypatch.setattr(tracker, "get_portfolio_summary", lambda: pd.DataFrame())
        assert goal_service.real_portfolio_assumptions_with_status()["status"] == "empty"

        # ผู้ใช้เพิ่งบันทึกรายการซื้อ — ครั้งถัดไปต้องเห็นพอร์ต ไม่ใช่ค้าง "ไม่มีพอร์ต"
        monkeypatch.setattr(tracker, "get_portfolio_summary", _holdings_df)
        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", lambda *a, **k: _good_prices())
        assert goal_service.real_portfolio_assumptions_with_status()["status"] == "ok"

    def test_fx_failure_reported_with_its_own_reason(self, monkeypatch):
        import portfolio.tracker as tracker
        import utils.fx as fx

        def _boom():
            raise fx.FxRateUnavailable("ค่าสำรองใน config.json (display.default_fx_rate = 900.0) อยู่นอกช่วง")

        monkeypatch.setattr(tracker, "get_portfolio_summary", _boom)
        status = goal_service.real_portfolio_assumptions_with_status()
        assert status["status"] == "error"
        assert "default_fx_rate" in status["error"]

        progress = goal_service._build_progress(self._goal())
        assert "ยังไม่มีพอร์ต" not in progress["assumptions_source"]
        assert "default_fx_rate" in progress["assumptions_error"]

    def test_code_bug_is_not_swallowed_as_missing_portfolio(self, monkeypatch):
        import portfolio.tracker as tracker

        # สมุดผิดรูป (ขาดคอลัมน์มูลค่า) = บั๊กจริง ต้องดัง ไม่ใช่กลายเป็น "ยังไม่มีพอร์ต"
        monkeypatch.setattr(
            tracker,
            "get_portfolio_summary",
            lambda: pd.DataFrame([{"Ticker": "VOO", "Price OK": True}]),
        )
        with pytest.raises(KeyError):
            goal_service.real_portfolio_assumptions_with_status()

    def test_success_is_cached(self, monkeypatch):
        import data.fetcher as fetcher

        self._stub_ledger(monkeypatch)
        calls = {"n": 0}

        def _prices(*a, **k):
            calls["n"] += 1
            return _good_prices()

        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", _prices)
        first = goal_service.real_portfolio_assumptions_with_status()
        second = goal_service.real_portfolio_assumptions_with_status()
        assert first["status"] == second["status"] == "ok"
        assert calls["n"] == 1

    def test_compat_wrapper_returns_none_on_failure(self, monkeypatch):
        import data.fetcher as fetcher

        self._stub_ledger(monkeypatch)

        def _boom(*a, **k):
            raise fetcher.PriceDataUnavailableError("yahoo ล่ม")

        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", _boom)
        assert goal_service.real_portfolio_assumptions() is None

    def test_api_progress_reports_the_real_cause(self, monkeypatch, tmp_path):
        """ธงต้องไปถึงผู้เรียก API ด้วย — ไม่ใช่รู้อยู่ในเซอร์วิสคนเดียว (ฐาน SQLite ชั่วคราว)"""
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        import data.fetcher as fetcher
        from backend.database import Base, get_db
        from backend.main import app

        self._stub_ledger(monkeypatch)

        def _boom(*a, **k):
            raise fetcher.PriceDataUnavailableError("yahoo ล่ม 3 ครั้งติด")

        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", _boom)
        monkeypatch.delenv("VAULTIS_API_KEY", raising=False)

        engine = create_engine(
            f"sqlite:///{tmp_path / 'g4_goals.db'}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        goal = self._goal()
        session.add(goal)
        session.commit()
        goal_id = goal.id

        def _override():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override
        try:
            resp = TestClient(app).get(f"/api/goals/{goal_id}/progress")
        finally:
            app.dependency_overrides.pop(get_db, None)
            session.close()
            engine.dispose()

        assert resp.status_code == 200, resp.text[:300]
        data = resp.json()["data"]
        assert data["assumptions_status"] == "error"
        assert "yahoo ล่ม 3 ครั้งติด" in data["assumptions_error"]
        assert "ยังไม่มีพอร์ต" not in data["assumptions_source"]
