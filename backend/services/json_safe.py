# -*- coding: utf-8 -*-
"""แปลงผลลัพธ์ pandas ให้ JSON serialize ได้ โดยไม่กลืนความหมายของ "ไม่มีข้อมูล".

สองบั๊กที่โมดูลนี้ปิด (AUDIT.md M16, M18):

- **M16** `NaN` หลุดเข้า ``JSONResponse`` → ``ValueError: Out of range float values are
  not JSON compliant`` → **ทั้ง endpoint คืน 500** ทั้งที่มีแค่บางช่องที่ไม่มีข้อมูล
- **M18** ``DataFrame.reset_index()`` พ่วง ``pd.Timestamp`` ติดมาใน records →
  ``TypeError: Object of type Timestamp is not JSON serializable``

หลักที่ยึด (AUDIT.md C1): ช่องที่ไม่มีข้อมูลต้องเป็น ``null`` ให้ผู้ใช้เห็นว่า
"ไม่รู้" — **ห้ามแปลงเป็น 0** เพราะ 0 คือตัวเลขที่ตีความเป็นผลตอบแทน/ราคาได้
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any

import pandas as pd


def json_safe(value: Any) -> Any:
    """คืนค่าที่ ``json.dumps`` รับได้ — NaN/NaT → ``None``, วันที่ → ISO string."""
    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (pd.Timestamp, _dt.datetime)):
        return None if pd.isna(value) else pd.Timestamp(value).isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]

    # numpy scalar / pd.NA / NaT ที่เล็ดลอดมา
    if value is pd.NaT or value is pd.NA:
        return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return json_safe(item())
        except (ValueError, TypeError):
            return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def frame_to_records(df: pd.DataFrame, *, reset_index: bool = True) -> list[dict[str, Any]]:
    """DataFrame → list[dict] ที่ JSON serialize ได้ (index กลายเป็นคอลัมน์ปกติ)."""
    if df is None or df.empty:
        return []
    frame = df.reset_index() if reset_index else df
    return [json_safe(row) for row in frame.to_dict(orient="records")]


def frame_to_dict(df: pd.DataFrame) -> dict[str, Any]:
    """DataFrame → dict ซ้อน (``{คอลัมน์: {index: ค่า}}``) ที่ JSON serialize ได้."""
    if df is None or df.empty:
        return {}
    return json_safe(df.to_dict())
