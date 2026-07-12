# -*- coding: utf-8 -*-
"""เทสต์การแก้ Phase 2 — ledger เดียว, FX, backtest, หนี้, เป้าหมาย, ตัวชี้วัด.

ไม่ยิง network ทั้งไฟล์ (mock ทุกจุดที่ต้องดึงข้อมูล)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import pytest

from analysis.macro import _cpi_yoy_percent
from analysis.risk import DEFAULT_RISK_FREE_RATE, calculate_risk_metrics
from backend.models.debt_models import Debt
from backend.services import debt_service, goal_service
from data.fetcher import normalize_close_series
from portfolio.backtest import _calculate_metrics, run_portfolio_backtest


class TestNormalizeCloseSeries:
    """yfinance คืน MultiIndex แม้ ticker เดียว — เดิมทำให้ FX ไม่เคยดึงได้จริง."""

    def test_multiindex_columns(self):
        idx = pd.date_range("2026-01-01", periods=3)
        df = pd.DataFrame(
            [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]],
            index=idx,
            columns=pd.MultiIndex.from_tuples([("Close", "THB=X"), ("Volume", "THB=X")]),
        )
        series = normalize_close_series(df)
        assert list(series) == [1.0, 2.0, 3.0]

    def test_flat_columns(self):
        idx = pd.date_range("2026-01-01", periods=2)
        df = pd.DataFrame({"Close": [33.1, 33.2], "Volume": [1, 2]}, index=idx)
        assert list(normalize_close_series(df)) == [33.1, 33.2]

    def test_missing_close_returns_empty(self):
        df = pd.DataFrame({"Open": [1.0]}, index=pd.date_range("2026-01-01", periods=1))
        assert normalize_close_series(df).empty

    def test_empty_input(self):
        assert normalize_close_series(pd.DataFrame()).empty


class TestFxRate:
    def test_uses_live_rate_when_sane(self, monkeypatch):
        from utils import fx

        fx._cached = None
        monkeypatch.setattr(fx, "_fetch_live", lambda: 32.8)
        result = fx.get_usdthb(force_refresh=True)
        assert result.rate == 32.8
        assert result.is_live is True

    def test_falls_back_and_flags_when_fetch_fails(self, monkeypatch):
        from utils import fx

        fx._cached = None
        monkeypatch.setattr(fx, "_fetch_live", lambda: None)
        result = fx.get_usdthb(force_refresh=True)
        assert result.is_live is False, "ต้องบอกได้ว่าค่านี้ไม่ใช่ค่าสด"
        assert fx.MIN_RATE <= result.rate <= fx.MAX_RATE


class TestLedgerRoundTrip:
    """ledger เดียว: เขียนผ่าน backend service → อ่านเจอใน tracker ตัวเดียวกัน (H2)."""

    def test_add_read_delete(self, tmp_path, monkeypatch):
        from backend.schemas import TransactionCreate
        from backend.services import portfolio_service
        from portfolio import tracker

        monkeypatch.setattr(tracker, "DATA_DIR", tmp_path)
        monkeypatch.setattr(tracker, "TRANSACTIONS_FILE", tmp_path / "transactions.csv")
        monkeypatch.setattr(tracker, "_get_latest_prices", lambda t: {"VOO": 700.0})
        monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: 33.0)

        created = portfolio_service.add_transaction(
            TransactionCreate(
                date="2026-07-01",
                ticker="voo",
                shares=1.0,
                price_usd=690.0,
                amount_thb=23000.0,
                fx_rate=33.3,
            )
        )
        assert created["tx_id"]
        assert created["ticker"] == "VOO"

        # อ่านผ่าน tracker (ช่องทางที่ dashboard/AI advisor ใช้) ต้องเห็นรายการเดียวกัน
        assert len(tracker.get_transactions()) == 1
        assert len(portfolio_service.get_history()) == 1

        holdings = portfolio_service.get_holdings()
        assert holdings[0]["ticker"] == "VOO"
        assert holdings[0]["price_ok"] is True

        assert portfolio_service.delete_transaction(created["tx_id"]) is True
        assert portfolio_service.get_history() == []
        assert portfolio_service.delete_transaction("ไม่มีอยู่จริง") is False

    def test_recorded_fee_is_not_overwritten(self, tmp_path, monkeypatch):
        """M12: ค่าธรรมเนียมที่บันทึกไว้ต้องไม่ถูกคำนวณทับตอนโหลด."""
        from portfolio import tracker

        csv = tmp_path / "transactions.csv"
        monkeypatch.setattr(tracker, "DATA_DIR", tmp_path)
        monkeypatch.setattr(tracker, "TRANSACTIONS_FILE", csv)
        csv.write_text(
            "tx_id,date,ticker,shares,price_usd,fx_rate_thb,amount_thb,fee_thb,note\n"
            "a1,2026-06-02,VOO,1,600,33,19800,0,first\n"
            "a2,2026-06-10,VOO,1,600,33,19800,999.99,ค่าธรรมเนียมจริงที่บันทึกไว้\n",
            encoding="utf-8",
        )
        df = tracker._load_transactions()
        recorded = df.loc[df["tx_id"] == "a2", "fee_thb"].iloc[0]
        assert recorded == pytest.approx(999.99), "ค่าธรรมเนียมที่บันทึกไว้ถูกเขียนทับ"


class TestPortfolioBacktest:
    def test_excludes_period_before_all_etfs_exist(self):
        """M4: ETF ที่ยังไม่เกิดต้องไม่ถูกนับเป็นผลตอบแทน 0% (ฉุดพอร์ตลง)."""
        idx = pd.date_range("2026-01-01", periods=100, freq="B")
        old = pd.Series(np.linspace(100, 200, 100), index=idx)  # ขึ้น 100%
        new = pd.Series([np.nan] * 50 + list(np.linspace(50, 60, 50)), index=idx)
        prices = pd.DataFrame({"OLD": old, "NEW": new})

        result = run_portfolio_backtest(prices, {"OLD": 0.5, "NEW": 0.5}, initial_capital=10000)
        assert result.index[0] == idx[50], "ต้องเริ่มนับจากวันที่ทุกตัวมีข้อมูล"
        assert result["Portfolio Value"].iloc[0] == pytest.approx(10000, rel=0.01)

    def test_raises_when_common_history_too_short(self):
        """ETF ที่เพิ่งเกิดจนแทบไม่มีช่วงเวลาร่วมกัน → ต้องบอกตรง ๆ ไม่ใช่คืนกราฟมั่ว."""
        idx = pd.date_range("2026-01-01", periods=10, freq="B")
        prices = pd.DataFrame(
            {"OLD": [1.0] * 10, "BRAND_NEW": [np.nan] * 9 + [50.0]}, index=idx
        )
        with pytest.raises(RuntimeError, match="ไม่พอ"):
            run_portfolio_backtest(prices, {"OLD": 0.5, "BRAND_NEW": 0.5})

    def test_sharpe_uses_same_risk_free_rate_as_risk_page(self):
        """M4: Sharpe จาก backtest กับหน้า Risk ต้องใช้ rf เดียวกัน ไม่งั้นเทียบกันไม่ได้."""
        idx = pd.date_range("2024-01-01", periods=500, freq="B")
        rng = np.random.default_rng(7)
        prices = pd.Series(100 * np.cumprod(1 + rng.normal(0.0004, 0.01, 500)), index=idx)
        returns = prices.pct_change().dropna()

        bt = _calculate_metrics(prices, returns)["Sharpe Ratio"]
        risk = calculate_risk_metrics(prices.to_frame("X"))["Sharpe Ratio"].iloc[0]
        assert bt == pytest.approx(risk, rel=0.02)
        assert DEFAULT_RISK_FREE_RATE == 0.02


class TestDebtService:
    BASE = [
        Debt(name="บัตรเครดิต", balance=50000, interest_rate=18.0, min_payment=2000),
        Debt(name="สินเชื่อรถ", balance=200000, interest_rate=5.0, min_payment=5000),
    ]

    def test_budget_below_minimums_is_rejected(self):
        """M10: เดิมจ่ายเกินงบเงียบ ๆ แล้วรายงานว่าหนี้หมด."""
        with pytest.raises(ValueError, match="น้อยกว่ายอดขั้นต่ำ"):
            debt_service._simulate(self.BASE, monthly_budget=3000, method="avalanche")

    def test_feasible_budget_works(self):
        result = debt_service._simulate(self.BASE, monthly_budget=10000, method="avalanche")
        assert result.months_to_payoff > 0
        assert result.total_interest > 0

    def test_avalanche_pays_less_interest_than_snowball(self):
        cmp = debt_service.compare_methods(self.BASE, monthly_budget=10000)
        assert cmp.avalanche.total_interest <= cmp.snowball.total_interest


class TestGoalService:
    def test_required_return_warning_actually_fires(self):
        """M9: เดิมเทียบ expected_return กับตัวมันเอง คำเตือนจึงไม่มีวันทำงาน."""
        needed = goal_service.required_annual_return(
            target=1_000_000, current=0, monthly=5_000, months=60
        )
        assert needed is not None and needed > 0.20, "เคสนี้ต้องการผลตอบแทนสูงมาก"

        result = goal_service.suggest_allocation("moderate", needed)
        assert result["warning"] is not None

    def test_weights_are_not_polluted_by_text(self):
        """M9: เดิมยัด key 'note' (string) ปนใน dict น้ำหนัก (ตัวเลข)."""
        result = goal_service.suggest_allocation("moderate", 0.50)
        assert all(isinstance(v, (int, float)) for v in result["weights"].values())
        assert "note" not in result["weights"]
        assert sum(result["weights"].values()) == pytest.approx(1.0)

    def test_realistic_goal_has_no_warning(self):
        needed = goal_service.required_annual_return(
            target=700_000, current=100_000, monthly=8_000, months=72
        )
        assert goal_service.suggest_allocation("moderate", needed)["warning"] is None


class TestCpiYoY:
    def test_converts_index_to_yoy_percent(self):
        """H7: เดิมรายงานระดับดัชนี CPI (~320) เป็น 'เงินเฟ้อ'."""
        idx = pd.date_range("2024-01-01", periods=25, freq="MS")
        # ดัชนีเพิ่ม 3% ต่อปี
        series = pd.Series([100 * (1.03 ** (i / 12)) for i in range(25)], index=idx)
        yoy = _cpi_yoy_percent(series)
        assert yoy.iloc[-1] == pytest.approx(3.0, abs=0.1)
        assert len(yoy) == 13

    def test_short_series_returns_empty(self):
        idx = pd.date_range("2026-01-01", periods=6, freq="MS")
        assert _cpi_yoy_percent(pd.Series([100.0] * 6, index=idx)).empty
