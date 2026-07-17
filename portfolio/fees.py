# -*- coding: utf-8 -*-
"""แหล่งเดียวของสูตรค่าธรรมเนียมโบรกเกอร์ (Dime) ทั้งระบบ.

มติผู้ใช้ 2026-07-16: ค่าธรรมเนียม = 0.15% ของมูลค่าซื้อขาย **ทุก transaction**
(ยืนยันจากบัญชีจริง — ไม่มีโปรเทรดแรกของเดือนฟรี)

เดิมสูตรกระจายอยู่สองที่และขัดกัน (Roadmap Phase 0 ข้อ 2):
- ``portfolio/tracker.py`` คิดเทรดแรกของเดือนฟรี (ผิด)
- ``backend/services/rebalance_service.py`` คิดทุกครั้ง (ถูก)

ห้ามประกาศอัตรา/สูตรค่าธรรมเนียมซ้ำที่อื่น — import จากไฟล์นี้เท่านั้น
"""

from __future__ import annotations

DIME_FEE_RATE = 0.0015  # 0.15% ต่อ transaction


def dime_fee_thb(trade_value_usd: float, fx_rate_thb: float) -> float:
    """ค่าธรรมเนียม Dime เป็นบาท = มูลค่าซื้อขาย (USD) × 0.15% × อัตราแลกเปลี่ยน.

    รับ ``pandas.Series`` ได้เช่นกัน (คูณ elementwise) — ทั้งเส้นทาง scalar
    และ vectorized ต้องใช้สูตรเดียวกันจากฟังก์ชันนี้
    """
    return trade_value_usd * DIME_FEE_RATE * fx_rate_thb
