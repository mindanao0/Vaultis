# -*- coding: utf-8 -*-
"""ทดสอบ shadow_benchmark และ xirr (Roadmap Phase 4 ข้อ 14)."""

import pandas as pd
import pytest

from portfolio.benchmark import shadow_benchmark, xirr


class TestShadowBenchmark:
    def _closes(self) -> pd.Series:
        idx = pd.to_datetime(["2025-01-02", "2025-06-02", "2026-01-02"])
        return pd.Series([50.0, 80.0, 100.0], index=idx)

    def test_same_money_same_day_into_benchmark(self):
        buys = pd.DataFrame(
            [
                {"date": "2025-01-02", "shares": 2.0, "price_usd": 50.0},   # 100 USD → 2 หุ้นเงา
                {"date": "2025-06-02", "shares": 1.0, "price_usd": 160.0},  # 160 USD → 2 หุ้นเงา
            ]
        )
        result = shadow_benchmark(buys, self._closes())
        assert result["rounds"] == 2
        assert result["invested_usd"] == pytest.approx(260.0)
        assert result["benchmark_shares"] == pytest.approx(4.0)
        assert result["benchmark_value_usd"] == pytest.approx(400.0)  # 4 × ราคาปัจจุบัน 100

    def test_buy_before_history_is_skipped_not_guessed(self):
        buys = pd.DataFrame(
            [
                {"date": "2024-01-01", "shares": 1.0, "price_usd": 100.0},
                {"date": "2025-06-02", "shares": 1.0, "price_usd": 80.0},
            ]
        )
        result = shadow_benchmark(buys, self._closes())
        assert result["skipped"] == 1
        assert result["rounds"] == 1
        assert result["invested_usd"] == pytest.approx(80.0)

    def test_empty_benchmark_prices_fail_loud(self):
        with pytest.raises(ValueError):
            shadow_benchmark(pd.DataFrame([{"date": "2025-01-02", "shares": 1, "price_usd": 1}]), pd.Series(dtype=float))


class TestXirr:
    def test_single_year_ten_percent(self):
        flows = [(pd.Timestamp("2025-01-01"), -100.0), (pd.Timestamp("2026-01-01"), 110.0)]
        rate = xirr(flows)
        assert rate == pytest.approx(0.10, abs=1e-3)

    def test_multiple_flows(self):
        flows = [
            (pd.Timestamp("2024-01-01"), -100.0),
            (pd.Timestamp("2025-01-01"), -100.0),
            (pd.Timestamp("2026-01-01"), 231.0),
        ]
        rate = xirr(flows)
        assert rate is not None and 0.05 < rate < 0.15

    def test_all_negative_returns_none(self):
        flows = [(pd.Timestamp("2025-01-01"), -100.0), (pd.Timestamp("2026-01-01"), -10.0)]
        assert xirr(flows) is None

    def test_insufficient_flows_return_none(self):
        assert xirr([]) is None
        assert xirr([(pd.Timestamp("2025-01-01"), -100.0)]) is None

    def test_total_loss_bounded(self):
        flows = [(pd.Timestamp("2025-01-01"), -100.0), (pd.Timestamp("2026-01-01"), 1.0)]
        rate = xirr(flows)
        assert rate is not None and rate < -0.9
