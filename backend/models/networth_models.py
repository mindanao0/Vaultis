"""Pydantic models for the Net Worth Tracker."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .finite import FiniteFloat, register_field_labels

register_field_labels({"value_thb": "มูลค่า (บาท)"})

AssetType = Literal["cash", "etf", "fund", "bond", "อื่นๆ"]

# ที่มาของมูลค่า ETF ในคำตอบหนึ่ง ๆ — ``etf_live`` ตัวเดิมเป็น bool จึงแยก
# "สดครบทุกตัว" ออกจาก "สดบางตัว" ไม่ได้ และเป็น True ทันทีที่มี ETF ตัวใดตัวหนึ่ง
# มีราคา ทั้งที่ตัวที่เหลือหายไปจากยอดรวมแล้ว (AUDIT_2026-08-06 H11)
#   live             = holdings ทุกตัวมีราคาจริง
#   partial          = บางตัวมีราคา บางตัวดึงไม่ได้ → ยอดรวม "ขาด" ดู ``missing_prices``
#   from_snapshot    = ราคาสดใช้ไม่ได้/สมุดไม่มี ETF จึงใช้มูลค่า ETF จาก snapshot ล่าสุด
#   unavailable      = ดึงราคาไม่ได้เลย และไม่มี snapshot ให้ถอย → ยอดไม่มี ETF เลย
#   fx_unavailable   = **ราคาดึงมาได้ แต่ไม่มีอัตราแลกเปลี่ยนให้แปลงเป็นบาท** และไม่มี
#                      snapshot ให้ถอย → ยอดไม่มี ETF เลย  แยกจาก ``unavailable``
#                      เพราะ "ดึงราคาไม่ได้" กับ "แปลงเป็นบาทไม่ได้ (FX ล่ม/ค่าสำรองผิด)"
#                      คนละสาเหตุ คนละวิธีแก้ — ``missing_prices`` ต้อง **ว่าง** ในเคสนี้
#                      รายละเอียดอยู่ที่ ``fx_error`` (G3)
#   ledger_unreadable= สมุดมีธุรกรรมอยู่ แต่ tracker ตัดทิ้งหมดจนไม่เหลือ holding
#                      → **บอกไม่ได้ว่ามี ETF หรือไม่** (คนละเรื่องกับ ``no_holdings``)
#   no_holdings      = สมุดไม่มี ETF จริง ๆ และไม่มีแถวไหนถูกตัดทิ้ง
#
# ``etf_status`` บอก "ตัวเลข ETF ในคำตอบนี้มาจากไหน" ส่วน "สมุดอ่านได้ครบไหม"
# อยู่ที่ ``skipped_rows``/``skipped_reason`` เสมอ — ต้องอ่านทั้งคู่
EtfStatus = Literal[
    "live",
    "partial",
    "from_snapshot",
    "unavailable",
    "fx_unavailable",
    "ledger_unreadable",
    "no_holdings",
]

# อายุของ snapshot ที่เอา "สินทรัพย์นอก ETF + หนี้สิน" มาใช้
#   fresh          = อายุ ≤ เกณฑ์ (``snapshot_stale=False``)
#   stale          = อายุเกินเกณฑ์ (``snapshot_stale=True``)
#   no_snapshot    = ยังไม่เคยบันทึก snapshot → ไม่มีอะไรให้วัดอายุ
#   unreadable_date= วันที่ในฐานอ่านไม่ออก
#   future_date    = วันที่ในฐานอยู่ในอนาคต (แถวเก่าก่อนมี validation) → อายุติดลบไม่ใช่ข้อมูล
# สามค่าหลังคือ "บอกไม่ได้" ⇒ ``snapshot_stale is None`` ห้ามเป็น ``False``
# เพราะผู้บริโภคอ่าน ``False`` ว่า "ยังใหม่"
SnapshotAgeStatus = Literal["fresh", "stale", "no_snapshot", "unreadable_date", "future_date"]


class Asset(BaseModel):
    name: str
    type: AssetType
    # ``inf`` เดินผ่าน ``Field(gt=0)`` ได้ แล้ว **ถูก commit ลง SQLite** ก่อนจะพังตอน
    # serialize ⇒ ผู้ใช้เห็น 500 (เหมือนบันทึกไม่สำเร็จ) แต่แถวพิษยังอยู่ และทำให้
    # ``GET /api/networth/history`` โยน exception ทุกครั้งหลังจากนั้น = ประวัติพังถาวร
    # จนกว่าจะเข้าไปลบแถวในฐานเอง (วัดจริง 2026-09-01) — ต้องกันที่ประตูเท่านั้น
    value_thb: FiniteFloat = Field(gt=0)


class Liability(BaseModel):
    name: str
    value_thb: FiniteFloat = Field(gt=0)


class SnapshotRequest(BaseModel):
    """ก้อนที่ผู้ใช้สั่งบันทึก — ``snapshot_date`` ถูกตรวจที่ชั้นนี้ที่เดียว.

    เดิมเป็น ``str`` ที่ไม่เคยถูกตรวจเลย: ``"2099-01-01"`` ได้ 201 แล้วไปโผล่เป็น
    ``snapshot_age_days = -26445`` กับ ``snapshot_stale = False`` (= "ยังใหม่")
    และแถวนั้นจะขึ้นหัวประวัติตลอดไปเพราะเรียงตามวันที่ ส่วน ``"banana"`` ลงฐาน
    ได้เหมือนกันแล้วอายุกลายเป็น "ไม่รู้" ถาวร — ต้องปฏิเสธตั้งแต่ประตู
    """

    assets: list[Asset]
    liabilities: list[Liability] = []
    snapshot_date: str | None = None  # YYYY-MM-DD; defaults to today if omitted

    @field_validator("snapshot_date")
    @classmethod
    def _check_snapshot_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None  # ว่าง = ไม่ได้ระบุ → ใช้วันนี้ (พฤติกรรมเดิม)
        try:
            parsed = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                f"snapshot_date ต้องเป็นวันที่รูปแบบ YYYY-MM-DD (ได้ {text!r})"
            ) from exc
        today = date.today()
        if parsed > today:
            raise ValueError(
                f"snapshot_date ต้องไม่เป็นวันในอนาคต (ได้ {parsed.isoformat()}, "
                f"วันนี้ {today.isoformat()})"
            )
        return parsed.isoformat()  # normalize ให้เป็น YYYY-MM-DD เสมอ


class NetWorthResponse(BaseModel):
    """Net worth หนึ่งก้อน **พร้อมช่องบอกว่าอะไรขาดหายไปจากยอดนี้บ้าง**.

    การข้ามสินทรัพย์ที่ดึงราคาไม่ได้โดยไม่รายงาน ให้ผลตัวเลขเหมือนกับการนับเป็น 0
    ทุกประการ เพราะมันหายจากตัวตั้งของ ``total_assets_thb``/``net_worth_thb``
    — ฟิลด์รายงานด้านล่างจึงเป็นส่วนหนึ่งของคำตอบ ไม่ใช่ของแถม (AUDIT_2026-08-06 H11)

    ชื่อคีย์ ``missing_prices``/``skipped_rows``/``skipped_reason`` ใช้ชุดเดียวกับ
    ``/api/portfolio`` — ห้ามตั้งชื่อใหม่
    """

    snapshot_date: str  # วันที่ของตัวเลขก้อนนี้ (ของ /current = วันนี้)
    assets: list[Asset]
    liabilities: list[Liability]
    total_assets_thb: float
    total_liabilities_thb: float
    net_worth_thb: float

    # เดิมคือ "True when ETF values are from live prices" แต่เป็นจริงทั้งที่บางตัวหาย
    # ตอนนี้ True เฉพาะเมื่อ ``etf_status == "live"`` (คงไว้เพื่อผู้เรียกเก่า)
    etf_live: bool = False
    etf_status: EtfStatus = "from_snapshot"

    # ETF ที่มีในสมุดแต่ดึงราคาปัจจุบันไม่ได้ → **ไม่ได้อยู่ในยอดรวมข้างบน**
    missing_prices: list[str] = Field(default_factory=list)
    # ธุรกรรมที่ข้อมูลไม่ครบจน tracker ตัดออกก่อนคิดมูลค่า
    skipped_rows: list[dict[str, Any]] = Field(default_factory=list)
    skipped_reason: str = ""

    # ที่มาของอัตราแลกเปลี่ยนที่ใช้แปลง ETF เป็นบาท (``fx_is_live=False``
    # = ค่าสำรองจาก config ตัวเลขบาทอาจคลาดเคลื่อน) — None เมื่อไม่ได้ใช้ FX เลย
    #
    # สี่สถานะที่ห้ามยุบรวมกัน (G3) — "ไม่ได้ใช้" ≠ "ใช้ไม่ได้" ≠ "ใช้ค่าสำรอง":
    #   ค่าสด            fx_rate=ตัวเลข  fx_is_live=True   fx_error=None
    #   ค่าสำรอง          fx_rate=ตัวเลข  fx_is_live=False  fx_error=None
    #   ไม่ได้ใช้ FX เลย   fx_rate=None   fx_is_live=None   fx_error=None
    #   ใช้ FX ไม่ได้      fx_rate=None   fx_is_live=None   fx_error=ข้อความไทย
    fx_rate: float | None = None
    fx_is_live: bool | None = None
    # เหตุผลที่ไม่มีอัตราแลกเปลี่ยนให้ใช้ (ข้อความจาก ``utils.fx.FxRateUnavailable``)
    # — มีค่าเมื่อคำตอบนี้ **ต้องใช้** FX แล้วหาไม่ได้ ทำให้ยอด ETF ขาดหรือถอยไปใช้
    # snapshot  ส่วนที่ไม่พึ่ง FX (เงินสด/สินทรัพย์อื่น/หนี้สิน) ยังถูกต้องตามปกติ
    fx_error: str | None = None

    # snapshot ที่เอา "สินทรัพย์นอก ETF + หนี้สิน" มาใช้จริง — คนละอันกับ
    # ``snapshot_date`` ซึ่งเป็นวันที่คำนวณ (AUDIT_2026-08-06 L-NW-3)
    as_of_snapshot_date: str | None = None
    snapshot_age_days: int | None = None
    # ``None`` = **บอกอายุไม่ได้** (ดู ``snapshot_age_status``) ห้ามเป็น ``False``
    # ซึ่งผู้บริโภคอ่านว่า "ยังใหม่" — เดิมวันที่อ่านไม่ออก/ไม่มี snapshot เลย
    # ก็คืน ``False`` เหมือนกันหมด (AUDIT_2026-08-06 K2 ข้อ 4)
    snapshot_stale: bool | None = None
    snapshot_age_status: SnapshotAgeStatus = "no_snapshot"

    # ข้อความไทยพร้อมแสดงบนหน้าจอ ครอบทุกเรื่องข้างบนที่ผู้ใช้ต้องรู้
    warnings: list[str] = Field(default_factory=list)
