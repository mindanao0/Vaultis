from __future__ import annotations

from typing import Any

from alerts.price_alert import get_current_prices
from analysis.llm import LLMDisabledError, chat_text
from portfolio.fees import dime_fee_thb
from portfolio.targets import RISK_PROFILES
from utils import fx

DRIFT_THRESHOLD = 0.05  # 5%

# สัดส่วนเป้าหมายมาจากแหล่งเดียว (portfolio/targets.py) — เดิมมี 2 ชุดที่ไม่ตรงกัน
# ทำให้แผน DCA กับแผน rebalance ดึงพอร์ตไปคนละทาง
TARGET_WEIGHTS = RISK_PROFILES

_RISK_PROFILE_TH = {
    "conservative": "อนุรักษ์นิยม",
    "moderate": "สมดุล",
    "aggressive": "เชิงรุก",
}


def _get_usdthb_rate() -> float:
    """ใช้แหล่ง FX กลางเดียวของระบบ — เดิม fallback 35.0 ต่างจากที่อื่น (AUDIT.md M5)."""
    return fx.get_usdthb_rate()


def calculate_drift(
    holdings: list[dict[str, Any]],
    target: dict[str, float],
    prices: dict[str, float],
) -> float:
    """คืนค่า drift สูงสุด (0–1) เทียบกับ target weights"""
    values: dict[str, float] = {}
    for h in holdings:
        sym = str(h["symbol"]).upper()
        price = prices.get(sym, 0.0)
        values[sym] = float(h["shares"]) * price

    total = sum(values.values())
    if total <= 0:
        return 1.0

    max_drift = 0.0
    for sym, target_w in target.items():
        current_w = values.get(sym, 0.0) / total
        drift = abs(current_w - target_w)
        if drift > max_drift:
            max_drift = drift
    return max_drift


def _build_actions(
    holdings: list[dict[str, Any]],
    target: dict[str, float],
    prices: dict[str, float],
    budget_usd: float,
    fx_rate: float,
) -> list[dict[str, Any]]:
    values: dict[str, float] = {}
    for h in holdings:
        sym = str(h["symbol"]).upper()
        price = prices.get(sym, 0.0)
        values[sym] = float(h["shares"]) * price

    total_usd = sum(values.values()) + budget_usd
    actions: list[dict[str, Any]] = []

    for sym, target_w in target.items():
        price = prices.get(sym)
        if not price:
            continue

        target_value = target_w * total_usd
        current_value = values.get(sym, 0.0)
        delta_usd = target_value - current_value

        if abs(delta_usd) < 0.01:
            action_type = "hold"
            shares_delta = 0.0
            usd_amount = 0.0
        elif delta_usd > 0:
            action_type = "buy"
            usd_amount = delta_usd
            shares_delta = usd_amount / price
        else:
            action_type = "sell"
            usd_amount = abs(delta_usd)
            shares_delta = usd_amount / price

        fee_thb = dime_fee_thb(usd_amount, fx_rate) if action_type != "hold" else 0.0

        actions.append({
            "symbol": sym,
            "action": action_type,
            "shares": round(shares_delta, 6),
            "usd_amount": round(usd_amount, 2),
            "thb_amount": round(usd_amount * fx_rate, 2),
            "fee_thb": round(fee_thb, 2),
        })

    return actions


def _generate_ai_comment(
    risk_profile: str,
    max_drift: float,
    actions: list[dict[str, Any]],
    target: dict[str, float],
    prices: dict[str, float],
    holdings: list[dict[str, Any]],
    user_initiated: bool = False,
) -> str:
    values: dict[str, float] = {}
    for h in holdings:
        sym = str(h["symbol"]).upper()
        values[sym] = float(h["shares"]) * prices.get(sym, 0.0)
    total = sum(values.values()) or 1.0

    overweight = [
        f"{sym} ({values.get(sym, 0.0)/total*100:.1f}% vs เป้า {tw*100:.0f}%)"
        for sym, tw in target.items()
        if values.get(sym, 0.0) / total - tw > 0.01
    ]
    underweight = [
        f"{sym} ({values.get(sym, 0.0)/total*100:.1f}% vs เป้า {tw*100:.0f}%)"
        for sym, tw in target.items()
        if tw - values.get(sym, 0.0) / total > 0.01
    ]

    buys = [a["symbol"] for a in actions if a["action"] == "buy"]
    sells = [a["symbol"] for a in actions if a["action"] == "sell"]
    action_summary = ""
    if buys:
        action_summary += f"ซื้อเพิ่ม: {', '.join(buys)}  "
    if sells:
        action_summary += f"ขาย: {', '.join(sells)}"

    profile_th = _RISK_PROFILE_TH.get(risk_profile, risk_profile)
    user_msg = (
        f"พอร์ตโฟลิโอมีการเบี่ยงเบนสูงสุด {max_drift*100:.1f}% จากสัดส่วนเป้าหมายของโปรไฟล์ {profile_th}\n"
        f"ETF เกินสัดส่วน: {', '.join(overweight) if overweight else 'ไม่มี'}\n"
        f"ETF ขาดสัดส่วน: {', '.join(underweight) if underweight else 'ไม่มี'}\n"
        f"แผน rebalance: {action_summary if action_summary else 'ไม่มีการซื้อขาย'}\n"
        "อธิบายสั้น ๆ ว่าทำไมต้อง rebalance และประโยชน์ที่ได้รับ"
    )

    system_prompt = (
        "คุณเป็นที่ปรึกษาการลงทุนสำหรับนักลงทุนรายย่อยชาวไทย "
        "ให้คำแนะนำเกี่ยวกับการ rebalance พอร์ตโฟลิโอ ETF "
        "ตัวเลขทั้งหมดคำนวณมาแล้ว — อธิบายเท่านั้น ห้ามคำนวณใหม่ "
        "อธิบายเป็นภาษาไทย กระชับ ชัดเจน ไม่เกิน 3 ประโยค"
    )
    try:
        return chat_text(
            system_prompt, user_msg, max_tokens=600, temperature=0.3, user_initiated=user_initiated
        )
    except LLMDisabledError:
        return ""  # แผน rebalance (ตัวเลข) ยังครบ — แค่ไม่มีคำอธิบายจาก AI
    except Exception as exc:
        return f"ไม่สามารถสร้างคำแนะนำได้: {exc}"


def compute_rebalance(
    holdings: list[dict[str, Any]],
    risk_profile: str,
    available_budget_thb: float,
    user_initiated: bool = False,
) -> dict[str, Any]:
    target = TARGET_WEIGHTS[risk_profile]
    all_symbols = list({str(h["symbol"]).upper() for h in holdings} | set(target.keys()))

    prices = get_current_prices(all_symbols)
    fx_rate = _get_usdthb_rate()
    budget_usd = available_budget_thb / fx_rate

    max_drift = calculate_drift(holdings, target, prices)

    if max_drift < DRIFT_THRESHOLD:
        return {
            "needs_rebalance": False,
            "max_drift_pct": round(max_drift * 100, 2),
            "actions": [],
            "total_fee_thb": 0.0,
            "ai_comment": "",
        }

    actions = _build_actions(holdings, target, prices, budget_usd, fx_rate)
    total_fee_thb = round(sum(a["fee_thb"] for a in actions), 2)
    ai_comment = _generate_ai_comment(
        risk_profile, max_drift, actions, target, prices, holdings, user_initiated=user_initiated
    )

    return {
        "needs_rebalance": True,
        "max_drift_pct": round(max_drift * 100, 2),
        "actions": actions,
        "total_fee_thb": total_fee_thb,
        "ai_comment": ai_comment,
    }
