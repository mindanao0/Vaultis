"""Portfolio service — บาง ๆ ครอบ ledger เดียวของระบบ (portfolio/tracker.py, CSV).

AUDIT.md H2/H8: เดิมมี ledger 2 ชุดที่ไม่ sync กัน — CSV (dashboard + AI advisor ใช้)
กับตาราง SQLite ``transactions`` (API ใช้) — และฝั่ง SQLite **พังมาตลอด**:
``Transaction(**payload.model_dump(), ticker=...)`` โยน TypeError ทุกครั้ง
ทำให้ ``POST /api/portfolio/add`` ไม่เคยบันทึกอะไรได้เลย (ตาราง 0 แถว)

ตอนนี้ทุกช่องทางอ่าน/เขียน ledger เดียวกัน และคืน dict ที่ serialize เป็น JSON ได้

**ทุกฟังก์ชันที่แปลง DataFrame เป็น dict ต้องอ่าน ``.attrs`` ออกมาก่อน** —
``DataFrame.to_dict()`` ทิ้ง ``.attrs`` ทั้งหมด ซึ่งเป็นที่ที่ ``portfolio/tracker.py``
เก็บรายงานแถวที่ถูกตัดออกไว้ (``skipped_rows`` — FIX_PLAN ข้อ 1.2) ถ้าไม่ดึงออกมา
ผู้เรียก API จะเห็นธุรกรรมน้อยกว่าที่บันทึกไว้จริงโดยไม่มีอะไรบอก
ซึ่งผิดกฎ fail-loud พอ ๆ กับการกุตัวเลข
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from portfolio import tracker

from ..schemas import TransactionCreate


def _clean(value: Any) -> Any:
    """NaN/±inf → None เพื่อให้ JSONResponse serialize ได้ และไม่หลอกว่าเป็น 0.

    ``inf`` เกิดได้จริงเมื่อต้นทุนรวมของ ticker เป็น 0 (เช่นแถวราคา 0 ที่กรอกผิด)
    → ``Return (%) = pnl / 0`` และ ``JSONResponse`` ใช้ ``allow_nan=False``
    ถ้าปล่อยผ่านไปทั้ง endpoint จะกลายเป็น 500 — **หายทั้งก้อน รวมถึง
    ``skipped_rows`` ที่ต้องถึงผู้ใช้** ค่าที่คำนวณไม่ได้ต้องเป็น ``None``
    เหมือน NaN ไม่ใช่ล้มคำขอทิ้งหรือกลายเป็น 0
    """
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y-%m-%d")
    return value


def _skipped_report(df: pd.DataFrame) -> dict[str, Any]:
    """รายงานแถวที่น่าสงสัย อ่านจาก ``df.attrs`` — **ต้องเรียกก่อน ``to_dict()`` เสมอ**.

    คีย์ใช้ชุดเดียวกับ ``tracker.get_total_summary()`` (``skipped_rows`` /
    ``skipped_reason`` / ``derived_fx_rows`` / ``inconsistent_rows`` + ``*_reason``)
    และข้อความไทยมาจาก ``tracker`` ที่เดียว — ห้ามประกอบข้อความเองซ้ำ
    """
    return tracker.reports_of(df)


def add_transaction(payload: TransactionCreate) -> dict[str, Any]:
    row = tracker.add_transaction(
        date=str(payload.date),
        ticker=payload.ticker.upper(),
        shares=float(payload.shares),
        price_usd=float(payload.price_usd),
        fx_rate_thb=float(payload.fx_rate),
        amount_thb=float(payload.amount_thb),
        note=str(payload.note or ""),
    )
    return {k: _clean(v) for k, v in row.items()}


def delete_transaction(tx_id: str) -> bool:
    return tracker.delete_transaction(tx_id)


def get_history() -> dict[str, Any]:
    """ประวัติธุรกรรม + รายงานแถวที่ถูกตัดออก.

    คืน ``{"transactions": [...], "skipped_rows": [...], "skipped_reason": "..."}``
    — ``transactions`` ที่ว่างพร้อม ``skipped_rows`` ที่ไม่ว่าง แปลว่า
    "สมุดมีธุรกรรมแต่ใช้ไม่ได้สักแถว" ซึ่งคนละเรื่องกับ "สมุดว่าง"
    ผู้เรียกต้องแสดง ``skipped_reason`` เสมอ (ห้ามตัดข้อมูลเงียบ ๆ)
    """
    df = tracker.get_transactions()
    report = _skipped_report(df)
    records = [] if df.empty else df.to_dict(orient="records")
    return {
        "transactions": [{k: _clean(v) for k, v in row.items()} for row in records],
        **report,
    }


def get_holdings() -> dict[str, Any]:
    """สรุปรายสินทรัพย์ + รายงานแถวที่ถูกตัดออก.

    คืน ``{"holdings": [...], "skipped_rows": [...], "skipped_reason": "..."}``
    — ``price_ok=False`` แปลว่าราคาปัจจุบันดึงไม่ได้ (ค่าเป็น None)
    ส่วน ``skipped_rows`` คือธุรกรรมที่ข้อมูลไม่ครบจนไม่ได้เข้าตัวเลขข้างบนเลย
    """
    return _holdings_payload(tracker.get_portfolio_summary())


def _holdings_payload(df: pd.DataFrame) -> dict[str, Any]:
    """แปลง snapshot ของ ``tracker.get_portfolio_summary()`` เป็น dict สำหรับ JSON.

    แยกออกมาเพื่อให้ :func:`get_portfolio_summary` ใช้ **snapshot ราคาชุดเดียวกัน**
    กับที่ส่งให้ ``tracker.get_total_summary()`` ได้ (AUDIT_ROUND2 G2)
    """
    report = _skipped_report(df)
    if df.empty:
        return {"holdings": [], **report}
    result: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        result.append(
            {
                "ticker": row["Ticker"],
                "shares": _clean(row["Shares"]),
                "avg_cost_usd": _clean(row["Avg Cost (USD)"]),
                "invested_usd": _clean(row["Invested (USD)"]),
                "invested_thb": _clean(row["Invested (THB)"]),
                "current_price_usd": _clean(row["Current Price (USD)"]),
                "current_value_usd": _clean(row["Current Value (USD)"]),
                "current_value_thb": _clean(row["Current Value (THB)"]),
                "pnl_usd": _clean(row["P&L (USD)"]),
                "pnl_thb": _clean(row["P&L (THB)"]),
                # ฐาน **เงินบาท** ตัวเดียวกับ ``total_return_pct`` ของสรุปรวม (FIX_PLAN 3.3)
                "return_pct": _clean(row["Return (%)"]),
                # ฐานดอลลาร์แยกช่องพร้อมป้ายของตัวเอง — แยกผลของหุ้นออกจากผลของค่าเงิน
                "return_pct_usd": _clean(row["Return USD (%)"]),
                "fee": _clean(row["Fee (THB)"]),
                "price_ok": bool(row["Price OK"]),
            }
        )
    return {"holdings": result, **report}


def _sum_or_none(values: list[Any]) -> float | None:
    """ผลรวมของค่าที่ "รู้จริง" — ลิสต์ว่างคือ **ไม่รู้** ไม่ใช่ 0.

    ``sum([])`` คืน ``0`` ซึ่งบนเส้นทางเงินอ่านได้ว่า "มูลค่า 0 บาท / เท่าทุนพอดี"
    ทั้งที่ความจริงคือดึงราคาไม่ได้สักกอง (AUDIT_2026-08-06 H9)
    ค่า ``None`` ในลิสต์ (= NaN ที่ ``_clean`` แปลงมา) ทำให้ผลรวมทั้งก้อนไม่รู้เช่นกัน
    — ห้ามใช้สำนวน ``x or 0`` ที่กลืนทั้ง ``None`` และ ``NaN`` เป็นศูนย์
    """
    if not values or any(v is None for v in values):
        return None
    return float(sum(float(v) for v in values))


def get_portfolio_summary() -> dict[str, Any]:
    """สรุปพอร์ตสำหรับ ``/api/portfolio``.

    **เงินลงทุนมีสองฐาน ทั้งฝั่ง USD และ THB** (AUDIT_2026-08-06 H9) —
    ``invested_*_all`` = จ่ายไปจริงทั้งหมด · ``invested_*_priced`` = เฉพาะกองที่มี
    ราคาปัจจุบัน ซึ่งเป็นฐานเดียวที่ ``pnl_*`` / ``return_pct`` คิดมาจาก
    เดิมคืน ``invested_usd`` ของทุกกองคู่กับ ``pnl_usd`` ของเฉพาะกองที่มีราคา
    ⇒ ``current_value_usd − invested_usd ≠ pnl_usd`` บน payload เดียวกัน

    ``current_value_*`` / ``pnl_*`` / ``return_pct`` เป็น ``None`` เมื่อดึงราคาไม่ได้
    เลยสักกอง — "ไม่รู้" ห้ามกลายเป็น 0 (``invested_*`` ยังรู้อยู่เสมอ)
    ``invested_usd`` / ``invested_thb`` เป็นชื่อเดิมของฐาน ``all``

    **แต่สมุดว่างเป็น 0 จริง ๆ ทั้งสองสกุล ไม่ใช่ "ไม่รู้"** — คนละเรื่องกับ
    "ดึงราคาไม่ได้" แบบเดียวกับที่ ``tracker.get_total_summary()`` แยกไว้ฝั่งบาท
    ถ้าฝั่ง USD ตอบ ``None`` ขณะที่ฝั่งบาทตอบ ``0.0`` payload เดียวกันจะขัดกันเอง
    และ ``report_service._plain_narrative()`` (format ``:,.2f``) ระเบิดเป็น
    ``TypeError`` ⇒ รายงานรายเดือนของพอร์ตเปล่าสร้างไม่ได้เลย

    ``fx_is_live=False`` แปลว่าตัวเลขฝั่ง THB ทั้งหมดคิดจาก **ค่าสำรอง** ใน
    ``config.json`` เพราะดึงอัตราสดไม่ได้ (AUDIT_2026-08-06 B9) — ผู้เรียกต้องแสดง
    คำเตือนเหมือน ``missing_prices`` ห้ามปล่อยให้ตัวเลขดูเหมือนคิดจากอัตราจริง

    **ทั้ง payload มาจากการดึงราคา+FX ครั้งเดียว** (AUDIT_ROUND2 G2) — เดิมฝั่ง USD
    มาจาก ``get_holdings()`` (ดึงรอบที่ 1) ส่วนฝั่ง THB + ``missing_prices`` +
    ``fx_rate_thb`` มาจาก ``tracker.get_total_summary()`` ที่ดึงรอบที่ 2 เอง
    ถ้า yfinance ติด rate limit คั่นกลาง จะได้ ``current_value_usd`` ที่ดูสมบูรณ์
    คู่กับ ``missing_prices`` ที่บอกว่ากองนั้นไม่มีราคา (และ ``current_value_thb=None``)
    บน payload เดียวกัน ⇒ ต้องเรียก ``tracker.get_portfolio_summary()`` ครั้งเดียว
    แล้วส่ง DataFrame ตัวนั้นต่อให้ ``get_total_summary()`` เสมอ
    (ผู้เรียกที่ต้องใช้ทั้งยอดรวมและรายสินทรัพย์ในคำขอเดียว ให้ใช้
    :func:`get_summary_and_holdings` ไม่ใช่เรียกสองฟังก์ชันต่อกัน)
    """
    return get_summary_and_holdings()["summary"]


def get_summary_and_holdings() -> dict[str, Any]:
    """ยอดรวม + รายสินทรัพย์ จาก **การดึงราคา/FX ครั้งเดียว** (AUDIT_ROUND2 G2).

    คืน ``{"summary": {...}, "holdings": [...]}`` โดย ``summary`` มีรูปร่างเดียวกับ
    :func:`get_portfolio_summary` และ ``holdings`` เหมือนของ :func:`get_holdings`
    ผู้เรียกที่ต้องใช้ทั้งสองอย่าง (เช่น ``report_service``) ต้องเรียกฟังก์ชันนี้
    ไม่งั้นตัวเลขยอดรวมกับรายตัวจะมาจากคนละ snapshot และขัดกันเองได้
    """
    snapshot = tracker.get_portfolio_summary()
    holdings = _holdings_payload(snapshot)["holdings"]
    totals = tracker.get_total_summary(snapshot)
    return {"summary": _summary_payload(holdings, totals), "holdings": holdings}


def _summary_payload(holdings: list[dict[str, Any]], totals: dict[str, Any]) -> dict[str, Any]:
    """ประกอบ payload ของ ``/api/portfolio`` จาก snapshot ชุดเดียว — ไม่ดึงข้อมูลเอง."""
    priced = [h for h in holdings if h["price_ok"]]
    invested_usd_all = _sum_or_none([h["invested_usd"] for h in holdings]) if holdings else 0.0
    invested_usd_priced = _sum_or_none([h["invested_usd"] for h in priced]) if priced else 0.0
    if not holdings:
        # สมุดว่าง: ไม่มีอะไรให้ดึงราคา มูลค่า/กำไร = 0 คือคำตอบจริง (ไม่ใช่ "ไม่รู้")
        current_value_usd: float | None = 0.0
        pnl_usd: float | None = 0.0
    else:
        current_value_usd = _sum_or_none([h["current_value_usd"] for h in priced])
        pnl_usd = _sum_or_none([h["pnl_usd"] for h in priced])
    return {
        "holdings_count": len(holdings),
        # ฝั่ง USD — สองฐานติดป้ายชัด (ชื่อเดิม invested_usd = ฐาน all)
        "invested_usd": invested_usd_all,
        "invested_usd_all": invested_usd_all,
        "invested_usd_priced": invested_usd_priced,
        # ฝั่ง THB — มาจาก tracker ที่เดียว ห้ามคำนวณซ้ำที่นี่
        "invested_thb": _clean(totals["total_invested_thb"]),
        "invested_thb_all": _clean(totals["invested_thb_all"]),
        "invested_thb_priced": _clean(totals["invested_thb_priced"]),
        "current_value_usd": current_value_usd,
        "current_value_thb": _clean(totals["current_value_thb"]),
        "pnl_thb": _clean(totals["total_pnl_thb"]),
        "pnl_usd": pnl_usd,
        "return_pct": _clean(totals["total_return_pct"]),
        "total_fee": _clean(totals["total_fee_thb"]),
        # ราคาที่ดึงไม่ได้ต้องบอกผู้ใช้ — ห้ามซ่อนแล้วให้ตัวเลขดูสมบูรณ์ (AUDIT.md C1)
        "missing_prices": list(totals.get("missing_prices") or []),
        # อัตราแลกเปลี่ยนที่ใช้แปลงเป็นบาท + ที่มา (AUDIT_2026-08-06 B9/C1.5)
        # fx_is_live=False → ตัวเลขบาททุกช่องคิดจากค่าสำรองใน config ผู้เรียกต้องเตือน
        # (None = ไม่ทราบที่มา/ไม่มีการแปลงค่าเงิน — คนละเรื่องกับ False ห้ามยุบรวม)
        "fx_rate_thb": _clean(totals.get("fx_rate_thb")),
        "fx_is_live": totals.get("fx_is_live"),
        # แถวธุรกรรมที่ถูกตัด/ถูกซ่อมอัตรา/ขัดกันเอง ต้องส่งต่อให้ผู้เรียกแสดงเสมอ
        # ห้ามตัดเงียบ ๆ (FIX_PLAN ข้อ 1.2 + AUDIT_2026-08-06 C1.2/C1.3)
        "skipped_rows": list(totals.get("skipped_rows") or []),
        "skipped_reason": str(totals.get("skipped_reason") or ""),
        "derived_fx_rows": list(totals.get("derived_fx_rows") or []),
        "derived_fx_reason": str(totals.get("derived_fx_reason") or ""),
        "inconsistent_rows": list(totals.get("inconsistent_rows") or []),
        "inconsistent_reason": str(totals.get("inconsistent_reason") or ""),
    }
