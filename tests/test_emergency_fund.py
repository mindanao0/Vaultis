"""Unit tests for Emergency Fund Calculator — risk scoring and multiplier logic."""

import math

import pytest

from backend.models.emergency_fund_models import RiskProfile
from backend.services.emergency_fund_service import (
    calculate,
    calculate_risk_score,
    get_multiplier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def profile(**kwargs) -> RiskProfile:
    defaults = dict(
        job_stability="stable",
        dependents=0,
        income_type="salary",
        has_health_insurance=True,
        industry="other",
    )
    defaults.update(kwargs)
    return RiskProfile(**defaults)


# ---------------------------------------------------------------------------
# Risk score: individual factors
# ---------------------------------------------------------------------------

def test_job_stability_very_stable():
    assert calculate_risk_score(profile(job_stability="very_stable")) == 0


def test_job_stability_stable():
    assert calculate_risk_score(profile(job_stability="stable")) == 20


def test_job_stability_unstable():
    assert calculate_risk_score(profile(job_stability="unstable")) == 60


def test_job_stability_freelance():
    assert calculate_risk_score(profile(job_stability="freelance")) == 80


def test_dependents_0():
    assert calculate_risk_score(profile(dependents=0)) == 20


def test_dependents_1():
    assert calculate_risk_score(profile(dependents=1)) == 30


def test_dependents_2():
    assert calculate_risk_score(profile(dependents=2)) == 40


def test_dependents_3_plus():
    # 3 and above should all yield the same +30
    assert calculate_risk_score(profile(dependents=3)) == 50
    assert calculate_risk_score(profile(dependents=5)) == 50


def test_income_type_salary():
    assert calculate_risk_score(profile(income_type="salary")) == 20


def test_income_type_mixed():
    assert calculate_risk_score(profile(income_type="mixed")) == 35


def test_income_type_freelance():
    assert calculate_risk_score(profile(income_type="freelance")) == 55


def test_income_type_business():
    assert calculate_risk_score(profile(income_type="business")) == 60


def test_no_health_insurance_adds_20():
    base = calculate_risk_score(profile(has_health_insurance=True))
    no_ins = calculate_risk_score(profile(has_health_insurance=False))
    assert no_ins - base == 20


def test_industry_government_subtracts_10():
    score = calculate_risk_score(profile(job_stability="very_stable", industry="government"))
    assert score == 0   # -10 clamped to 0


def test_industry_startup_adds_20():
    base = calculate_risk_score(profile(industry="other"))
    startup = calculate_risk_score(profile(industry="startup"))
    assert startup - base == 20


def test_industry_self_employed_adds_15():
    base = calculate_risk_score(profile(industry="other"))
    se = calculate_risk_score(profile(industry="self_employed"))
    assert se - base == 15


# ---------------------------------------------------------------------------
# Risk score: clamp behaviour
# ---------------------------------------------------------------------------

def test_score_floor_zero():
    # very_stable salary, government, insured, 0 dependents → -10 → clamped to 0
    s = calculate_risk_score(profile(
        job_stability="very_stable",
        dependents=0,
        income_type="salary",
        has_health_insurance=True,
        industry="government",
    ))
    assert s == 0


def test_score_ceiling_100():
    # worst possible inputs sum well above 100
    s = calculate_risk_score(profile(
        job_stability="freelance",
        dependents=5,
        income_type="business",
        has_health_insurance=False,
        industry="startup",
    ))
    assert s == 100


# ---------------------------------------------------------------------------
# Multiplier boundaries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,expected", [
    (0,   2.5),
    (20,  2.5),
    (21,  3.5),
    (40,  3.5),
    (41,  5.0),
    (60,  5.0),
    (61,  6.5),
    (80,  6.5),
    (81,  8.0),
    (100, 8.0),
])
def test_multiplier_boundaries(score, expected):
    assert get_multiplier(score) == expected


# ---------------------------------------------------------------------------
# Full calculate()
# ---------------------------------------------------------------------------

def test_calculate_already_funded():
    p = profile(job_stability="very_stable", dependents=0, income_type="salary",
                has_health_insurance=True, industry="government")
    result = calculate(p, monthly_expense=30_000, current_savings=200_000,
                       monthly_saving_capacity=5_000)
    # risk_score=0, multiplier=2.5, target=75_000; current_savings > target → gap < 0
    assert result.risk_score == 0
    assert result.multiplier == 2.5
    assert result.target_amount == 75_000.0
    assert result.gap < 0
    assert result.months_to_goal is None


def test_calculate_gap_and_months():
    p = profile(job_stability="stable", dependents=0, income_type="salary",
                has_health_insurance=True, industry="other")
    # risk_score=20, multiplier=2.5, target=2.5*20000=50000
    result = calculate(p, monthly_expense=20_000, current_savings=10_000,
                       monthly_saving_capacity=5_000)
    assert result.target_amount == 50_000.0
    assert result.gap == 40_000.0
    assert result.months_to_goal == math.ceil(40_000 / 5_000)  # 8


def test_calculate_zero_saving_capacity():
    p = profile()
    result = calculate(p, monthly_expense=20_000, current_savings=0,
                       monthly_saving_capacity=0)
    assert result.months_to_goal is None


def test_calculate_recommendation_not_empty():
    p = profile()
    result = calculate(p, monthly_expense=20_000, current_savings=5_000,
                       monthly_saving_capacity=3_000)
    assert len(result.recommendation) > 0


def test_calculate_high_risk_multiplier():
    p = profile(
        job_stability="freelance",
        dependents=3,
        income_type="business",
        has_health_insurance=False,
        industry="startup",
    )
    result = calculate(p, monthly_expense=50_000, current_savings=0,
                       monthly_saving_capacity=10_000)
    assert result.risk_score == 100
    assert result.multiplier == 8.0
    assert result.target_amount == 400_000.0
    assert result.months_to_goal == 40
