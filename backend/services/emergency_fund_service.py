"""Emergency Fund Calculator: risk scoring, multiplier, and recommendation."""

from __future__ import annotations

import math

from ..models.emergency_fund_models import EmergencyFundResult, RiskProfile

_JOB_STABILITY_SCORE: dict[str, int] = {
    "very_stable": 0,
    "stable": 20,
    "unstable": 60,
    "freelance": 80,
}

_INCOME_TYPE_SCORE: dict[str, int] = {
    "salary": 0,
    "mixed": 15,
    "freelance": 35,
    "business": 40,
}

_INDUSTRY_SCORE: dict[str, int] = {
    "government": -10,
    "startup": 20,
    "self_employed": 15,
    "other": 0,
}

_MULTIPLIER_TABLE: list[tuple[int, float]] = [
    (20, 2.5),
    (40, 3.5),
    (60, 5.0),
    (80, 6.5),
    (100, 8.0),
]


def calculate_risk_score(profile: RiskProfile) -> int:
    score = 0
    score += _JOB_STABILITY_SCORE[profile.job_stability]
    score += min(profile.dependents, 3) * 10   # 0=0, 1=10, 2=20, 3+=30
    score += _INCOME_TYPE_SCORE[profile.income_type]
    if not profile.has_health_insurance:
        score += 20
    score += _INDUSTRY_SCORE[profile.industry]
    return max(0, min(100, score))


def get_multiplier(risk_score: int) -> float:
    for threshold, multiplier in _MULTIPLIER_TABLE:
        if risk_score <= threshold:
            return multiplier
    return 8.0  # unreachable after clamp, but satisfies type checker


def generate_recommendation(result: EmergencyFundResult) -> str:
    score = result.risk_score
    if score <= 20:
        risk_label, advice = "ต่ำ", "สถานการณ์ทางการเงินของคุณค่อนข้างมั่นคง"
    elif score <= 40:
        risk_label, advice = "ปานกลาง", "ควรเริ่มสะสมเงินสำรองอย่างสม่ำเสมอ"
    elif score <= 60:
        risk_label, advice = "สูง", "ควรให้ความสำคัญกับเงินสำรองฉุกเฉินเป็นอันดับแรก"
    elif score <= 80:
        risk_label, advice = "สูงมาก", "เงินสำรองฉุกเฉินเป็นสิ่งจำเป็นเร่งด่วน"
    else:
        risk_label, advice = "วิกฤต", "ให้หยุดลงทุนทุกอย่างและสะสมเงินสำรองก่อนเป็นอันดับแรก"

    lines = [
        f"ระดับความเสี่ยง: {risk_label} (คะแนน {score}/100) — {advice}",
        f"เป้าหมายเงินสำรอง: {result.multiplier:.1f} เดือน = {result.target_amount:,.0f} บาท",
    ]

    if result.gap <= 0:
        lines.append(
            f"✅ คุณมีเงินสำรองเพียงพอแล้ว (เกินเป้า {abs(result.gap):,.0f} บาท)"
        )
    else:
        lines.append(f"ยังขาดอีก {result.gap:,.0f} บาท")
        if result.months_to_goal is not None:
            total = result.months_to_goal
            years = int(total // 12)
            rem = int(total % 12)
            if years > 0 and rem > 0:
                time_str = f"{years} ปี {rem} เดือน"
            elif years > 0:
                time_str = f"{years} ปี"
            else:
                time_str = f"{rem} เดือน"
            lines.append(f"ด้วยการออมปัจจุบัน จะครบเป้าหมายภายใน {time_str}")
        else:
            lines.append("กรุณาระบุจำนวนเงินออมรายเดือนเพื่อคำนวณระยะเวลา")

    return "\n".join(lines)


def calculate(
    profile: RiskProfile,
    monthly_expense: float,
    current_savings: float,
    monthly_saving_capacity: float,
) -> EmergencyFundResult:
    risk_score = calculate_risk_score(profile)
    multiplier = get_multiplier(risk_score)
    target = round(monthly_expense * multiplier, 2)
    gap = round(target - current_savings, 2)

    if gap <= 0 or monthly_saving_capacity <= 0:
        months_to_goal = None
    else:
        months_to_goal = math.ceil(gap / monthly_saving_capacity)

    result = EmergencyFundResult(
        risk_score=risk_score,
        multiplier=multiplier,
        target_amount=target,
        current_savings=current_savings,
        gap=gap,
        months_to_goal=months_to_goal,
        recommendation="",
    )
    result.recommendation = generate_recommendation(result)
    return result
