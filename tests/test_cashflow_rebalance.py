# -*- coding: utf-8 -*-
"""ทดสอบ rebalance ด้วยเงินใหม่ (Roadmap Phase 4 ข้อ 12) — ไม่ขาย เทงบเข้า underweight."""

import pytest

from portfolio.cashflow_rebalance import rebalance_with_new_money


def test_all_budget_goes_to_underweight_when_gap_exceeds_budget():
    plan = rebalance_with_new_money(
        current_values_thb={"A": 70_000.0, "B": 30_000.0},
        target_weights={"A": 0.5, "B": 0.5},
        budget_thb=20_000.0,
    )
    # หลังเติม 120k เป้าตัวละ 60k → A ไม่ขาด, B ขาด 30k > งบ → งบทั้งหมดเข้า B
    assert "A" not in plan
    assert plan["B"]["amount_thb"] == 20_000
    assert plan["B"]["projected_pct"] == pytest.approx(41.7, abs=0.1)


def test_exact_gap_split():
    plan = rebalance_with_new_money(
        current_values_thb={"A": 55_000.0, "B": 45_000.0},
        target_weights={"A": 0.5, "B": 0.5},
        budget_thb=20_000.0,
    )
    assert plan["A"]["amount_thb"] == 5_000
    assert plan["B"]["amount_thb"] == 15_000
    assert plan["A"]["projected_pct"] == pytest.approx(50.0)
    assert plan["B"]["projected_pct"] == pytest.approx(50.0)


def test_leftover_after_gaps_follows_target_weights():
    plan = rebalance_with_new_money(
        current_values_thb={"A": 55_000.0, "B": 45_000.0},
        target_weights={"A": 0.5, "B": 0.5},
        budget_thb=30_000.0,
    )
    # gap รวม 20k เหลือ 10k แจก 50/50 → A 10k, B 20k → จบที่ 65k/65k
    assert plan["A"]["amount_thb"] == 10_000
    assert plan["B"]["amount_thb"] == 20_000
    assert plan["A"]["projected_pct"] == pytest.approx(50.0)


def test_budget_fully_used_after_rounding():
    plan = rebalance_with_new_money(
        current_values_thb={"A": 10_000.0, "B": 9_000.0, "C": 8_000.0},
        target_weights={"A": 1 / 3, "B": 1 / 3, "C": 1 / 3},
        budget_thb=5_000.0,
    )
    assert sum(item["amount_thb"] for item in plan.values()) == 5_000
    assert all(item["amount_thb"] % 100 == 0 for item in plan.values())


def test_empty_portfolio_fails_loud():
    with pytest.raises(ValueError):
        rebalance_with_new_money({}, {"A": 1.0}, 5_000.0)
    with pytest.raises(ValueError):
        rebalance_with_new_money({"A": 1000.0}, {"A": 1.0}, 0.0)
