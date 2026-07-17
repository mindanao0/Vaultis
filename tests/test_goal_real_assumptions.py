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

    def test_real_assumptions_flow_into_progress(self, monkeypatch):
        monkeypatch.setattr(
            goal_service,
            "real_portfolio_assumptions",
            lambda: {"mu": 0.10, "sigma": 0.12, "source": "พอร์ตจริงจาก ledger (ทดสอบ)"},
        )
        progress = goal_service._build_progress(self._goal())
        assert progress["assumed_annual_return_pct"] == pytest.approx(10.0)
        assert "พอร์ตจริง" in progress["assumptions_source"]
        assert "12.0%" in progress["assumptions_note"]
        assert 0.0 <= progress["probability_of_success"] <= 1.0

    def test_fallback_to_preset_when_no_portfolio(self, monkeypatch):
        monkeypatch.setattr(goal_service, "real_portfolio_assumptions", lambda: None)
        progress = goal_service._build_progress(self._goal())
        assert progress["assumed_annual_return_pct"] == pytest.approx(9.0)
        assert "preset" in progress["assumptions_source"]

    def test_assumptions_none_when_ledger_empty(self, monkeypatch):
        import portfolio.tracker as tracker

        goal_service._real_assumptions_cache = None
        monkeypatch.setattr(tracker, "get_portfolio_summary", lambda: pd.DataFrame())
        assert goal_service.real_portfolio_assumptions() is None
        goal_service._real_assumptions_cache = None
