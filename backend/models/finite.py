# -*- coding: utf-8 -*-
"""``FiniteFloat`` — ชนิดจำนวนเงิน/อัตราที่ปฏิเสธ ``inf``/``NaN`` ตั้งแต่ประตู.

**ทำไมต้องมีชนิดนี้ ไม่ใช้ ``Field(gt=0)`` เฉย ๆ** (K8 · AUDIT_ROUND2_2026-08-07 G8):
``inf > 0`` เป็น ``True`` ⇒ ``inf`` เดินผ่านด่าน pydantic ทุกด่านที่เป็นการเปรียบเทียบ
(``NaN`` ตกที่ ``gt``/``ge`` อยู่แล้วเพราะเทียบกับอะไรก็ ``False`` แต่ได้ข้อความอังกฤษ
ที่บอกเหตุผลผิด) แล้วไหลลึกเข้าไปในโค้ดคำนวณจนไปพังที่จุดซึ่งไม่เกี่ยวกับต้นเหตุ

วัดจริง 2026-09-01 ก่อนไฟล์นี้จะมี — ``POST /api/networth/snapshot`` ด้วย
``value_thb: Infinity``:

1. ผ่าน ``Field(gt=0)``
2. **ถูก commit ลง SQLite** เป็น ``total_assets_thb = inf``
3. แล้วค่อยพังตอน serialize เป็น JSON ⇒ ผู้ใช้เห็น HTTP 500 (ดูเหมือนบันทึกไม่สำเร็จ)
4. หลังจากนั้น ``GET /api/networth/history`` **โยน exception ทุกครั้ง** เพราะแถวพิษ
   ยังอยู่ ⇒ คำขอผิดครั้งเดียวทำให้ประวัติมูลค่าสุทธิพังถาวร จนกว่าจะเข้าไปลบแถวใน
   ฐานข้อมูลเอง ซึ่งผู้ใช้ทำจากหน้าจอไม่ได้

จึงต้องกันที่ประตู ไม่ใช่ไปดักตอนคำนวณหรือตอนแสดงผล — และต้องเป็น**นิยามเดียว**
ของทั้งระบบ ก่อนหน้านี้แนวคิดนี้ถูกเขียนสองที่ที่ไม่รู้จักกัน (``debt_models`` มี
``FiniteFloat`` ส่วน ``schemas`` มี ``_finite_amount``) แล้วโมเดลอีกสามไฟล์ไม่มีเลย

**ข้อจำกัดที่ต้องรู้: ชนิดนี้กันได้แค่ "ค่าที่รับเข้ามา"** ผลลัพธ์ที่ **ล้น** จากอินพุต
ที่จำกัดค่าดี ๆ (``1e308 × 3.5 = inf``) ชนิดนี้กันไม่ได้ ผู้คำนวณต้องตรวจผลของตัวเอง
ด้วย :func:`ensure_finite_result`
"""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import BeforeValidator, ValidationInfo

#: ชื่อไทยของฟิลด์ที่ใช้ประกอบข้อความ error — ฟิลด์ที่ไม่มีในนี้ใช้ชื่อฟิลด์ตรง ๆ
#: (ลงทะเบียนเพิ่มด้วย :func:`register_field_labels` จากไฟล์โมเดลที่เป็นเจ้าของฟิลด์)
_FIELD_LABELS: dict[str, str] = {}

#: สตริงที่ ``float()`` แปลงเป็น inf/NaN ได้ (รวมรูปที่ผู้ใช้พิมพ์มาเองด้วย)
_NON_FINITE_TOKENS = {"inf", "-inf", "+inf", "infinity", "-infinity", "+infinity", "nan", "-nan"}


def register_field_labels(labels: dict[str, str]) -> None:
    """ลงทะเบียนชื่อไทยของฟิลด์ — ไฟล์โมเดลเรียกตอน import."""
    _FIELD_LABELS.update(labels)


def _to_json_safe(value: Any) -> Any:
    """ด่านที่ 1 — แปลง inf/NaN เป็นสตริงก่อนที่ pydantic จะจดค่านี้ลง error.

    FastAPI ใส่ ``input`` (ค่าที่ผู้ใช้ส่งมา) ลงใน response 422 ด้วย และ ``json.dumps``
    ของ starlette ตั้ง ``allow_nan=False`` → ถ้าปล่อย ``inf`` ติดไปกับ error ทั้งคำขอจะ
    กลายเป็น 500 ที่ไม่มีเนื้อความแทนที่จะเป็น 422 ที่บอกสาเหตุ
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value  # ไม่ใช่ตัวเลข — ปล่อยให้ pydantic รายงาน type error ตามปกติ
    if math.isfinite(number):
        return value
    return repr(number)  # 'inf' | '-inf' | 'nan'


def _reject_non_finite(value: Any, info: ValidationInfo) -> Any:
    """ด่านที่ 2 — ปฏิเสธค่าที่ด่านแรกทำเครื่องหมายไว้ พร้อมเหตุผลภาษาไทย."""
    if isinstance(value, str) and value.strip().lower() in _NON_FINITE_TOKENS:
        label = _FIELD_LABELS.get(info.field_name or "", info.field_name or "ค่านี้")
        raise ValueError(
            f"{label} ต้องเป็นตัวเลขจริงที่มีค่าจำกัด (ได้รับ {value}) — "
            "inf/NaN ไม่ใช่จำนวนเงินหรืออัตราที่ใช้คำนวณได้"
        )
    return value


# BeforeValidator ใน Annotated ทำงานจากขวาไปซ้าย: _to_json_safe ก่อน แล้วค่อย _reject_non_finite
FiniteFloat = Annotated[
    float,
    BeforeValidator(_reject_non_finite),
    BeforeValidator(_to_json_safe),
]


def ensure_finite_input(value: float, label: str) -> float:
    """ตรวจค่าที่ **รับเข้ามา** ว่าเป็นจำนวนจำกัด — ไม่ใช่ก็ ``ValueError``.

    รูปแบบฟังก์ชัน (ไม่ใช่ชนิด) มีไว้เรียกจากใน ``field_validator`` สำหรับฟิลด์ที่
    ประกาศชนิดเป็น :data:`FiniteFloat` ไม่ได้ — ผลลัพธ์เหมือนกันทุกประการ

    **ข้อความต่างจาก :func:`ensure_finite_result` โดยตั้งใจ ห้ามยุบรวม**: ที่นี่คือ
    "ค่าที่คุณส่งมาใช้ไม่ได้" (ผู้ใช้แก้ด้วยการกรอกใหม่) ส่วนอีกตัวคือ "ค่าที่เราคำนวณได้
    ล้นช่วง" (ผู้ใช้แก้ด้วยการลดขนาดตัวเลข) — คนละอาการ คนละวิธีแก้ และสัญญาของ API
    ก็ตรึงข้อความนี้ไว้แล้วว่าต้องเอ่ยถึงทั้ง ``inf`` และ ``NaN``
    """
    if not math.isfinite(value):
        raise ValueError(f"{label} ต้องเป็นตัวเลขจำกัด ไม่ใช่ inf หรือ NaN (ได้ {value})")
    return value


def ensure_finite_result(value: float, label: str) -> float:
    """ตรวจ**ผลลัพธ์**ที่คำนวณเสร็จแล้วว่ายังเป็นจำนวนจำกัด — ไม่ใช่ก็ ``ValueError``.

    ต่างจาก :data:`FiniteFloat` ซึ่งกันที่ประตู: ฟังก์ชันนี้กันกรณีที่อินพุต**ถูกต้อง
    ทุกช่อง**แต่ผลคูณ/ผลหารล้นจนกลายเป็น ``inf`` เช่น ``1e308 × 3.5`` หรือ
    ``gap / 1e-300`` — วัดจริงแล้วทำให้ ``/api/emergency-fund/calculate`` ตอบ 500
    ทั้งที่ผู้ใช้กรอกตัวเลขที่ผ่านทุกด่านของ schema

    เป็น ``ValueError`` เพราะต้นเหตุคือ**ตัวเลขที่ผู้ใช้กรอก** ผู้เรียกฝั่ง router
    ต้องแปลงเป็น 4xx ไม่ใช่ 500 (ความผิดของคำขอ ห้ามรายงานว่าเซิร์ฟเวอร์พัง)
    """
    if not math.isfinite(value):
        raise ValueError(
            f"{label} คำนวณออกมาเป็น {value} (เกินช่วงที่คำนวณได้) — "
            "ตัวเลขที่กรอกใหญ่หรือเล็กเกินไป กรุณาใช้ค่าที่สมจริง"
        )
    return value
