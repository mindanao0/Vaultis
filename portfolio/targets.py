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
   — ข้อนี้เป็นจริงเมื่อคำนวณบน **จักรวาลเต็ม** (``partial=False`` ซึ่งเป็นค่าเริ่มต้น)
   โหมด ``partial=True`` ตั้งใจให้ต่างออกไป ดูหัวข้อ "รายชื่อที่ส่งเข้ามาเป็นชุดย่อยได้"
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

รายชื่อที่ส่งเข้ามาเป็น "ชุดย่อย" ได้ (AUDIT_ROUND2_2026-08-07 G1)
-----------------------------------------------------------------
``calculate_allocation()`` ส่งเฉพาะ ticker ที่ **ดึงราคาสำเร็จ** เข้ามาตามนโยบาย DCA
ถ้าวันไหนตัวที่ถือน้ำหนักดึงราคาไม่ได้ เหลือแต่ตัวที่ผู้ใช้ตั้ง ``0`` ไว้ตั้งใจ ชุดย่อยนั้น
จะหน้าตาเหมือน "ตั้งครบทุกตัวและรวมได้ 0" ทั้งที่ config.json ถูกต้องทุกบรรทัด

ผู้เรียกที่กรองรายชื่อมาแล้วต้องบอกด้วย ``partial=True`` แล้วโมดูลนี้จะแยกสาเหตุให้:

* :class:`InvalidTargetWeights` — **คอนฟิกผิดจริง** แก้ที่ ``config.json`` แล้วหาย
* :class:`NoTargetForSubset` — คอนฟิกถูก แต่ ticker ที่ถือน้ำหนักไม่ได้อยู่ในรอบนี้
  (เช่น ดึงราคาไม่สำเร็จ) — รอบหน้าที่ข้อมูลครบจะจัดสรรได้เอง ห้ามชวนผู้ใช้ไปแก้คอนฟิก
* ทั้งคู่สืบจาก :class:`TargetWeightsError` สำหรับผู้เรียกที่ไม่ต้องแยกสาเหตุ

``partial`` เปลี่ยนสองอย่าง (AUDIT_ROUND2_2026-08-07 G1 + T7):

1. **รายงานสาเหตุ** — แยก ``NoTargetForSubset`` ออกจาก ``InvalidTargetWeights`` ตามด้านบน
2. **ฐานที่ใช้คิดน้ำหนัก** — คิดบน "จักรวาลเต็ม" (ticker ที่ระบบติดตาม) ก่อน แล้วค่อย
   ตัดลงเหลือรอบนี้และ normalize ใหม่ ไม่ใช่คิดบนชุดย่อยตรง ๆ

ข้อ 2 จำเป็นเพราะกฎข้อ 2 ด้านบน ("ตัวที่ไม่ได้ตั้งแบ่งส่วนที่เหลือกันเอง") จะตีความ
"น้ำหนักของกองที่ดึงราคาไม่สำเร็จ" เป็น "ส่วนที่เหลือที่ยังไม่มีเจ้าของ" แล้วยกให้
ticker ที่ผู้ใช้ตั้งใจไม่ถือ — ตั้ง ``{VOO: .4, SCHD: .3, QQQM: .3}`` ไว้ครบ 100%
วันที่ SCHD ดึงราคาไม่สำเร็จ แผน DCA จะไปซื้อ XLV/GLDM ที่ตั้งใจไม่ถือ
คิดบนจักรวาลเต็มก่อนแล้วค่อยตัด จึงได้ VOO 57.1% / QQQM 42.9% ตามอัตราส่วนเดิม
(ดู :func:`_restrict_to_round`)

**สิ่งที่ ``partial=True`` รับประกัน / ไม่รับประกัน** (AUDIT_ROUND2_2026-08-07 — เขียนไว้ตรงนี้
เพราะกฎข้อ 1 ด้านบนเคยถูกอ่านว่า "ค่าที่ตั้งเองไม่มีวันถูกแตะ" ซึ่งไม่จริงอีกแล้วในโหมดนี้
และ docstring ที่ขัดกับโค้ดคือทางที่บั๊กเดิมจะถูกใส่กลับเข้ามา):

* **ไม่รับประกัน** ว่าตัวเลขของ ticker ที่ตั้งเองจะเท่ากับที่ตั้งไว้ — ผลรวมของรอบต้องเป็น
  1.0 เสมอ ในเมื่อกองที่หายไปพาน้ำหนักของมันออกไปด้วย ที่เหลือจึงถูกขยาย
  (``{VOO: .4, SCHD: .3, QQQM: .3}`` เมื่อ SCHD หาย → VOO = 0.4/0.7 = 57.1% ไม่ใช่ 40%)
* **รับประกัน** อัตราส่วนระหว่าง ticker ที่อยู่ในรอบเท่าเดิม (VOO:QQQM = 4:3 ทั้งก่อนและหลัง)
* **รับประกัน** ว่า ``0`` ยังเป็น ``0`` — เจตนา "ตั้งใจไม่ถือ" ห้ามถูกเจือจางหายไป
  ไม่ว่ากองไหนจะดึงราคาไม่สำเร็จ
* ``adjusted=True`` + ``notes`` ภาษาไทยจะติดมาด้วยเสมอเมื่อการขยายนี้เกิดขึ้น เพื่อให้
  หน้าจอบอกผู้ใช้ได้ว่าตัวเลขที่เห็นมาจากรอบที่ข้อมูลไม่ครบ ไม่ใช่เพราะคอนฟิกผิด

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


class TargetWeightsError(ValueError):
    """ฐานของทุกข้อผิดพลาดเรื่องสัดส่วนเป้าหมาย — จับตัวนี้เมื่อไม่ต้องแยกสาเหตุ."""


class InvalidTargetWeights(TargetWeightsError):
    """``portfolio.target_weights`` ใน config.json ผิดรูป — ห้ามเดาแทนผู้ใช้.

    หมายถึง **คอนฟิกผิดจริง ๆ** เท่านั้น: แก้ที่ ``config.json`` แล้วอาการหาย
    ห้ามใช้ตัวนี้กับกรณีข้อมูลราคาไม่พร้อม (ดู :class:`NoTargetForSubset`)
    """


class NoTargetForSubset(TargetWeightsError):
    """คอนฟิกถูกต้อง แต่ ticker ที่ถือน้ำหนัก **ไม่ได้อยู่ในรอบคำนวณนี้**.

    เกิดเมื่อผู้เรียกส่งชุดย่อยที่กรองมาแล้ว (``partial=True`` — เช่นเหลือเฉพาะตัวที่
    ดึงราคาสำเร็จ) แล้วตัวที่เหลือถูกตั้งเป้าไว้ ``0`` ทั้งหมด
    **ไม่ใช่ปัญหาที่ config.json** รอบหน้าที่ข้อมูลครบจะจัดสรรได้ตามปกติ

    ``requested``  ticker ที่คำนวณได้รอบนี้ (ทุกตัวเป้าเป็น 0)
    ``missing``    ticker ที่ถือน้ำหนักอยู่แต่ไม่ได้อยู่ในรอบนี้
    """

    def __init__(self, message: str, *, requested: list[str], missing: list[str]) -> None:
        super().__init__(message)
        self.requested = list(requested)
        self.missing = list(missing)


@dataclass(frozen=True)
class TargetWeights:
    """น้ำหนักเป้าหมายพร้อมที่มาและคำเตือน.

    ``weights``     สัดส่วนที่ใช้จริง รวมเป็น 1.0 เสมอ
    ``profile``     risk profile ที่ใช้เป็นฐานของ ticker ที่ไม่ได้ตั้ง
    ``configured``  ค่าที่ผู้ใช้ตั้งไว้ (แปลงเป็นสัดส่วนแล้ว) เฉพาะ ticker ในรายการนี้
    ``source``      symbol → ``custom`` (ผู้ใช้ตั้ง) / ``preset`` / ``unknown`` (preset ไม่รู้จัก)
    ``notes``       ข้อความไทยสำหรับหน้า Settings — ว่างเมื่อทุกอย่างตรงตามที่ตั้ง
    ``adjusted``    ``True`` เมื่อค่าที่ใช้จริงของ ticker ที่ "ตั้งไว้" ต่างจากที่ตั้ง

    ``adjusted=True`` **ไม่ได้แปลว่าคอนฟิกผิด** — ในโหมด ``partial=True`` ที่มีกองหายไป
    จากรอบนี้ (เช่นดึงราคาไม่สำเร็จ) ค่าที่ตั้งไว้จะถูกขยายให้รวมเป็น 100% เป็นปกติ
    ผู้เรียกที่อยากรู้ "ทำไม" ต้องอ่าน ``notes`` ไม่ใช่เดาจากธงตัวนี้
    (AUDIT_ROUND2_2026-08-07)
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


def get_target_weights(
    tickers: list[str] | None = None, *, partial: bool = False
) -> dict[str, float]:
    """สัดส่วนเป้าหมายของ ticker ที่ระบบติดตาม (รวมเป็น 1.0 เสมอ).

    รายละเอียดกติกาดู docstring ของโมดูล · โยน :class:`InvalidTargetWeights`
    เมื่อ ``portfolio.target_weights`` ผิดรูป (ไม่คืน preset ทับเงียบ ๆ)

    ``partial=True`` = ``tickers`` ถูกกรองมาแล้ว (เช่นเหลือเฉพาะตัวที่ดึงราคาสำเร็จ)
    ไม่ใช่จักรวาลที่ผู้ใช้ตั้งไว้ทั้งหมด → ถ้าไม่เหลือน้ำหนักให้จัดสรรจะโยน
    :class:`NoTargetForSubset` แทน :class:`InvalidTargetWeights` เพราะคอนฟิกไม่ได้ผิด
    และ **ค่าที่ผู้ใช้ตั้งเองจะถูกขยายให้รอบนี้รวมเป็น 100%** (อัตราส่วนเดิม, ``0`` ยังเป็น
    ``0``) — รายละเอียดว่ารับประกันอะไรบ้างอยู่ใน docstring ของโมดูล
    """
    return get_target_weights_with_status(tickers, partial=partial).weights


def get_target_weights_with_status(
    tickers: list[str] | None = None, *, partial: bool = False
) -> TargetWeights:
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

    # ``partial`` = ``symbols`` เป็นชุดย่อยที่กรองมาแล้ว ไม่ใช่จักรวาลที่ผู้ใช้ตั้งไว้
    # จึงต้องคิดน้ำหนักบน **จักรวาลเต็ม** ก่อน แล้วค่อยตัดลงเหลือรอบนี้ + normalize ใหม่
    # (AUDIT_ROUND2_2026-08-07 T7 — เหตุผลเต็มอยู่ที่ :func:`_restrict_to_round`)
    universe = _full_universe(symbols) if partial else symbols

    if not partial:
        outside = sorted(set(custom) - set(symbols))
        if outside:
            notes.append(
                "ตั้งน้ำหนักไว้ให้ " + ", ".join(outside) + " แต่ไม่ได้อยู่ในรายการที่คำนวณรอบนี้ "
                "— ค่าที่ตั้งไม่ถูกใช้"
            )
    # โหมด partial: หมายเหตุเรื่อง ticker ที่ไม่ได้อยู่ในรอบนี้ ออกที่ ``_restrict_to_round``
    # ที่เดียว (ตอนนั้นถึงจะรู้ว่าน้ำหนักหายไปเท่าไรจริง ๆ) — ไม่งั้นได้หมายเหตุซ้ำสองรอบ

    configured = {s: custom[s] for s in universe if s in custom}
    unset = [s for s in universe if s not in configured]
    assigned = sum(configured.values())

    if not unset:
        # ระดับจักรวาลเต็ม: "น้ำหนักไปกองอยู่นอกจักรวาล" = คีย์ค้างใน config.json เสมอ
        # (คนละเรื่องกับ "ticker ที่ถือน้ำหนักดึงราคาไม่สำเร็จรอบนี้" ซึ่งยังอยู่ในจักรวาล)
        weights = _fill_without_unset(
            configured,
            assigned,
            notes,
            requested=universe,
            missing=_weight_bearing_outside(custom, universe),
        )
    else:
        weights = dict(configured)
        weights.update(_share_leftover(unset, preset, max(0.0, 1.0 - assigned), notes))

    if partial:
        weights = _restrict_to_round(weights, symbols, custom, notes)

    in_round = {s: custom[s] for s in symbols if s in custom}
    adjusted = any(abs(weights[s] - in_round[s]) > _TOL for s in in_round)

    source = {
        s: "custom" if s in in_round else ("preset" if s in preset else "unknown")
        for s in symbols
    }
    return TargetWeights(
        weights={s: weights[s] for s in symbols},
        profile=profile_name,
        configured=in_round,
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


def _full_universe(symbols: list[str]) -> list[str]:
    """จักรวาลเต็มของรอบคำนวณแบบ ``partial``: ticker ที่ระบบติดตาม + ตัวที่ผู้เรียกส่งมา.

    ต้องเป็น superset ของ ``symbols`` เสมอ เพราะน้ำหนักถูกคิดบนจักรวาลนี้ก่อนตัดลง
    ticker ที่ผู้เรียกส่งมาแต่ไม่ได้ถูกติดตาม (เช่นหน้าจอเฉพาะกิจ) ยังต้องได้น้ำหนัก
    """
    universe = [str(t).strip().upper() for t in get_tickers() if str(t).strip()]
    universe += [s for s in symbols if s not in universe]
    return list(dict.fromkeys(universe))


def _restrict_to_round(
    weights: dict[str, float], symbols: list[str], custom: dict[str, float], notes: list[str]
) -> dict[str, float]:
    """ตัดน้ำหนักของจักรวาลเต็มลงเหลือเฉพาะ ticker ที่คำนวณได้รอบนี้ แล้ว normalize ใหม่.

    ทำไมต้องคิดบนจักรวาลเต็มก่อน (AUDIT_ROUND2_2026-08-07 T7): ถ้าคิดบนชุดย่อยตรง ๆ
    ส่วนที่เหลือ (``1 - ผลรวมที่ตั้งไว้``) จะถูกแบ่งให้ ticker ที่ **ผู้ใช้ตั้งใจไม่ถือ**
    ตัวอย่างจริง — ติดตาม 5 กอง ตั้ง ``{VOO: .4, SCHD: .3, QQQM: .3}`` (XLV/GLDM
    จึงได้ 0% เพราะน้ำหนักถูกใช้ครบ 100%) วันที่ SCHD ดึงราคาไม่สำเร็จ ชุดย่อยจะเหลือ
    ``[VOO, QQQM, XLV, GLDM]`` ซึ่งมีน้ำหนักที่ตั้งไว้แค่ 70% ⇒ อีก 30% ของ SCHD
    ถูกยกให้ XLV/GLDM ตาม preset = **แผน DCA ไปซื้อกองที่ผู้ใช้เลือกจะไม่ถือ เพราะ
    yfinance ล่มชั่วคราว** คิดบนจักรวาลเต็มก่อนแล้วค่อยตัด จะได้ VOO 57.1% / QQQM 42.9%
    ตามอัตราส่วนเดิมและ XLV/GLDM ยังเป็น 0% ตามเจตนา

    ไม่เหลือน้ำหนักในรอบนี้เลย → :class:`NoTargetForSubset` (คอนฟิกไม่ได้ผิด)
    """
    subset = {s: weights[s] for s in symbols}
    total = sum(subset.values())
    missing = _weight_bearing_outside(custom, symbols)

    if total <= _TOL:
        raise _no_weight_left(sorted(symbols), missing, True)

    if missing and 1.0 - total > _TOL:
        notes.append(
            "รอบนี้ไม่ได้คิด " + ", ".join(missing) + " (เช่น ดึงราคาไม่สำเร็จ) "
            "— น้ำหนักของมันถูกกระจายให้ตัวที่มีข้อมูลตามอัตราส่วนเดิม ไม่ใช่ปัญหาที่ config.json"
        )
    return {s: w / total for s, w in subset.items()}


def _weight_bearing_outside(custom: dict[str, float], symbols: list[str]) -> list[str]:
    """ticker ที่ "ถือน้ำหนักอยู่" แต่ไม่ได้อยู่ในรายการที่คำนวณรอบนี้.

    รวมสองแบบ: ตั้งไว้เองมากกว่า 0 · และตัวที่ระบบติดตามแต่ไม่ได้ตั้ง ซึ่งจะได้ส่วนที่เหลือ
    จาก preset (นับเฉพาะเมื่อยังมีส่วนที่เหลือให้แบ่งจริง ๆ)
    """
    outside = [s for s in custom if s not in symbols and custom[s] > _TOL]
    if 1.0 - sum(custom.values()) > _TOL:
        tracked = [str(t).strip().upper() for t in get_tickers() if str(t).strip()]
        outside += [t for t in tracked if t not in symbols and t not in custom]
    return sorted(dict.fromkeys(outside))


def _no_weight_left(requested: list[str], missing: list[str], partial: bool) -> TargetWeightsError:
    """ไม่เหลือน้ำหนักให้จัดสรร — แยกให้ออกว่า "คอนฟิกผิด" หรือ "ข้อมูลไม่ครบรอบนี้".

    AUDIT_ROUND2_2026-08-07 G1: เดิมทุกเส้นทางลงท้ายที่ข้อความเดียวคือ
    "ตั้งเป็น 0 ทุก ticker" ⇒ yfinance ล่มชั่วคราวถูกรายงานเป็นคอนฟิกผิด และถ้าผู้ใช้
    ทำตามคำแนะนำ (ลบคีย์ที่ตั้ง 0) ก็จะลบเจตนา "ตั้งใจไม่ถือตัวนี้" ทิ้งไปด้วย
    """
    listed = ", ".join(requested)
    if missing:
        elsewhere = ", ".join(missing)
        if partial:
            return NoTargetForSubset(
                f"ticker ที่คำนวณได้รอบนี้ ({listed}) ถูกตั้งเป้าหมายไว้ 0% ทั้งหมด "
                f"ส่วนตัวที่ถือน้ำหนักอยู่ ({elsewhere}) ไม่ได้อยู่ในรอบนี้ (เช่น ดึงราคาไม่สำเร็จ) "
                "— สัดส่วนใน config.json ไม่ได้ผิด รอบที่ข้อมูลครบจะจัดสรรได้ตามปกติ",
                requested=requested,
                missing=missing,
            )
        return InvalidTargetWeights(
            f"ticker ที่คำนวณรอบนี้ ({listed}) ถูกตั้งเป้าหมายไว้ 0% ทั้งหมด "
            f"น้ำหนักที่เหลือไปกองอยู่ที่ {elsewhere} ซึ่งไม่ได้อยู่ในรายการที่คำนวณ "
            "— ย้ายน้ำหนักมาที่ ticker ที่ติดตาม หรือเพิ่มตัวนั้นเข้า etf.tickers ใน config.json"
        )
    return InvalidTargetWeights(
        "portfolio.target_weights ตั้งเป็น 0 ทุก ticker — ไม่มีสัดส่วนให้จัดสรร "
        "ลบคีย์ที่ไม่ต้องการออก หรือกำหนดน้ำหนักให้อย่างน้อยหนึ่งตัว"
    )


def _fill_without_unset(
    configured: dict[str, float],
    assigned: float,
    notes: list[str],
    *,
    requested: list[str] | None = None,
    missing: list[str] | None = None,
) -> dict[str, float]:
    """ตั้งครบทุก ticker แล้ว — ไม่มีใครรับส่วนที่เหลือ ต้องขยาย/ย่อให้รวมเป็น 1.

    ฟังก์ชันนี้ทำงานบน **จักรวาลเต็ม** เสมอ (ดู ``get_target_weights_with_status``)
    ``missing`` จึงหมายถึง ticker ที่ถือน้ำหนักแต่อยู่ *นอกจักรวาล* = คีย์ค้างใน
    config.json ซึ่งเป็นคอนฟิกผิดจริง — กรณี "อยู่ในจักรวาลแต่ไม่ได้อยู่ในรอบนี้"
    (ดึงราคาไม่สำเร็จ) ตัดสินที่ :func:`_restrict_to_round` แทน
    """
    listed = sorted(requested if requested is not None else configured)
    absent = list(missing or [])
    if assigned <= _TOL:
        raise _no_weight_left(listed, absent, False)
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
