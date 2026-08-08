# -*- coding: utf-8 -*-
"""ทดสอบ Monte Carlo ผูกพอร์ตจริง (Roadmap Phase 4 ข้อ 15)."""

import numpy as np
import pandas as pd
import pytest

from analysis.risk import portfolio_mu_sigma, portfolio_return_stats
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


# ── สัญญาสองอัตรา (FIX_PLAN เฟส 4① · AUDIT_ROUND2_2026-08-07) ─────────────────
#
# ตัวเลขคู่นี้เลือกให้ต่างกันเท่า σ²/2 พอดี (0.155² / 2 = 0.0120) ซึ่งเป็นระยะห่าง
# ตามทฤษฎีระหว่างค่าเฉลี่ยเลขคณิตกับอัตราทบต้น — สลับสองตัวนี้เมื่อไหร่ ตัวเลขที่
# ผู้ใช้เห็นก็เพี้ยนไปทั้งหน้าโดยไม่มี error ให้จับ เทสต์ชุดนี้จึงต้องจับแทน
_MU_GEOMETRIC = 0.080   # CAGR — ตัวที่ทบต้นได้
_MU_ARITHMETIC = 0.092  # ค่าเฉลี่ยเลขคณิต — สูงกว่าเสมอ ห้ามเอาไปทบต้น
_SIGMA = 0.155


def _two_rate_assumptions() -> dict:
    """สมมติฐาน ``ok`` ที่แยกสองอัตราออกจากกันชัด ๆ (ไม่แตะเครือข่าย)"""
    return {
        "status": "ok",
        "mu": _MU_GEOMETRIC,
        "mu_geometric": _MU_GEOMETRIC,
        "mu_arithmetic": _MU_ARITHMETIC,
        "sigma": _SIGMA,
        "window": {
            "start": "2020-01-03",
            "end": "2024-10-04",
            "days": 1199,
            "days_available": 1199,
            "years": 1199 / 252,
            "tickers": ["VOO", "SCHD"],
        },
        "source": "พอร์ตจริงจาก ledger (ทดสอบ)",
        "error": None,
        "data_ok": True,
    }


class TestTwoRateContract:
    """PMT ต้องกินอัตราทบต้น · Monte Carlo ต้องกิน drift เลขคณิต — ห้ามสลับกัน.

    เดิม ``_build_progress`` มีอัตราเดียวแล้วส่งเข้าทั้งสองสูตร: เอาค่าเฉลี่ยเลขคณิต
    ไปทบต้นทำให้ "ต้องออมเดือนละเท่าไร" **ต่ำกว่าที่ต้องออมจริง** (σ 15% ⇒ ราว 1.1
    จุด/ปี) ส่วนการเอา CAGR ไปเป็น drift ของ MC คือหักส่วนต่าง σ²/2 ซ้ำสองรอบ
    ทั้งคู่ไม่ทำให้อะไรพัง มันแค่ตอบเลขผิดเงียบ ๆ กับเงินจริงของผู้ใช้
    """

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
            risk_profile="moderate",
        )

    def test_required_pmt_uses_geometric_not_arithmetic(self, monkeypatch):
        """(ก) เงินออมที่ต้องการต้องคิดจาก CAGR — เทียบกับ ``calculate_pmt`` ทั้งสองทาง"""
        monkeypatch.setattr(
            goal_service, "real_portfolio_assumptions_with_status", _two_rate_assumptions
        )
        goal = self._goal()
        months = goal_service._months_remaining(goal.target_date)
        assert months > 0, "เป้าหมายทดสอบต้องยังไม่หมดเวลา ไม่งั้นสูตร PMT ลัดวงจร"

        pmt_geometric = goal_service.calculate_pmt(
            goal.target_amount_thb, goal.current_amount_thb, _MU_GEOMETRIC, months
        )
        pmt_arithmetic = goal_service.calculate_pmt(
            goal.target_amount_thb, goal.current_amount_thb, _MU_ARITHMETIC, months
        )
        # ทิศทางของบั๊ก: อัตราที่สูงกว่าทำให้ระบบบอกให้ออม "น้อยกว่า" ที่ต้องออมจริง
        assert pmt_arithmetic < pmt_geometric

        progress = goal_service._build_progress(goal)
        assert progress["required_monthly_pmt"] == pytest.approx(round(pmt_geometric, 2))
        assert progress["required_monthly_pmt"] != pytest.approx(round(pmt_arithmetic, 2))

        # เลขที่โชว์ข้างบรรทัดต้องเป็นตัวทบต้นตัวเดียวกับที่ใช้คำนวณ ไม่ใช่ drift
        assert progress["assumed_annual_return_pct"] == pytest.approx(_MU_GEOMETRIC * 100)
        assert progress["montecarlo_drift_annual_pct"] == pytest.approx(_MU_ARITHMETIC * 100)
        assert "ทบต้น" in progress["assumptions_note"]
        assert f"{_MU_GEOMETRIC*100:.1f}%" in progress["assumptions_note"]

    def test_monte_carlo_receives_arithmetic_drift(self, monkeypatch):
        """(ข) ดัก kwargs ที่ ``calculate_probability`` ได้รับจริง"""
        monkeypatch.setattr(
            goal_service, "real_portfolio_assumptions_with_status", _two_rate_assumptions
        )
        seen: dict = {}

        def _spy(*args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            return 0.4242

        monkeypatch.setattr(goal_service, "calculate_probability", _spy)
        progress = goal_service._build_progress(self._goal())

        # เรียกด้วย keyword ล้วน — ถ้าวันหนึ่งเปลี่ยนเป็น positional เทสต์นี้ต้องดัง
        # ไม่ใช่ผ่านไปเงียบ ๆ เพราะ ``kwargs`` ว่าง
        assert seen["args"] == ()
        assert seen["kwargs"]["annual_return"] == pytest.approx(_MU_ARITHMETIC)
        assert seen["kwargs"]["annual_return"] != pytest.approx(_MU_GEOMETRIC)
        assert seen["kwargs"]["volatility"] == pytest.approx(_SIGMA)
        assert progress["probability_of_success"] == pytest.approx(0.4242)

    def test_preset_path_uses_one_rate_for_both(self, monkeypatch):
        """preset ไม่มีคู่เลขคณิต/เรขาคณิตให้แยก — ต้องใช้ค่าเดียวกันทั้งสองทาง"""
        monkeypatch.setattr(
            goal_service,
            "real_portfolio_assumptions_with_status",
            lambda: {
                "status": "empty",
                "mu": None,
                "mu_geometric": None,
                "mu_arithmetic": None,
                "sigma": None,
                "window": None,
                "source": "ยังไม่มีพอร์ตใน ledger",
                "error": None,
                "data_ok": False,
            },
        )
        seen: dict = {}
        monkeypatch.setattr(
            goal_service,
            "calculate_probability",
            lambda *a, **k: seen.update(k) or 0.5,
        )
        progress = goal_service._build_progress(self._goal())
        assert progress["assumed_annual_return_pct"] == pytest.approx(9.0)
        assert progress["montecarlo_drift_annual_pct"] == pytest.approx(9.0)
        assert seen["annual_return"] == pytest.approx(0.09)
        assert progress["assumptions_window"] is None  # preset ไม่มีหน้าต่างข้อมูลให้อ้าง

    def test_real_window_reaches_the_user(self, monkeypatch):
        """(ค) ช่วงข้อมูล**ที่ใช้จริง** ต้องไปถึงผลลัพธ์ ไม่ใช่ป้ายคงที่ "ย้อนหลัง 10 ปี"

        ``dropna`` ตัดอนุกรมเหลือประวัติร่วมที่สั้นที่สุดของพอร์ตเสมอ — ป้ายที่ยังบอก
        จำนวนปีที่ *ขอ* คือการกุที่มาของตัวเลข (AUDIT_ROUND2_2026-08-07)
        """
        import json

        import data.fetcher as fetcher
        import portfolio.tracker as tracker

        monkeypatch.setattr(tracker, "get_portfolio_summary", _holdings_df)
        monkeypatch.setattr(fetcher, "fetch_adjusted_close_data", lambda *a, **k: _good_prices())

        progress = goal_service._build_progress(self._goal())
        assert progress["assumptions_status"] == "ok"

        window = progress["assumptions_window"]
        assert window is not None, "assumptions_window ถูกคำนวณแล้วแต่ไม่เคยส่งออกไปให้ผู้ใช้"

        # เทียบกับชั้นวิเคราะห์ตรง ๆ ด้วยข้อมูล/น้ำหนักชุดเดียวกัน
        expected = portfolio_return_stats(_good_prices(), {"VOO": 100_000.0, "SCHD": 50_000.0})
        assert window["start"] == expected["window_start"]
        assert window["end"] == expected["window_end"]
        assert window["days"] == int(expected["window_days"])
        assert window["days_available"] == int(expected["window_days_available"])
        assert set(window["tickers"]) == {"VOO", "SCHD"}

        source = progress["assumptions_source"]
        assert "ย้อนหลัง 10 ปี" not in source
        assert window["start"] in source and window["end"] in source
        assert f"{window['years']:.1f} ปี" in source
        # ข้อมูลทดสอบมีราว 4.8 ปี ทั้งที่คำขอคือ 10 ปี — ป้ายที่เป็นค่าคงที่จะตกข้อนี้
        assert window["years"] < goal_service._HISTORY_YEARS_REQUESTED / 2
        # ป้ายที่มาต้องอยู่ในบรรทัดที่ผู้ใช้อ่านจริง ไม่ใช่ซ่อนอยู่ในคีย์ที่ไม่มีใครแสดง
        assert source in progress["assumptions_note"]

        # routers/goals.py ส่ง dict นี้ผ่าน JSONResponse ตรง ๆ ไม่มี response_model กรอง
        # numpy scalar หลุดมาเมื่อไหร่ก็ 500 ทั้งหน้า — ตรึงไว้ที่นี่
        json.dumps(progress, ensure_ascii=False)


class TestRateUnitIsCompoundNotNominal:
    """สูตรที่ทบต้นเองต้องแปลงรายปี→รายเดือนแบบ **ทบต้น** ไม่ใช่ ``rate / 12``.

    ที่มา (AUDIT_ROUND2_2026-08-07): ``calculate_pmt`` และ ``projected_value`` รับ
    ``mu_geometric`` ซึ่งเป็นอัตรา**ทบต้น**จริง (CAGR) แต่แปลงเป็นรายเดือนด้วย
    ``rate / 12`` — นั่นคืออัตรา *นาม*: ทบ 12 งวดแล้วได้ ``(1+r/12)^12 − 1`` ซึ่งสูงกว่า
    ``r`` ที่รับมา (9.00% ⇒ ทบจริง 9.38%) ⇒ ระบบคิดว่าเงินโตเร็วกว่าความจริง ⇒ บอกผู้ใช้
    ให้ออม**น้อยกว่าที่ต้องออมจริง** เป็นบั๊กทิศทางเดียวกับ σ²/2 ที่เฟส 4① เพิ่งปิด
    และ ``assumed_annual_return_pct`` ที่โชว์บนจอก็จะไม่ใช่อัตราที่ใช้คำนวณจริง

    เทสต์ชุดนี้จงใจ **ไม่เรียก ``calculate_pmt`` มาเทียบกับตัวเอง** — เคสเดิมใน
    ``TestTwoRateContract`` ทำอย่างนั้น จึงตรึงได้แค่ "ส่งอัตราตัวไหนเข้าไป" ไม่ได้ตรึง
    ว่าคำนวณถูกหน่วย (พิสูจน์แล้ว: เปลี่ยน ``rate/12`` เป็นสูตรทบต้น ชุดเทสต์ทั้งหมด
    ยังเขียว) ที่นี่จึงคำนวณคำตอบด้วยสูตรปิดรูปขึ้นมาเองแล้วเทียบ
    """

    def test_pmt_ตรงกับสูตรปิดรูปที่ใช้อัตราทบต้นรายเดือน(self) -> None:
        target, current, rate, months = 1_000_000.0, 100_000.0, 0.09, 120

        got = goal_service.calculate_pmt(target, current, rate, months)

        # สูตร annuity มาตรฐาน คำนวณตรง ๆ ไม่ผ่านโค้ดที่กำลังทดสอบ
        m = (1.0 + rate) ** (1.0 / 12.0) - 1.0
        growth = (1.0 + m) ** months
        expected = (target - current * growth) * m / (growth - 1.0)

        assert got == pytest.approx(expected, rel=1e-9)

        # และต้อง **ไม่** เท่ากับเวอร์ชันนาม (rate/12) ที่เป็นบั๊กเดิม
        m_nominal = rate / 12.0
        growth_nominal = (1.0 + m_nominal) ** months
        nominal = (target - current * growth_nominal) * m_nominal / (growth_nominal - 1.0)
        assert got > nominal, (
            "อัตรานามทำให้เงินโตเร็วเกินจริง ⇒ PMT ต่ำกว่าที่ต้องออมจริง "
            f"(นาม {nominal:,.2f} < ทบต้น {got:,.2f} บาท/เดือน)"
        )

    def test_อัตราที่โชว์กับอัตราที่ทบจริงต้องเป็นตัวเดียวกัน(self) -> None:
        """ทบอัตรารายเดือนครบ 12 งวดแล้วต้องได้อัตราต่อปีที่ประกาศไว้เป๊ะ."""
        for annual in (0.0, 0.02, 0.08, 0.09, 0.15):
            m = goal_service.monthly_compound_rate(annual)
            assert (1.0 + m) ** 12 - 1.0 == pytest.approx(annual, abs=1e-12), (
                f"อัตรา {annual:.0%} ต่อปีทบ 12 งวดแล้วต้องได้ {annual:.0%} กลับมา"
            )

    def test_อัตราติดลบเกินร้อยเปอร์เซ็นต์ต้องดังไม่ใช่_NaN(self) -> None:
        with pytest.raises(ValueError):
            goal_service.monthly_compound_rate(-1.0)

    def test_เงินออมเป็นศูนย์เมื่อเงินต้นถึงเป้าแล้ว(self) -> None:
        """กันไม่ให้การแก้หน่วยไปทำให้เคสขอบ (ไม่ต้องออมเพิ่ม) กลายเป็นค่าติดลบ."""
        assert goal_service.calculate_pmt(100_000.0, 500_000.0, 0.09, 120) == 0.0

    def test_อัตราศูนย์ยังหารเวลาตรง_ๆ_ได้(self) -> None:
        assert goal_service.calculate_pmt(120_000.0, 0.0, 0.0, 12) == pytest.approx(10_000.0)
