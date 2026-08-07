# -*- coding: utf-8 -*-
"""สัดส่วนพอร์ตเป้าหมาย — แหล่งเดียวของทั้งระบบ.

เดิมมีชุดเป้าหมาย 2 ชุดที่ไม่ตรงกัน:
- dashboard / main.py : VOO 35 / SCHD 20 / QQQM 20 / XLV 15 / GLDM 10
- rebalance / goals   : VOO 35 / SCHD 25 / QQQM 20 / XLV 10 / GLDM 10
→ แผน DCA กับแผน rebalance ดึงพอร์ตไปคนละทาง

ตอนนี้ทุกที่อ่านจาก ``get_target_weights()`` ซึ่งมาจาก config.json
(``portfolio.risk_profile`` + ``portfolio.target_weights`` ถ้าตั้งเอง)

กติกาของ ``portfolio.target_weights`` (AUDIT_2026-08-06 B10)
-----------------------------------------------------------
**"มีคีย์" = ตั้งแล้ว · "ไม่มีคีย์" = ไม่ได้ตั้ง** — สองอย่างนี้คนละความหมายกัน
เดิมโค้ดยุบรวมกัน (``0`` และค่าลบถูกทิ้งเงียบ ๆ) ทำให้ ``{"GLDM": 0}`` ซึ่งแปลว่า
"ตั้งใจไม่ถือทอง" กลายเป็น "GLDM 10% ตาม preset"

1. ticker ที่ **ตั้งไว้** ได้ค่านั้นเป๊ะ ๆ ไม่ถูก normalize บิด (ตั้ง ``0`` = ไม่ถือ)
2. ticker ที่ **ไม่ได้ตั้ง** แบ่งน้ำหนักที่เหลือ (``1 − ผลรวมที่ตั้งไว้``) กันเอง
   **ตามอัตราส่วนของ preset** ตาม ``risk_profile`` — อัตราส่วนระหว่างกันจึงไม่เพี้ยน
   ticker ที่ preset ไม่รู้จัก (เพิ่งเพิ่มจากหน้า Settings) นับเป็นตำแหน่งขนาดเฉลี่ย
   คือ ``1 / จำนวน ticker ใน preset`` เพื่อไม่ให้ถูกละเลยและได้ค่าที่ทำนายได้
   (เดิม docstring เขียนว่า "แบ่งเท่า ๆ กัน" แต่กิ่งนั้นไม่มีวันทำงานเพราะ preset
   ครอบ ticker ดีฟอลต์ครบทุกตัว — ทุกตัวจึงถูก normalize ทั้งชุดแทน)
3. หน่วยต้องชัด: ผลรวมของค่าที่ตั้ง ต้องอยู่ใน ``[0, 1]`` (สัดส่วน) หรือ ``100`` เป๊ะ
   (เปอร์เซ็นต์) นอกนั้นโยน :class:`InvalidTargetWeights` แทนที่จะเดาหน่วยให้
4. ค่าติดลบ / ไม่ใช่ตัวเลข / NaN / โครงสร้างผิดรูป → :class:`InvalidTargetWeights`
5. ถ้าค่าที่ใช้จริงต่างจากที่ตั้ง (เช่นตั้งครบทุกตัวแต่รวมได้ 90% ระบบต้องขยายให้เต็ม)
   ``get_target_weights_with_status()`` จะคืน ``adjusted=True`` + ``notes`` ภาษาไทย
   ให้หน้า Settings แสดง — ห้ามแก้ตัวเลขให้เงียบ ๆ

``get_target_weights()`` คืนเฉพาะน้ำหนัก (รวมเป็น 1.0 เสมอ) สำหรับผู้เรียกที่ไม่ต้องการ
สถานะ ส่วน ``get_target_weights_with_status()`` คืนทั้งก้อน — รูปแบบเดียวกับ
``get_news()`` / ``get_news_with_status()``
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from utils.config import get_tickers, load_config

RISK_PROFILES: dict[str, dict[str, float]] = {
    "conservative": {"VOO": 0.30, "SCHD": 0.30, "QQQM": 0.10, "XLV": 0.20, "GLDM": 0.10},
    "moderate":     {"VOO": 0.35, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.10},
    "aggressive":   {"VOO": 0.25, "SCHD": 0.10, "QQQM": 0.45, "XLV": 0.10, "GLDM": 0.10},
}

DEFAULT_PROFILE = "moderate"

# ผลรวมที่ตั้งไว้เกิน 1.0 ได้ไม่เกินนี้ถือว่าเป็นเศษทศนิยม (0.35+0.25+0.2+0.1+0.1 = 1.0000000000000002)
_TOL = 1e-9
# ผลรวม ≈ 100 = ผู้ใช้เขียนเป็นเปอร์เซ็นต์
_PERCENT_TOTAL = 100.0
_PERCENT_TOL = 1e-6


class InvalidTargetWeights(ValueError):
    """``portfolio.target_weights`` ใน config.json ผิดรูป — ห้ามเดาแทนผู้ใช้."""


@dataclass(frozen=True)
class TargetWeights:
    """น้ำหนักเป้าหมายพร้อมที่มาและคำเตือน.

    ``weights``     สัดส่วนที่ใช้จริง รวมเป็น 1.0 เสมอ
    ``profile``     risk profile ที่ใช้เป็นฐานของ ticker ที่ไม่ได้ตั้ง
    ``configured``  ค่าที่ผู้ใช้ตั้งไว้ (แปลงเป็นสัดส่วนแล้ว) เฉพาะ ticker ในรายการนี้
    ``source``      symbol → ``custom`` (ผู้ใช้ตั้ง) / ``preset`` / ``unknown`` (preset ไม่รู้จัก)
    ``notes``       ข้อความไทยสำหรับหน้า Settings — ว่างเมื่อทุกอย่างตรงตามที่ตั้ง
    ``adjusted``    ``True`` เมื่อค่าที่ใช้จริงของ ticker ที่ "ตั้งไว้" ต่างจากที่ตั้ง
    """

    weights: dict[str, float]
    profile: str
    configured: dict[str, float] = field(default_factory=dict)
    source: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    adjusted: bool = False


def get_risk_profile() -> str:
    profile = str(load_config()["portfolio"].get("risk_profile", DEFAULT_PROFILE)).strip().lower()
    return profile if profile in RISK_PROFILES else DEFAULT_PROFILE


def get_target_weights(tickers: list[str] | None = None) -> dict[str, float]:
    """สัดส่วนเป้าหมายของ ticker ที่ระบบติดตาม (รวมเป็น 1.0 เสมอ).

    รายละเอียดกติกาดู docstring ของโมดูล · โยน :class:`InvalidTargetWeights`
    เมื่อ ``portfolio.target_weights`` ผิดรูป (ไม่คืน preset ทับเงียบ ๆ)
    """
    return get_target_weights_with_status(tickers).weights


def get_target_weights_with_status(tickers: list[str] | None = None) -> TargetWeights:
    """เหมือน :func:`get_target_weights` แต่คืนที่มา + คำเตือนมาด้วย."""
    config = load_config()
    profile_name = get_risk_profile()
    preset = RISK_PROFILES[profile_name]

    symbols = [t.strip().upper() for t in (tickers or get_tickers()) if str(t).strip()]
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        return TargetWeights(weights={}, profile=profile_name)

    notes: list[str] = []
    custom, was_percent = _read_custom_weights(config["portfolio"].get("target_weights"))
    if was_percent:
        notes.append(
            "อ่าน portfolio.target_weights เป็นเปอร์เซ็นต์ (ผลรวม = 100) "
            "ถ้าตั้งใจให้เป็นสัดส่วน ให้เขียนเป็น 0–1 แทน"
        )

    outside = sorted(set(custom) - set(symbols))
    if outside:
        notes.append(
            "ตั้งน้ำหนักไว้ให้ " + ", ".join(outside) + " แต่ไม่ได้อยู่ในรายการที่คำนวณรอบนี้ "
            "— ค่าที่ตั้งไม่ถูกใช้"
        )

    configured = {s: custom[s] for s in symbols if s in custom}
    unset = [s for s in symbols if s not in configured]
    assigned = sum(configured.values())

    if not unset:
        weights = _fill_without_unset(configured, assigned, notes)
    else:
        weights = dict(configured)
        weights.update(_share_leftover(unset, preset, max(0.0, 1.0 - assigned), notes))

    adjusted = any(
        abs(weights[s] - configured[s]) > _TOL for s in configured
    )

    source = {
        s: "custom" if s in configured else ("preset" if s in preset else "unknown")
        for s in symbols
    }
    return TargetWeights(
        weights={s: weights[s] for s in symbols},
        profile=profile_name,
        configured=configured,
        source=source,
        notes=notes,
        adjusted=adjusted,
    )


def _read_custom_weights(raw: Any) -> tuple[dict[str, float], bool]:
    """อ่าน ``portfolio.target_weights`` → (สัดส่วน, อ่านเป็นเปอร์เซ็นต์หรือไม่).

    ตรวจทุกค่าแบบดัง ๆ — คีย์ที่มีอยู่คือ "ตั้งแล้ว" เสมอ รวมถึงค่า ``0``
    """
    if raw is None:
        return {}, False
    if not isinstance(raw, dict):
        raise InvalidTargetWeights(
            f"portfolio.target_weights ต้องเป็นอ็อบเจกต์ {{ticker: น้ำหนัก}} "
            f"แต่ได้ {type(raw).__name__} — แก้ที่ config.json"
        )

    parsed: dict[str, float] = {}
    for key, value in raw.items():
        symbol = str(key).strip().upper()
        if not symbol:
            raise InvalidTargetWeights("portfolio.target_weights มีคีย์ว่าง — แก้ที่ config.json")
        if symbol in parsed:
            raise InvalidTargetWeights(
                f"portfolio.target_weights มี {symbol} ซ้ำกัน — แก้ที่ config.json"
            )
        if isinstance(value, bool):
            raise InvalidTargetWeights(
                f"portfolio.target_weights[{symbol}] = {value!r} ไม่ใช่ตัวเลข — "
                "ใส่สัดส่วน 0–1 (เช่น 0.35)"
            )
        try:
            weight = float(value)
        except (TypeError, ValueError):
            raise InvalidTargetWeights(
                f"portfolio.target_weights[{symbol}] = {value!r} ไม่ใช่ตัวเลข — "
                "ใส่สัดส่วน 0–1 (เช่น 0.35)"
            ) from None
        if not math.isfinite(weight):
            raise InvalidTargetWeights(
                f"portfolio.target_weights[{symbol}] = {value!r} ไม่ใช่ตัวเลขที่ใช้ได้ — "
                "ใส่สัดส่วน 0–1 (เช่น 0.35)"
            )
        if weight < 0:
            raise InvalidTargetWeights(
                f"portfolio.target_weights[{symbol}] = {weight:g} ติดลบ — "
                "น้ำหนักเป้าหมายต้องไม่ติดลบ (0 = ตั้งใจไม่ถือ)"
            )
        parsed[symbol] = weight

    if not parsed:
        return {}, False

    total = sum(parsed.values())
    if total <= 1.0 + _TOL:
        return parsed, False
    if abs(total - _PERCENT_TOTAL) <= _PERCENT_TOL:
        return {s: w / _PERCENT_TOTAL for s, w in parsed.items()}, True
    raise InvalidTargetWeights(
        f"portfolio.target_weights รวมกันได้ {total:g} ซึ่งอ่านไม่ออกว่าเป็นหน่วยอะไร — "
        "ใช้สัดส่วนที่รวมกันไม่เกิน 1 (เช่น 0.35) หรือเปอร์เซ็นต์ที่ตั้งครบทุกตัวให้รวมเป็น 100"
    )


def _fill_without_unset(
    configured: dict[str, float], assigned: float, notes: list[str]
) -> dict[str, float]:
    """ตั้งครบทุก ticker แล้ว — ไม่มีใครรับส่วนที่เหลือ ต้องขยาย/ย่อให้รวมเป็น 1."""
    if assigned <= _TOL:
        raise InvalidTargetWeights(
            "portfolio.target_weights ตั้งเป็น 0 ทุก ticker — ไม่มีสัดส่วนให้จัดสรร "
            "ลบคีย์ที่ไม่ต้องการออก หรือกำหนดน้ำหนักให้อย่างน้อยหนึ่งตัว"
        )
    if abs(assigned - 1.0) > _TOL:
        notes.append(
            f"น้ำหนักที่ตั้งไว้รวมกันได้ {assigned * 100:.1f}% และไม่มี ticker ที่เว้นว่างไว้รับส่วนที่เหลือ "
            f"— ระบบปรับตามอัตราส่วนเดิมให้รวมเป็น 100%"
        )
    return {s: w / assigned for s, w in configured.items()}


def _share_leftover(
    unset: list[str], preset: dict[str, float], leftover: float, notes: list[str]
) -> dict[str, float]:
    """แบ่งน้ำหนักที่เหลือให้ ticker ที่ไม่ได้ตั้ง ตามอัตราส่วนของ preset."""
    if leftover <= _TOL:
        notes.append(
            "น้ำหนักที่ตั้งไว้ใช้ครบ 100% แล้ว — " + ", ".join(unset) + " จึงได้ 0% "
            "ถ้าต้องการให้ถือด้วย ให้ลดน้ำหนักตัวอื่นลง"
        )
        return {s: 0.0 for s in unset}

    unknown_base = 1.0 / len(preset) if preset else 1.0
    base = {s: preset.get(s, unknown_base) for s in unset}
    base_total = sum(base.values())
    if base_total <= _TOL:  # pragma: no cover — preset ทุกตัวเป็นบวกเสมอ
        equal = leftover / len(unset)
        return {s: equal for s in unset}
    return {s: base[s] / base_total * leftover for s in unset}
