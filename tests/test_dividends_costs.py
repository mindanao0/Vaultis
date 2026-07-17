# -*- coding: utf-8 -*-
"""ทดสอบชั้นความจริง Phase 2: ปันผลใน ledger, ต้นทุน/ภาษี, DRIP."""

import pandas as pd
import pytest

from portfolio import costs, drip, tracker


@pytest.fixture()
def temp_ledger(tmp_path, monkeypatch):
    """ledger ชั่วคราว + ตัดเส้นทาง network ของ tracker ออก."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(tracker, "DATA_DIR", data_dir)
    monkeypatch.setattr(tracker, "TRANSACTIONS_FILE", data_dir / "transactions.csv")
    monkeypatch.setattr(tracker, "_get_latest_prices", lambda tickers: {t: 500.0 for t in tickers})
    monkeypatch.setattr(tracker, "_get_usdthb_rate", lambda: 34.0)
    return data_dir


class TestDividendLedger:
    def test_dividend_excluded_from_cost_basis(self, temp_ledger):
        tracker.add_transaction(
            date="2026-06-01", ticker="SCHD", shares=10.0, price_usd=80.0,
            fx_rate_thb=35.0, amount_thb=28000.0,
        )
        tracker.add_dividend(
            date="2026-07-01", ticker="SCHD", amount_usd=17.0, fx_rate_thb=34.0,
        )

        summary = tracker.get_portfolio_summary()
        schd = summary.loc[summary["Ticker"] == "SCHD"].iloc[0]
        assert schd["Shares"] == pytest.approx(10.0)          # ปันผลไม่เพิ่มหุ้น
        assert schd["Invested (USD)"] == pytest.approx(800.0)  # ไม่เข้า cost basis

        dividends = tracker.get_dividends("SCHD")
        assert len(dividends) == 1
        assert dividends.iloc[0]["amount_usd"] == pytest.approx(17.0)

        totals = tracker.get_dividend_summary()
        assert totals["count"] == 1
        assert totals["total_thb"] == pytest.approx(17.0 * 34.0)
        assert totals["by_ticker_thb"]["SCHD"] == pytest.approx(578.0)

    def test_dividend_not_counted_as_trade_number(self, temp_ledger):
        tracker.add_dividend(
            date="2026-07-02", ticker="VOO", amount_usd=5.0, fx_rate_thb=34.0,
        )
        trade_number, fee = tracker.estimate_dime_fee_thb(
            trade_date="2026-07-15", shares=1.0, price_usd=100.0, fx_rate_thb=34.0,
        )
        assert trade_number == 1  # ปันผลเดือนเดียวกันไม่ถูกนับเป็นเทรด
        assert fee == pytest.approx(100.0 * 0.0015 * 34.0)

    def test_old_csv_without_tx_type_rows_are_buys(self, temp_ledger):
        temp_ledger.mkdir(parents=True, exist_ok=True)
        legacy = pd.DataFrame(
            [
                {
                    "tx_id": "old-1", "date": "2025-01-10", "ticker": "VOO",
                    "shares": 2.0, "price_usd": 400.0, "fx_rate_thb": 34.5,
                    "amount_thb": 27600.0, "fee_thb": 41.4, "note": "แถวยุคก่อนมี tx_type",
                }
            ]
        )
        legacy.to_csv(tracker.TRANSACTIONS_FILE, index=False)

        loaded = tracker._load_transactions()
        assert (loaded["tx_type"] == tracker.TX_BUY).all()
        summary = tracker.get_portfolio_summary()
        assert summary.loc[summary["Ticker"] == "VOO"].iloc[0]["Shares"] == pytest.approx(2.0)


class TestCosts:
    def test_net_dividend_yield(self):
        assert costs.net_dividend_yield(0.04) == pytest.approx(0.034)
        with pytest.raises(ValueError):
            costs.net_dividend_yield(-0.01)

    def test_monthly_costs_breakdown(self, monkeypatch):
        monkeypatch.setattr(costs, "fx_spread_pct", lambda: 0.25)
        result = costs.estimate_monthly_costs_thb(5000.0)
        assert result["fee_thb"] == pytest.approx(7.5)
        assert result["fx_spread_thb"] == pytest.approx(12.5)
        assert result["total_thb"] == pytest.approx(20.0)
        assert result["total_pct"] == pytest.approx(0.4)
        assert costs.estimate_monthly_costs_thb(0.0)["total_thb"] == 0.0

    def test_gross_up_net_dividend(self):
        gross, tax = costs.gross_up_net_dividend(85.0)
        assert gross == pytest.approx(100.0)
        assert tax == pytest.approx(15.0)

    def test_annual_dividend_tax(self):
        assert costs.estimate_annual_dividend_tax_thb(100000.0, 0.04) == pytest.approx(600.0)
        assert costs.estimate_annual_dividend_tax_thb(0.0, 0.04) == 0.0


class TestDrip:
    def _closes(self) -> pd.Series:
        idx = pd.to_datetime(["2026-01-05", "2026-02-05", "2026-03-05"])
        return pd.Series([100.0, 100.0, 200.0], index=idx)

    def test_reinvested_dividend_beats_cash_when_price_rises(self):
        dividends = pd.DataFrame(
            [{"date": "2026-01-05", "amount_usd": 100.0}]
        )
        result = drip.simulate_drip(dividends, self._closes())
        assert result["rounds"] == 1
        assert result["extra_shares"] == pytest.approx(1.0)
        assert result["drip_value_usd"] == pytest.approx(200.0)
        assert result["advantage_usd"] == pytest.approx(100.0)

    def test_dividend_before_price_history_is_skipped(self):
        dividends = pd.DataFrame(
            [
                {"date": "2025-12-01", "amount_usd": 50.0},   # ก่อนมีราคา — ต้องข้าม
                {"date": "2026-02-05", "amount_usd": 100.0},
            ]
        )
        result = drip.simulate_drip(dividends, self._closes())
        assert result["rounds"] == 1
        assert result["skipped"] == 1
        assert result["cash_usd"] == pytest.approx(100.0)

    def test_no_price_history_fails_loud(self):
        with pytest.raises(ValueError):
            drip.simulate_drip(pd.DataFrame([{"date": "2026-01-05", "amount_usd": 10.0}]), pd.Series(dtype=float))
