# -*- coding: utf-8 -*-
"""ชั้นต้นทุน/ภาษีที่มองไม่เห็น (Roadmap Phase 2 ข้อ 4) — ค่าคงที่และสูตร แหล่งเดียว.

- ภาษีหัก ณ ที่จ่ายปันผล US สำหรับผู้ถือไทย = 15% (สนธิสัญญาภาษีซ้อน US-ไทย
  — โบรกหักให้อัตโนมัติก่อนจ่าย กระทบ SCHD หนักสุดเพราะ yield สูงสุดในพอร์ต)
- FX spread = **ประมาณการ** ตั้งได้ใน config.json (``costs.fx_spread_pct``)
  UI ต้องระบุว่าเป็นค่าประมาณเสมอ — ไม่ใช่ค่าที่ยืนยันจากบัญชีจริง
- ค่าคอม Dime อยู่ใน ``portfolio/fees.py`` (0.15% ทุก transaction) — ไม่ประกาศซ้ำที่นี่
- ภาษีเงินได้ไทยกรณีนำเงินกลับประเทศ (ปอ.161/2566 มีผลปีภาษี 2567): **ไม่เข้าเลขคำนวณ**
  เป็นเพียง disclaimer ใน UI เพราะขึ้นกับสถานการณ์ภาษีรายบุคคล
"""

from __future__ import annotations

from portfolio.fees import DIME_FEE_RATE
from utils.config import load_config

US_DIVIDEND_WITHHOLDING = 0.15


def net_dividend_yield(gross_yield: float) -> float:
    """yield สุทธิหลังภาษีหัก ณ ที่จ่าย 15% (รับสัดส่วน เช่น 0.035 = 3.5%)."""
    if gross_yield < 0:
        raise ValueError("dividend yield ติดลบไม่ได้")
    return gross_yield * (1.0 - US_DIVIDEND_WITHHOLDING)


def fx_spread_pct() -> float:
    """FX spread โดยประมาณ (%) จาก config — ค่าประมาณการ ผู้ใช้ปรับให้ตรงบัญชีจริงได้."""
    return float(load_config()["costs"]["fx_spread_pct"])


def estimate_monthly_costs_thb(budget_thb: float) -> dict[str, float]:
    """ต้นทุนโดยประมาณของการซื้อ DCA หนึ่งรอบ: ค่าคอม + FX spread.

    คืน ``{"fee_thb", "fx_spread_thb", "total_thb", "total_pct"}``
    (งบ ≤ 0 → ศูนย์ทุกช่อง — ไม่มีการซื้อก็ไม่มีต้นทุน)
    """
    if budget_thb <= 0:
        return {"fee_thb": 0.0, "fx_spread_thb": 0.0, "total_thb": 0.0, "total_pct": 0.0}
    fee = budget_thb * DIME_FEE_RATE
    spread = budget_thb * fx_spread_pct() / 100.0
    total = fee + spread
    return {
        "fee_thb": fee,
        "fx_spread_thb": spread,
        "total_thb": total,
        "total_pct": total / budget_thb * 100.0,
    }


def estimate_annual_dividend_tax_thb(holding_value_thb: float, gross_yield: float) -> float:
    """ภาษีปันผลที่จะถูกหักต่อปีโดยประมาณ = มูลค่าถือครอง × gross yield × 15%."""
    if holding_value_thb <= 0 or gross_yield <= 0:
        return 0.0
    return holding_value_thb * gross_yield * US_DIVIDEND_WITHHOLDING


def gross_up_net_dividend(net_amount: float) -> tuple[float, float]:
    """แปลงยอดปันผลสุทธิที่รับจริง → (ยอด gross โดยประมาณ, ภาษีที่ถูกหักโดยประมาณ).

    ใช้อธิบายย้อนหลังจากยอดที่บันทึกใน ledger (บันทึกเป็น net เสมอ)
    """
    if net_amount <= 0:
        return 0.0, 0.0
    gross = net_amount / (1.0 - US_DIVIDEND_WITHHOLDING)
    return gross, gross - net_amount
