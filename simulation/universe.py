# -*- coding: utf-8 -*-
"""รายชื่อกอง + "ชนิด" ของแต่ละกอง — simulation ไม่ผูกกับห้ากองตายตัว.

ชนิด (``kind``) ใช้ 2 อย่างเท่านั้น: (1) ขนาดของเหตุการณ์ใหญ่ที่กระทบกองนั้น (2) ผลตอบแทนคาดหวังข้างหน้าที่สมมติ
ทั้งสองอย่างเป็น **ข้อสมมติ** (ไม่ใช่ข้อมูลที่วัด) ยกเว้นตัวที่ระบุว่าวัดจากข้อมูลใน ``engine.build_events``
กองที่ไม่รู้จัก = ``other_equity`` (ถือว่าขยับเท่าตลาดหุ้นสหรัฐ) และ **ต้องบอกผู้ใช้** ว่าชนิดนี้เป็นการเดา
"""
from __future__ import annotations

from dataclasses import dataclass

KINDS = (
    "us_equity", "us_dividend", "us_growth", "us_health", "gold",
    "intl_equity", "em_equity", "reit", "bond", "other_equity",
)


@dataclass(frozen=True)
class Asset:
    ticker: str
    kind: str
    #: กองพี่ที่ตามดัชนีเดียวกันและมีประวัติยาวกว่า — ใช้ยืดประวัติ **เฉพาะตอนสอบเทียบโมเดล** (ผลตอบแทนช่วงนั้นเป็นของกองพี่)
    calib_proxy: str | None = None
    note: str = ""


#: ห้ากองของพอร์ตหลัก (ตรงกับ research/dar_sim — เทสต์ parity ผูกไว้)
CORE_ASSETS: tuple[Asset, ...] = (
    Asset("VOO", "us_equity", "SPY"),
    Asset("SCHD", "us_dividend", "DVY"),
    Asset("QQQM", "us_growth", "QQQ"),
    Asset("XLV", "us_health", None),
    Asset("GLDM", "gold", "GLD"),
)

#: กองที่รู้จักเพิ่ม (ใช้เมื่อผู้ใช้เพิ่มใน Settings) — **ยังไม่ได้ตรวจว่าซื้อได้บน Dime** ผู้ใช้ต้องตรวจเอง
KNOWN_EXTRA_ASSETS: tuple[Asset, ...] = (
    Asset("VXUS", "intl_equity", "VEU", "หุ้นนอกสหรัฐทั้งตลาด"),
    Asset("VEA", "intl_equity", "EFA", "หุ้นพัฒนาแล้วนอกสหรัฐ"),
    Asset("VWO", "em_equity", "EEM", "ตลาดเกิดใหม่"),
    Asset("IEMG", "em_equity", "EEM", "ตลาดเกิดใหม่"),
    Asset("BND", "bond", "AGG", "พันธบัตรสหรัฐรวม"),
    Asset("AGG", "bond", None, "พันธบัตรสหรัฐรวม"),
    Asset("TLT", "bond", None, "พันธบัตรรัฐบาลสหรัฐระยะยาว"),
    Asset("TIP", "bond", None, "พันธบัตรผูกเงินเฟ้อสหรัฐ"),
    Asset("VNQ", "reit", None, "อสังหาริมทรัพย์สหรัฐ (REIT)"),
    Asset("IAU", "gold", "GLD", "ทอง"),
    Asset("SPY", "us_equity", None), Asset("QQQ", "us_growth", None), Asset("VTI", "us_equity", "SPY"),
    Asset("VYM", "us_dividend", "DVY"), Asset("VGK", "intl_equity", "EFA", "ยุโรป"),
)

_REGISTRY = {a.ticker: a for a in CORE_ASSETS + KNOWN_EXTRA_ASSETS}


def asset_for(ticker: str) -> Asset:
    """รู้จัก → ตามตาราง · ไม่รู้จัก → ``other_equity`` ไม่มีกองพี่ (ผู้เรียกต้องแจ้งผู้ใช้ว่าเป็นการเดา)."""
    t = str(ticker).strip().upper()
    return _REGISTRY.get(t) or Asset(t, "other_equity", None, "ไม่รู้จักชนิด — ใช้ค่าสมมติของหุ้นสหรัฐ")


def is_guessed(ticker: str) -> bool:
    return str(ticker).strip().upper() not in _REGISTRY


def extra_tickers() -> list[str]:
    """กองเสริมที่ดึงข้อมูลไว้เสมอ (นอกเหนือจากกองที่ผู้ใช้ติดตาม) — ให้ลองสัดส่วนที่มีสินทรัพย์เหล่านี้ใน simulation ได้ทันที."""
    return [a.ticker for a in KNOWN_EXTRA_ASSETS]


def universe_for(tickers: list[str] | tuple[str, ...]) -> tuple[Asset, ...]:
    out, seen = [], set()
    for t in tickers:
        a = asset_for(t)
        if a.ticker not in seen:
            seen.add(a.ticker)
            out.append(a)
    if not out:
        raise ValueError("ไม่มีกองให้จำลอง")
    return tuple(out)


#: กองที่ต้องมีในข้อมูลดิบเสมอ ไว้วัดเหตุการณ์ฟองสบู่หุ้นเทคปี 2000–02 (SPY/QQQ/XLV) — ไม่ว่าผู้ใช้ถือกองอะไร
CALIBRATION_REQUIRED = ("SPY", "QQQ", "XLV")
