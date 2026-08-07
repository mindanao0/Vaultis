# -*- coding: utf-8 -*-
"""Portfolio tracker: เก็บธุรกรรมและสรุปพอร์ตจากไฟล์ CSV.

นโยบายราคา (AUDIT.md C1): ราคาที่ดึงไม่ได้ = NaN + ธง "Price OK" = False
ห้ามเติม 0 เด็ดขาด (เดิมทำให้พอร์ตโชว์ขาดทุน -100% ปลอมและหลอก AI advisor)

นโยบายอัตราแลกเปลี่ยน (FIX_PLAN ข้อ 1.2 + รอบเก็บกวาด C1): แถวที่ **ไม่มี**
``fx_rate_thb`` หรือมีแต่ **ใช้ไม่ได้** (0 / ติดลบ / นอกช่วง ``utils.fx.MIN_RATE``–
``MAX_RATE``) จะ **คำนวณย้อนจากยอดบาทที่จ่ายจริง**
(``(amount_thb - fee_thb) / (shares * price_usd)``) ก่อน
ถ้าคำนวณไม่ได้ให้ตัดแถวทิ้ง **พร้อมรายงานออกไปทาง ``skipped_rows``**
(เหตุผลบอกด้วยว่าค่าเดิมที่บันทึกไว้คืออะไร)
ห้ามเติมค่า default ทับ (เดิม ``fillna(DEFAULT_USDTHB)`` = กุตัวเลขบนเส้นทางเงินตรง ๆ
และทำให้ ``dropna`` บรรทัดถัดไปไม่มีวันทำงาน) และห้ามปล่อยค่าที่ผิดรูปผ่านไปคิดเงิน
(fx=0 เคยทำให้เงินลงทุนกลายเป็น 0, fx ติดลบทำให้ติดลบ, ปันผลได้ยอด USD = inf)

ข้อจำกัดที่ยังเหลือ: การหักค่าธรรมเนียมก่อนหารใช้ **ค่าที่บันทึกไว้จริง** ในคอลัมน์
``fee_thb`` เท่านั้น แถวที่ไม่ได้บันทึกค่าธรรมเนียมจะหารตรง ๆ ตามยอดที่มี
(ไม่ประมาณค่าธรรมเนียมขึ้นมาหักเอง — จะเป็นการกุตัวเลขซ้อนตัวเลข)

รายงานที่แนบไปกับทุก DataFrame/สรุป (``.attrs`` และคีย์ใน dict) — AUDIT_2026-08-06 C1:

===================== ===================================================
``skipped_rows``      แถวที่ข้อมูลไม่ครบจน **ถูกตัดออก** จากทุกตัวเลข
``derived_fx_rows``   แถวที่อัตราแลกเปลี่ยนถูก **คำนวณย้อนมาแทน** ค่าที่บันทึก
                      (ยังอยู่ในตัวเลข — C1.3 เดิมมีแค่ ``logger.warning``)
``inconsistent_rows`` แถวที่ยอดบาทที่จ่ายจริง **ขัดกับ** จำนวนหุ้น × ราคา ×
                      อัตรา + ค่าธรรมเนียม เกิน 1% (ยังอยู่ในตัวเลข — C1.2)
===================== ===================================================

ทั้งสามชุดคือ "เตือน" คนละความหมายกัน ห้ามยุบรวมกัน: ``skipped_rows`` = ตัวเลข
**ขาดไป**, อีกสองชุด = ตัวเลข **อยู่ครบแต่น่าสงสัย**

อัตราแลกเปลี่ยน "วันนี้" ที่ใช้แปลงมูลค่าปัจจุบันเป็นบาทต้องไปพร้อมที่มาเสมอ
(``fx_rate_thb`` / ``fx_is_live`` — AUDIT_2026-08-06 B9/C1.5): ``utils/fx`` บอกอยู่แล้ว
ว่าเป็นค่าสดหรือค่าสำรอง แต่เดิมที่นี่รับมาเฉพาะตัวเลขแล้วทิ้งธงทิ้ง ผู้ใช้จึงไม่มีทางรู้
ว่ามูลค่า/กำไรเป็นบาทกำลังคิดจากค่าสำรองใน config (คลาดเคลื่อนวัดได้ −1.39% ณ วันตรวจ)

ฐานเงินลงทุนมีสองฐานและต้องติดป้ายเสมอ (H9): ``invested_thb_all`` = เงินที่จ่ายไป
จริงทั้งหมด · ``invested_thb_priced`` = เฉพาะกองที่มีราคาปัจจุบัน ซึ่งเป็นฐานเดียว
ที่ใช้คิด P&L / % ผลตอบแทนได้ — เดิมใช้ชื่อกลาง ๆ ตัวเดียว (``total_invested_thb``)
คู่กับกำไรที่คิดจากอีกฐานหนึ่ง ผู้ใช้จึงเห็นเลข 3 ตัวบนจอเดียวกันที่บวกลบไม่ลงตัว
และ % ผลตอบแทน **สูงขึ้น** เมื่อดึงราคาไม่ได้
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pandas as pd
import yfinance as yf

from data.fetcher import normalize_close_series
from portfolio.fees import dime_fee_thb
from utils import fx

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "tx_id",
    "date",
    "ticker",
    "shares",
    "price_usd",
    "fx_rate_thb",
    "amount_thb",
    "fee_thb",
    "note",
    "tx_type",
]

# ประเภทธุรกรรมใน ledger (Roadmap Phase 2 ข้อ 5 — เดิม buy-only)
# แถวเก่า/ค่าว่าง/ค่าที่ไม่รู้จัก ถูก normalize เป็น buy เสมอ (backward compatible)
TX_BUY = "buy"
TX_DIVIDEND = "dividend"
TRACKER_DIR = Path(__file__).resolve().parent
DATA_DIR = TRACKER_DIR / "data"
TRANSACTIONS_FILE = DATA_DIR / "transactions.csv"

# คอลัมน์ที่ต้องมีค่าจริงถึงจะเอาแถวนั้นไปคิดเงินได้ (ชื่อไทยไว้แสดงในรายงาน)
# ค่าที่ขาดต้องเป็น NaN แล้วถูกตัดออก + รายงาน ห้ามเดาค่าแทน
REQUIRED_FIELDS_TH: dict[str, str] = {
    "date": "วันที่",
    "ticker": "ticker",
    "shares": "จำนวนหุ้น",
    "price_usd": "ราคา (USD)",
    "fx_rate_thb": "อัตราแลกเปลี่ยน",
    "amount_thb": "ยอดเงิน (THB)",
}
_SKIPPED_ROWS_ATTR = "skipped_rows"
_DERIVED_FX_ROWS_ATTR = "derived_fx_rows"
_INCONSISTENT_ROWS_ATTR = "inconsistent_rows"
_REPORT_ATTRS = (_SKIPPED_ROWS_ATTR, _DERIVED_FX_ROWS_ATTR, _INCONSISTENT_ROWS_ATTR)
# อัตราแลกเปลี่ยน "วันนี้" ที่ใช้แปลงมูลค่าปัจจุบันเป็นบาท + ที่มาของมัน (B9/C1.5)
_FX_SOURCE_ATTR = "fx_source"
_MAX_SKIPPED_IN_MESSAGE = 5

# ยอดเงินบาทที่จ่ายจริง (``amount_thb``) กับ จำนวนหุ้น × ราคา × อัตรา + ค่าธรรมเนียม
# ต่างกันได้ไม่เกินเท่านี้ถึงจะถือว่า "สอดคล้องกัน" (C1.2)
# 1% กว้างพอให้ค่าธรรมเนียมที่ไม่ได้บันทึก (0.15% ตาม portfolio/fees.py) และการปัดเศษ
# ผ่านได้ แต่แคบพอจะจับการกรอกอัตราของ "วันนี้" ทับอัตราของวันที่ซื้อจริง
FX_CONSISTENCY_TOLERANCE = 0.01


def _ensure_storage() -> None:
    """สร้างไฟล์ transactions.csv และเติมคอลัมน์ที่ขาด (รวม tx_id ให้แถวเก่า)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRANSACTIONS_FILE.exists():
        pd.DataFrame(columns=CSV_COLUMNS).to_csv(TRANSACTIONS_FILE, index=False)
        return

    existing_df = pd.read_csv(TRANSACTIONS_FILE)
    changed = False
    for col in CSV_COLUMNS:
        if col not in existing_df.columns:
            if col == "fee_thb":
                existing_df[col] = 0.0
            elif col == "tx_type":
                existing_df[col] = TX_BUY  # แถวเก่าทั้งหมดคือรายการซื้อ
            else:
                existing_df[col] = ""
            changed = True

    # แถวเก่าที่ยังไม่มี tx_id → ออกให้ (ใช้อ้างอิงตอนลบผ่าน API)
    if "tx_id" in existing_df.columns and not existing_df.empty:
        missing_id = existing_df["tx_id"].isna() | (existing_df["tx_id"].astype(str).str.strip() == "")
        if missing_id.any():
            existing_df.loc[missing_id, "tx_id"] = [
                str(uuid.uuid4()) for _ in range(int(missing_id.sum()))
            ]
            changed = True

    if changed:
        existing_df = existing_df[CSV_COLUMNS]
        existing_df.to_csv(TRANSACTIONS_FILE, index=False)


def _calculate_dime_fee_info(transactions: pd.DataFrame) -> pd.DataFrame:
    """เติมลำดับเทรดรายเดือน และคำนวณค่าธรรมเนียม Dime *เฉพาะแถวที่ยังไม่มีค่าบันทึกไว้*.

    สูตรกลาง 0.15% ทุก transaction จาก ``portfolio/fees.py`` (มติ 2026-07-16 —
    เดิมคิดเทรดแรกของเดือนฟรี ซึ่งไม่ตรงกับบัญชีจริง)

    (AUDIT.md M12: เดิมคำนวณทับทุกครั้งที่โหลด — ถ้ากติกาโบรกเกอร์เปลี่ยน
    ประวัติค่าธรรมเนียมจริงที่บันทึกไว้จะถูกเขียนทับด้วยสูตรปัจจุบัน)
    """
    if transactions.empty:
        result = transactions.copy()
        result["trade_number_in_month"] = pd.Series(dtype="int64")
        result["fee_thb"] = pd.Series(dtype="float64")
        return result

    result = transactions.sort_values("date").reset_index(drop=True).copy()
    result["trade_month"] = result["date"].dt.to_period("M")
    # ปันผลไม่ใช่เทรด — ไม่นับลำดับเทรดของเดือนและไม่ประมาณค่าธรรมเนียม
    if "tx_type" in result.columns:
        is_trade = result["tx_type"] != TX_DIVIDEND
    else:
        is_trade = pd.Series(True, index=result.index)
    result["trade_number_in_month"] = 0
    result.loc[is_trade, "trade_number_in_month"] = (
        result[is_trade].groupby("trade_month").cumcount() + 1
    )
    result["trade_value_usd"] = result["shares"] * result["price_usd"]

    estimated_fee = dime_fee_thb(result["trade_value_usd"], result["fx_rate_thb"])
    estimated_fee = estimated_fee.where(is_trade, 0.0)
    # ใช้ค่าที่บันทึกไว้ก่อน; เติมด้วยค่าประมาณเฉพาะแถวที่ไม่มีค่า
    result["fee_thb"] = pd.to_numeric(result.get("fee_thb"), errors="coerce").fillna(estimated_fee)
    return result.drop(columns=["trade_month", "trade_value_usd"])


def _empty_transactions() -> pd.DataFrame:
    """DataFrame ว่างที่ dtype ถูกต้อง.

    (เดิมคืน ``pd.DataFrame(columns=CSV_COLUMNS)`` ซึ่งคอลัมน์ date เป็น object →
    ``transactions["date"].dt`` พัง → **การเพิ่มธุรกรรมแรกสุดลงสมุดที่ว่างจะ crash**)
    """
    empty = pd.DataFrame(
        {
            "tx_id": pd.Series(dtype="object"),
            "date": pd.Series(dtype="datetime64[ns]"),
            "ticker": pd.Series(dtype="object"),
            "shares": pd.Series(dtype="float64"),
            "price_usd": pd.Series(dtype="float64"),
            "fx_rate_thb": pd.Series(dtype="float64"),
            "amount_thb": pd.Series(dtype="float64"),
            "fee_thb": pd.Series(dtype="float64"),
            "note": pd.Series(dtype="object"),
            "tx_type": pd.Series(dtype="object"),
        }
    )
    empty["trade_number_in_month"] = pd.Series(dtype="int64")
    return empty


def _recorded_fee_thb(df: pd.DataFrame) -> pd.Series | float:
    """ค่าธรรมเนียมที่ **บันทึกไว้จริง** ของแต่ละแถว (แถวที่ไม่มี = 0).

    ใช้เฉพาะตอนคำนวณอัตราแลกเปลี่ยนย้อนกลับ: ``amount_thb`` คือเงินที่จ่ายจริง
    ซึ่งรวมค่าธรรมเนียมไว้แล้ว จึงต้องหักออกก่อนหาร ไม่งั้นอัตราที่ได้สูงเกินจริง
    ~0.15% (= ``fees.DIME_FEE_RATE``)

    แถวที่ไม่ได้บันทึกค่าธรรมเนียม (หรือบันทึกเป็นค่าติดลบ = ข้อมูลผิด) คืน 0
    — **ไม่ประมาณค่าธรรมเนียมขึ้นมาหักเอง** เพราะยังไม่รู้อัตราแลกเปลี่ยนของแถวนั้น
    การเดาจะกลายเป็นการกุตัวเลขซ้อนตัวเลข (การเติมค่าประมาณให้คอลัมน์ ``fee_thb``
    เป็นหน้าที่ของ ``_calculate_dime_fee_info`` ซึ่งทำหลังจากได้อัตราจริงแล้ว)
    """
    if "fee_thb" not in df.columns:
        return 0.0
    fee = pd.to_numeric(df["fee_thb"], errors="coerce")
    return fee.where(fee >= 0).fillna(0.0)


def _derive_fx_from_amount(df: pd.DataFrame) -> pd.Series:
    """อัตราแลกเปลี่ยนที่ "จ่ายจริง" ของแต่ละแถว คำนวณย้อนจากยอดบาทที่บันทึกไว้.

    นี่คือ**ค่าจริง** ไม่ใช่ค่าเดา: ``(amount_thb - fee_thb) / (shares * price_usd)``
    (ค่าธรรมเนียมที่บันทึกไว้ต้องหักออกก่อน เพราะ ``amount_thb`` = เงินที่จ่ายจริง
    ซึ่งรวมค่าธรรมเนียมตามสูตรเดียวของระบบใน ``portfolio/fees.py`` ไว้แล้ว)

    คืน NaN เมื่อคำนวณไม่ได้ (ตัวหาร ≤ 0 หรือว่าง เช่นแถวปันผลที่ shares=0)
    หรือได้ค่านอกช่วงสมเหตุสมผลของ ``utils/fx.py`` — ค่าที่ไม่น่าเชื่อถือ
    ถือว่า "ไม่มีข้อมูล" ห้ามเอาไปคิดเงินต่อ
    """
    trade_value_usd = df["shares"] * df["price_usd"]
    denominator = trade_value_usd.where(trade_value_usd > 0)
    derived = (df["amount_thb"] - _recorded_fee_thb(df)) / denominator
    return derived.where(derived.between(fx.MIN_RATE, fx.MAX_RATE))


def _collect_skipped_rows(
    df: pd.DataFrame,
    unusable_fx: pd.Series | None = None,
) -> list[dict[str, object]]:
    """รายการแถวที่ข้อมูลไม่ครบจนเอาไปคิดเงินไม่ได้ พร้อมเหตุผลรายแถว.

    ตัดแถวเงียบ ๆ ไม่ได้ (แค่ย้ายจากกุตัวเลขไปเป็นซ่อนข้อมูล ผิดกฎ fail-loud เหมือนกัน)
    ผู้เรียกต้องเอาไปแสดงให้ผู้ใช้เห็นเสมอ

    ``unusable_fx`` = อัตราแลกเปลี่ยน**ที่บันทึกไว้จริงแต่ใช้ไม่ได้** (NaN = ไม่ได้บันทึก)
    ใส่ลงในเหตุผลด้วย เพื่อให้ผู้ใช้รู้ว่าต้องไปแก้เลขอะไรในสมุด
    """
    missing_mask = df[list(REQUIRED_FIELDS_TH)].isna()
    incomplete = missing_mask.any(axis=1)
    if not bool(incomplete.any()):
        return []

    skipped: list[dict[str, object]] = []
    for idx in df.index[incomplete]:
        fields = [col for col in REQUIRED_FIELDS_TH if bool(missing_mask.at[idx, col])]
        reason = "ข้อมูลไม่ครบ: " + ", ".join(REQUIRED_FIELDS_TH[col] for col in fields)
        if "fx_rate_thb" in fields:
            recorded_fx = None if unusable_fx is None else unusable_fx.at[idx]
            band = f"{fx.MIN_RATE:.0f}–{fx.MAX_RATE:.0f}"
            if recorded_fx is not None and pd.notna(recorded_fx):
                reason += (
                    f" (ค่าที่บันทึกไว้ {float(recorded_fx):g} ใช้ไม่ได้ ต้องอยู่ในช่วง {band}"
                    " และคำนวณอัตราย้อนจากยอดบาทไม่ได้)"
                )
            else:
                reason += f" (คำนวณอัตราย้อนจากยอดบาทไม่ได้ หรือได้ค่านอกช่วง {band})"
        skipped.append(
            {
                **_row_identity(df, idx),
                "missing_fields": fields,
                "reason": reason,
            }
        )
    return skipped


def _row_identity(df: pd.DataFrame, idx: object) -> dict[str, object]:
    """คีย์ประจำตัวของแถวที่ต้องมีในทุกรายงาน (``tx_id``/``date``/``ticker``/``tx_type``).

    ``tx_type`` จำเป็นเพราะรายงานถูกกรองตามประเภทธุรกรรม — สรุปปันผลต้องไม่แสดง
    ไม้ซื้อที่ถูกตัด ไม่งั้นผู้ใช้อ่านว่า "ปันผลหายไป" (C1.4)
    """
    date_value = df.at[idx, "date"]
    ticker_value = df.at[idx, "ticker"]
    tx_type = df.at[idx, "tx_type"] if "tx_type" in df.columns else TX_BUY
    return {
        "tx_id": str(df.at[idx, "tx_id"]) if "tx_id" in df.columns else "",
        "date": "" if pd.isna(date_value) else pd.Timestamp(date_value).strftime("%Y-%m-%d"),
        "ticker": "" if pd.isna(ticker_value) else str(ticker_value),
        "tx_type": TX_BUY if pd.isna(tx_type) else str(tx_type),
    }


def _complete_rows_mask(df: pd.DataFrame) -> pd.Series:
    """แถวที่ข้อมูลครบพอจะคิดเงินได้ (= แถวที่จะรอดจาก ``dropna`` ข้างล่าง)."""
    return ~df[list(REQUIRED_FIELDS_TH)].isna().any(axis=1)


def _collect_derived_fx_rows(
    df: pd.DataFrame,
    usable_fx: pd.Series,
    recorded_fx: pd.Series,
) -> list[dict[str, object]]:
    """แถวที่อัตราแลกเปลี่ยน **ถูกคำนวณย้อนมาแทน** ค่าที่บันทึกไว้ (C1.3).

    แถวเหล่านี้ *ยังอยู่* ในทุกตัวเลข — ต่างจาก ``skipped_rows`` ที่ถูกตัดออก
    เดิมมีแค่ ``logger.warning`` ผู้ใช้จึงไม่มีทางรู้ว่าเงินที่เห็นคำนวณจากอัตรา
    ที่ระบบหาเอง ต้องรายงานออกไปให้เห็นเหมือน ``skipped_rows``

    ``usable_fx`` = อัตราที่บันทึกไว้ **หลังผ่านด่านช่วง 20–50** (NaN = ใช้ไม่ได้/ไม่มี)
    ``recorded_fx`` = ค่าดิบที่อยู่ในสมุดจริง ๆ (NaN = ช่องว่าง)
    ต้องเรียก **หลัง** เติมค่า derive ลง ``df['fx_rate_thb']`` แล้ว
    """
    if df.empty:
        return []
    derived_mask = _complete_rows_mask(df) & usable_fx.isna() & df["fx_rate_thb"].notna()
    if not bool(derived_mask.any()):
        return []

    band = f"{fx.MIN_RATE:.0f}–{fx.MAX_RATE:.0f}"
    rows: list[dict[str, object]] = []
    for idx in df.index[derived_mask]:
        recorded = recorded_fx.at[idx]
        used = float(df.at[idx, "fx_rate_thb"])
        if recorded is not None and pd.notna(recorded):
            reason = (
                f"อัตราที่บันทึกไว้ {float(recorded):g} ใช้ไม่ได้ (ต้องอยู่ในช่วง {band}) "
                f"— ใช้อัตราที่คำนวณย้อนจากยอดเงินบาท {used:.4f} แทน"
            )
            recorded_value: float | None = float(recorded)
        else:
            reason = f"ไม่ได้บันทึกอัตราแลกเปลี่ยน — ใช้อัตราที่คำนวณย้อนจากยอดเงินบาท {used:.4f}"
            recorded_value = None
        rows.append(
            {
                **_row_identity(df, idx),
                "recorded_fx": recorded_value,
                "used_fx": used,
                "reason": reason,
            }
        )
    return rows


def _collect_inconsistent_rows(df: pd.DataFrame) -> list[dict[str, object]]:
    """แถวที่ยอดเงินบาทที่จ่ายจริง **ขัดกับ** ตัวเลขอื่นในแถวเดียวกัน (C1.2).

    ทุกไม้ซื้อมีข้อมูลพอจะตรวจตัวเองได้อยู่แล้ว:
    ``amount_thb ≈ shares × price_usd × fx_rate_thb + fee_thb``
    เดิมไม่มีชั้นไหนเทียบเลย อัตราที่ "ผิดแต่อยู่ในช่วง 20–50" จึงรอดทุกด่าน
    (ตัวจุดชนวนคือฟอร์มบันทึกย้อนหลังที่ตั้งค่าเริ่มต้นเป็น **อัตราวันนี้**)

    **เตือนอย่างเดียว ไม่ตัดทิ้ง** — ข้อมูลครบและระบบบันทึกตามที่ผู้ใช้บอก
    สิ่งที่ผิดคือ "ไม่ตรวจข้อมูลที่ขัดกันเอง" ไม่ใช่ "ข้อมูลไม่ครบ"

    ข้ามแถวปันผล (shares/price = 0 ไม่มีอะไรให้เทียบ)

    **"เทียบไม่ได้" ไม่เท่ากับ "ผ่าน" (K4).** การเทียบใช้สัดส่วนโดยมี ``amount_thb``
    เป็นตัวหาร ยอดเงินที่เป็น 0 / ติดลบ / ±inf ทำให้สัดส่วนเป็น ``NaN`` และ
    ``NaN > threshold`` เป็น ``False`` เสมอ ⇒ แถวที่ขัดกันชัด ๆ (จ่าย 0 บาท
    ทั้งที่มี จำนวนหุ้น × ราคา ให้เทียบ) เคยเดินผ่านด่านนี้เงียบ ๆ
    แถวเหล่านั้นต้องถูกรายงาน โดย ``diff_pct``/``implied_fx`` เป็น ``None``
    (= ไม่ทราบ) ห้ามเป็น ``nan`` หรือตัวเลขที่หารด้วยศูนย์แล้วเดาขึ้นมา

    **ตัวเศษก็เล็ดลอดได้ด้วยกลไกเดียวกัน (K4 รอบสอง).** ``gap`` เป็น ``NaN`` ได้เอง
    เมื่อ ``implied`` คำนวณไม่ออก (``0 × inf``) หรือทั้งสองฝั่งเป็น ``inf``
    (``inf − inf``) — ตอนนั้น **ทั้ง** ``ratio > tol`` และ ``gap > 0`` เป็น ``False``
    พร้อมกัน ด่านจึงกลับไปเงียบอีกครั้ง ส่วนต่างที่ไม่ใช่ตัวเลขจริงคือ "เทียบไม่ได้เลย"
    ซึ่งต้องเตือน ไม่ใช่ผ่าน (ทุกด่านในไฟล์นี้ต้องกรองด้วย :func:`_is_real_number`
    ก่อนใช้ ``>``/``<`` เสมอ ห้ามเทียบดิบ ๆ)

    ยอดเงิน 0 ที่ implied ก็เป็น 0 ด้วย (หุ้นแถมราคา 0) ไม่ถือว่าขัดกัน — ไม่มีตัวเลข
    ไหนขัดกับตัวเลขไหน การเตือนตรงนั้นคือเสียงรบกวน ไม่ใช่ข้อมูล
    """
    if df.empty:
        return []
    is_trade = (
        df["tx_type"] != TX_DIVIDEND
        if "tx_type" in df.columns
        else pd.Series(True, index=df.index)
    )
    amount = df["amount_thb"]
    fee = _recorded_fee_thb(df)
    if not isinstance(fee, pd.Series):
        fee = pd.Series(float(fee), index=df.index)
    implied = df["shares"] * df["price_usd"] * df["fx_rate_thb"] + fee
    gap = (amount - implied).abs()
    # ตัวหารต้องเป็นจำนวนเงินบวกที่มีค่าจริงเท่านั้น (inf ผ่าน ``> 0`` ได้ แต่ inf/inf = NaN)
    comparable_amount = _is_real_number(amount) & (amount > 0)
    comparable = comparable_amount & _is_real_number(implied)
    ratio = gap / amount.where(comparable_amount)
    flagged = (
        _complete_rows_mask(df)
        & is_trade
        & (
            ratio.gt(FX_CONSISTENCY_TOLERANCE)  # เส้นทางปกติ: ต่างกันเกินเกณฑ์
            # เทียบสัดส่วนไม่ได้ แต่ยังมีส่วนต่างจริงให้เห็น → ต่างกันบาทเดียวก็ผิด
            | (~comparable & gap.gt(0))
            # ส่วนต่างเองก็ไม่ใช่ตัวเลขจริง (implied = 0 × inf, หรือ inf − inf)
            # → เทียบไม่ได้เลยสักทาง ซึ่งไม่ใช่ "ผ่าน": ทั้งสองเงื่อนไขข้างบนเป็น
            # False พร้อมกันเพราะ NaN ไม่ใช่เพราะตัวเลขตรงกัน
            | ~_is_real_number(gap)
            # ยอดเงินที่จ่ายติดลบไม่มีทางถูก แม้ implied จะติดลบตามไปด้วย
            | amount.lt(0)
        )
    )
    if not bool(flagged.any()):
        return []

    rows: list[dict[str, object]] = []
    for idx in df.index[flagged]:
        paid = float(amount.at[idx])
        expected = float(implied.at[idx])
        recorded_rate = float(df.at[idx, "fx_rate_thb"])
        # % ที่หารด้วยตัวหารที่ใช้ไม่ได้ = ตัวเลขที่ระบบแต่งเอง — ต้องเป็น None (ไม่ทราบ)
        diff_pct = float(ratio.at[idx]) * 100.0 if bool(comparable.at[idx]) else None
        trade_value_usd = float(df.at[idx, "shares"]) * float(df.at[idx, "price_usd"])
        implied_rate, rate_txt = _implied_rate_note(
            paid=paid,
            fee=float(fee.at[idx]),
            trade_value_usd=trade_value_usd,
            recorded_rate=recorded_rate,
        )
        if diff_pct is None:
            head = (
                f"ยอดเงินที่บันทึกไว้ {_fmt_thb(paid)} บาท เทียบสัดส่วนกับ "
                f"จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน + ค่าธรรมเนียม = {_fmt_thb(expected)} บาท ไม่ได้ "
                f"({_uncomparable_cause(paid)}) "
            )
        else:
            head = (
                f"ยอดเงินที่บันทึกไว้ {_fmt_thb(paid)} บาท ไม่ตรงกับ "
                f"จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน + ค่าธรรมเนียม = {_fmt_thb(expected)} บาท "
                f"(ต่างกัน {diff_pct:.2f}%) "
            )
        rows.append(
            {
                **_row_identity(df, idx),
                # ตัวเลขที่ไม่ใช่ตัวเลขจริงต้องออกไปเป็น None (= ไม่ทราบ) เหมือน ``diff_pct``
                # — ``nan``/``inf`` แปลงเป็น JSON ที่ถูกต้องไม่ได้ และไปโผล่บนตารางหน้าจอ
                # ส่วนที่อธิบายว่าเกิดอะไรขึ้นอยู่ใน ``reason`` ซึ่งพูดว่า "คำนวณไม่ได้" ตรง ๆ
                "amount_thb": _real_or_none(paid),
                "implied_amount_thb": _real_or_none(expected),
                "recorded_fx": _real_or_none(recorded_rate),
                "implied_fx": implied_rate,
                "diff_pct": diff_pct,
                "reason": f"{head}{rate_txt}ตัวเลขยังถูกนับอยู่ ให้ตรวจสอบแถวนี้ในสมุด",
            }
        )
    return rows


def _is_real_number(values: pd.Series) -> pd.Series:
    """True เฉพาะค่าที่เป็นตัวเลขจริง — ``NaN``/``±inf`` เป็น False.

    ใช้คุมด่านที่เทียบด้วยเครื่องหมาย ``>``/``<``: ค่าที่ไม่ใช่ตัวเลขจริงทำให้
    การเปรียบเทียบเป็น ``False`` เสมอ ซึ่งอ่านได้ว่า "ผ่าน" ทั้งที่จริงคือ "เทียบไม่ได้"
    """
    return values.notna() & values.abs().ne(float("inf"))


def _is_real_scalar(value: float) -> bool:
    """คู่สเกลาร์ของ :func:`_is_real_number` — นิยาม "ตัวเลขจริง" ต้องมีที่เดียว."""
    return bool(pd.notna(value)) and abs(float(value)) != float("inf")


def _real_or_none(value: float) -> float | None:
    """ตัวเลขจริงเท่านั้นที่ออกไปเป็นตัวเลข — ``NaN``/``±inf`` ออกไปเป็น ``None`` (ไม่ทราบ)."""
    return float(value) if _is_real_scalar(value) else None


def _uncomparable_cause(paid: float) -> str:
    """เหตุผลว่า **ทำไม** ถึงเทียบสัดส่วนไม่ได้ — ต้องชี้ฝั่งที่พังจริง.

    เหตุผลที่ชี้ผิดฝั่งคือการกุคำอธิบาย: แถวที่ยอดเงินปกติดีแต่ ``implied``
    คำนวณไม่ออก (จำนวนหุ้น/ราคาเป็น ``inf``) เคยขึ้นข้อความว่า "ยอดเงินต้องเป็น
    จำนวนบวก" ซึ่งส่งผู้ใช้ไปนั่งแก้เลขที่ถูกอยู่แล้ว
    """
    if not (_is_real_scalar(paid) and paid > 0):
        return "ยอดเงินต้องเป็นจำนวนบวก"
    return "จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน + ค่าธรรมเนียม คำนวณเป็นตัวเลขจริงไม่ได้"


def _fmt_thb(value: float) -> str:
    """จำนวนเงินสำหรับข้อความเตือน — ค่าที่ไม่ใช่ตัวเลขจริงต้องพูดตรง ๆ ห้ามพิมพ์ ``nan``."""
    if not _is_real_scalar(value):
        return "คำนวณไม่ได้"
    return f"{value:,.2f}"


def _implied_rate_note(
    *,
    paid: float,
    fee: float,
    trade_value_usd: float,
    recorded_rate: float,
) -> tuple[float | None, str]:
    """อัตราแลกเปลี่ยนที่คำนวณย้อนจากยอดเงินของแถวนั้น + ข้อความอธิบายเมื่อคำนวณไม่ได้.

    คืน ``None`` เมื่อผลลัพธ์ไม่ใช่อัตราแลกเปลี่ยนที่เป็นไปได้ (ตัวหาร ≤ 0 จาก
    หุ้นแถมราคา 0 / จำนวนหุ้นติดลบ หรือยอดเงินหลังหักค่าธรรมเนียม ≤ 0)
    — ตัวเลขที่หารด้วยศูนย์/ติดลบแล้วเดาขึ้นมาคือการกุตัวเลขบนเส้นทางเงิน

    เหตุผลต้องบอก **ค่าจริง** ของตัวที่ทำให้คำนวณไม่ได้ ห้ามเขียนเหมารวมว่า "= 0"
    (ผู้ใช้จะไปตามหาราคา 0 ที่ไม่มีอยู่จริง แทนที่จะเห็นว่าจำนวนหุ้นติดลบ)
    """
    if not (_is_real_scalar(trade_value_usd) and trade_value_usd > 0):
        return None, (
            f"— คำนวณอัตราย้อนจากยอดเงินบาทไม่ได้ (จำนวนหุ้น × ราคา = {_fmt_thb(trade_value_usd)}) "
        )
    net_paid = paid - fee
    rate = net_paid / trade_value_usd
    if not (_is_real_scalar(rate) and rate > 0):
        return None, (
            "— คำนวณอัตราย้อนจากยอดเงินบาทไม่ได้ "
            f"(ยอดเงินหลังหักค่าธรรมเนียม = {_fmt_thb(net_paid)}) "
        )
    return rate, (
        f"— อัตราที่คำนวณย้อนจากยอดเงินบาทคือ {rate:.4f} "
        f"แต่ในสมุดบันทึกไว้ {recorded_rate:.4f} "
    )


def describe_skipped_rows(skipped: list[dict[str, object]]) -> str:
    """ข้อความไทยบรรทัดเดียวสำหรับแสดงเตือนผู้ใช้; คืน "" เมื่อไม่มีแถวถูกข้าม."""
    return _describe_rows(
        skipped,
        head="ข้ามธุรกรรม {n} แถวเพราะข้อมูลไม่ครบ ตัวเลขสรุปไม่รวมแถวเหล่านี้: ",
    )


def describe_derived_fx_rows(rows: list[dict[str, object]]) -> str:
    """ข้อความไทยบรรทัดเดียวสำหรับแถวที่อัตราถูกคำนวณย้อนมาแทน (C1.3)."""
    return _describe_rows(
        rows,
        head=(
            "อัตราแลกเปลี่ยน {n} แถวถูกคำนวณย้อนจากยอดเงินบาทเพราะค่าที่บันทึกไว้ใช้ไม่ได้ "
            "(ตัวเลขด้านล่างรวมแถวเหล่านี้อยู่): "
        ),
    )


def describe_inconsistent_rows(rows: list[dict[str, object]]) -> str:
    """ข้อความไทยบรรทัดเดียวสำหรับแถวที่ยอดเงินขัดกับอัตราที่บันทึก (C1.2)."""
    return _describe_rows(
        rows,
        head=(
            "ยอดเงินบาทของ {n} แถวไม่ตรงกับ จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน "
            "(ตัวเลขด้านล่างยังนับแถวเหล่านี้อยู่ ให้ตรวจสอบอัตราที่บันทึกไว้): "
        ),
    )


def _describe_rows(rows: list[dict[str, object]], *, head: str) -> str:
    """ประกอบข้อความเตือนรายแถวแบบเดียวกับ ``describe_skipped_rows`` (ตัดที่ 5 แถว)."""
    if not rows:
        return ""
    shown = rows[:_MAX_SKIPPED_IN_MESSAGE]
    parts = [
        f"{row.get('ticker') or 'ไม่ทราบ ticker'} "
        f"{row.get('date') or 'ไม่ทราบวันที่'} — {row.get('reason')}"
        for row in shown
    ]
    remaining = len(rows) - len(shown)
    if remaining > 0:
        parts.append(f"และอีก {remaining} แถว")
    return head.format(n=len(rows)) + "; ".join(parts)


def _empty_reports() -> dict[str, list[dict[str, object]]]:
    return {attr: [] for attr in _REPORT_ATTRS}


def _with_reports(df: pd.DataFrame, reports: dict[str, list[dict[str, object]]]) -> pd.DataFrame:
    """แนบรายงานทั้งสามชุดไปกับ DataFrame (อ่านต่อด้วย ``_reports_of``)."""
    for attr in _REPORT_ATTRS:
        df.attrs[attr] = list(reports.get(attr) or [])
    return df


def _reports_of(df: pd.DataFrame) -> dict[str, list[dict[str, object]]]:
    """อ่านรายงานทั้งสามชุดจาก DataFrame (ว่างถ้าไม่มี/ถูก monkeypatch)."""
    attrs = getattr(df, "attrs", {})
    return {attr: list(attrs.get(attr) or []) for attr in _REPORT_ATTRS}


def _report_keys(
    reports: dict[str, list[dict[str, object]]],
    *,
    tx_type: str | None = None,
) -> dict[str, object]:
    """แปลงรายงานเป็นคีย์ dict พร้อมข้อความไทย — รูปแบบเดียวกันทุก summary.

    ``tx_type`` กรองเฉพาะแถวของประเภทธุรกรรมนั้น (C1.4 — สรุปปันผลต้องไม่แสดง
    ไม้ซื้อที่ถูกตัด ผู้ใช้จะเข้าใจว่าปันผลที่บันทึกไว้หายไป)
    """

    def _filtered(attr: str) -> list[dict[str, object]]:
        rows = reports.get(attr) or []
        if tx_type is None:
            return list(rows)
        return [row for row in rows if str(row.get("tx_type") or TX_BUY) == tx_type]

    skipped = _filtered(_SKIPPED_ROWS_ATTR)
    derived = _filtered(_DERIVED_FX_ROWS_ATTR)
    inconsistent = _filtered(_INCONSISTENT_ROWS_ATTR)
    return {
        "skipped_rows": skipped,
        "skipped_reason": describe_skipped_rows(skipped),
        "derived_fx_rows": derived,
        "derived_fx_reason": describe_derived_fx_rows(derived),
        "inconsistent_rows": inconsistent,
        "inconsistent_reason": describe_inconsistent_rows(inconsistent),
    }


def _with_fx_source(df: pd.DataFrame, quote: tuple[float, bool | None]) -> pd.DataFrame:
    """แนบอัตราแลกเปลี่ยนที่ใช้จริง + ที่มา ไปกับ DataFrame (อ่านต่อด้วย ``_fx_source_of``)."""
    df.attrs[_FX_SOURCE_ATTR] = {"fx_rate_thb": quote[0], "fx_is_live": quote[1]}
    return df


def _fx_source_of(df: pd.DataFrame) -> dict[str, object]:
    """คีย์อัตราแลกเปลี่ยนของ summary — ไม่มีข้อมูล = ``None`` (ไม่ทราบ) ห้ามเดา."""
    source = getattr(df, "attrs", {}).get(_FX_SOURCE_ATTR) or {}
    return {
        "fx_rate_thb": source.get("fx_rate_thb"),
        "fx_is_live": source.get("fx_is_live"),
    }


def reports_of(df: pd.DataFrame) -> dict[str, object]:
    """คีย์รายงานพร้อมข้อความไทย สำหรับผู้เรียกที่แปลง DataFrame เป็น dict.

    ``DataFrame.to_dict()`` ทิ้ง ``.attrs`` ทั้งหมด — ต้องเรียกฟังก์ชันนี้ **ก่อน**
    แปลง ไม่งั้นคำเตือนหายทั้งชุด (ผิดกฎ fail-loud พอ ๆ กับการกุตัวเลข)
    """
    return _report_keys(_reports_of(df))


def _load_transactions() -> pd.DataFrame:
    """อ่านธุรกรรมจาก CSV และ normalize ชนิดข้อมูล.

    แถวที่ข้อมูลไม่ครบถูกตัดออกและรายงานไว้ที่ ``df.attrs['skipped_rows']``
    แถวที่ยังอยู่แต่น่าสงสัยอยู่ที่ ``derived_fx_rows`` / ``inconsistent_rows``
    """
    _ensure_storage()
    df = pd.read_csv(TRANSACTIONS_FILE)
    if df.empty:
        return _with_reports(_empty_transactions(), _empty_reports())

    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[CSV_COLUMNS].copy()
    df["tx_id"] = df["tx_id"].astype(str).str.strip()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    ticker = df["ticker"].astype(str).str.strip().str.upper()
    # ค่าว่าง/NaN ที่ astype(str) แปลงเป็นข้อความ ต้องกลับไปเป็น "ไม่มีข้อมูล"
    # ไม่ใช่กลายเป็น ticker ชื่อ "NAN" ที่โผล่ในพอร์ตเป็นสินทรัพย์ผี
    df["ticker"] = ticker.where(~ticker.isin(["", "NAN", "NONE"]))
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    df["price_usd"] = pd.to_numeric(df["price_usd"], errors="coerce")
    df["fx_rate_thb"] = pd.to_numeric(df["fx_rate_thb"], errors="coerce")
    df["amount_thb"] = pd.to_numeric(df["amount_thb"], errors="coerce")
    df["fee_thb"] = pd.to_numeric(df["fee_thb"], errors="coerce")
    df["note"] = df["note"].fillna("").astype(str)
    # ค่าว่าง/ไม่รู้จัก = buy เสมอ — ห้ามทิ้งแถวเก่าเงียบ ๆ เพราะ schema เพิ่มทีหลัง
    tx_type = df["tx_type"].fillna("").astype(str).str.strip().str.lower()
    df["tx_type"] = tx_type.where(tx_type.isin([TX_BUY, TX_DIVIDEND]), TX_BUY)

    # ชั้นที่ 0 — อัตราที่บันทึกไว้แต่ "มีค่าแต่ใช้ไม่ได้" (0 / ติดลบ / นอกช่วง 20–50)
    # คือข้อมูลผิด ไม่ใช่ค่าจริง ต้องเดินเส้นทางเดียวกับค่าว่าง ห้ามไหลเข้าไปคิดเงิน
    recorded_fx = df["fx_rate_thb"]
    usable_fx = recorded_fx.where(recorded_fx.between(fx.MIN_RATE, fx.MAX_RATE))
    unusable_fx = recorded_fx.where(recorded_fx.notna() & usable_fx.isna())
    df["fx_rate_thb"] = usable_fx
    # ชั้นที่ 1 — หาค่าจริงก่อนเดา: อัตราที่จ่ายจริงย้อนจากยอดบาทของแถวนั้นเอง
    df["fx_rate_thb"] = df["fx_rate_thb"].fillna(_derive_fx_from_amount(df))
    recovered = unusable_fx.notna() & df["fx_rate_thb"].notna()
    if bool(recovered.any()):
        logger.warning(
            "อัตราแลกเปลี่ยนที่บันทึกไว้ใช้ไม่ได้ %d แถวใน %s — ใช้อัตราที่คำนวณย้อนจากยอดบาทแทน: %s",
            int(recovered.sum()),
            TRANSACTIONS_FILE,
            df.loc[recovered, "tx_id"].tolist(),
        )
    # ชั้นที่ 2 — ที่เหลือปล่อยเป็น NaN ให้ dropna ทำงานจริง แล้วรายงานออกไป
    skipped = _collect_skipped_rows(df, unusable_fx)
    if skipped:
        logger.warning(
            "ข้ามธุรกรรม %d แถวใน %s เพราะข้อมูลไม่ครบ: %s",
            len(skipped),
            TRANSACTIONS_FILE,
            [row["tx_id"] for row in skipped],
        )
    # ชั้นที่ 3 — แถวที่ "ยังอยู่แต่น่าสงสัย" ต้องถึงหน้าจอด้วย ไม่ใช่ค้างอยู่ใน log
    reports = {
        _SKIPPED_ROWS_ATTR: skipped,
        _DERIVED_FX_ROWS_ATTR: _collect_derived_fx_rows(df, usable_fx, recorded_fx),
        _INCONSISTENT_ROWS_ATTR: _collect_inconsistent_rows(df),
    }
    df = df.dropna(subset=list(REQUIRED_FIELDS_TH))
    if df.empty:
        return _with_reports(_empty_transactions(), reports)
    return _with_reports(_calculate_dime_fee_info(df), reports)


def _get_latest_prices(tickers: list[str]) -> dict[str, float]:
    """ดึงราคาล่าสุดของแต่ละ ticker จาก yfinance."""
    if not tickers:
        return {}

    try:
        downloaded = yf.download(
            tickers=tickers,
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
        )
    except Exception as exc:
        logger.warning("ดึงราคาล่าสุดไม่สำเร็จ (%s): %s", tickers, exc)
        return {}

    if downloaded.empty:
        logger.warning("ดึงราคาล่าสุดได้ผลว่างเปล่า (%s)", tickers)
        return {}

    prices: dict[str, float] = {}
    if isinstance(downloaded.columns, pd.MultiIndex):
        available = set(downloaded.columns.get_level_values(0))
        for ticker in tickers:
            if ticker not in available:
                continue
            close_series = normalize_close_series(downloaded[ticker])
            if not close_series.empty:
                prices[ticker] = float(close_series.iloc[-1])
        return prices

    close_series = normalize_close_series(downloaded)
    if len(tickers) == 1 and not close_series.empty:
        prices[tickers[0]] = float(close_series.iloc[-1])
    return prices


def _get_usdthb_rate() -> float:
    """อัตราแลกเปลี่ยน THB/USD จากแหล่งกลางเดียวของระบบ (utils/fx.py)."""
    return fx.get_usdthb_rate()


def _get_fx_quote() -> tuple[float, bool | None]:
    """อัตราที่ใช้แปลงมูลค่าวันนี้เป็นบาท **พร้อมที่มา** (AUDIT_2026-08-06 B9/C1.5).

    ยังดึงตัวเลขผ่าน :func:`_get_usdthb_rate` เพื่อให้ทั้งไฟล์เหลือทางเข้าเดียวเหมือนเดิม
    แล้วถาม ``utils.fx`` ว่าอัตราตัวนี้เป็นค่าสดหรือค่าสำรอง (``source_of`` อ่านจากแคช
    ไม่ยิงเน็ตซ้ำ) — ที่มาต้องเดินทางไปถึงหน้าจอ/API เพราะค่าสำรองทำให้ตัวเลขบาททั้งก้อน
    (``Current Value (THB)`` / ``P&L (THB)``) คลาดเคลื่อนโดยไม่มีอะไรบอก

    ``None`` = **ไม่ทราบที่มา** เกิดเมื่อผู้เรียกจัดหาอัตรามาเอง (เช่นเทสต์ที่แทน
    :func:`_get_usdthb_rate`) — คนละความหมายกับ ``False`` ที่แปลว่ารู้ว่าเป็นค่าสำรอง
    """
    rate = float(_get_usdthb_rate())
    return rate, fx.source_of(rate)


def get_today_fx_rate_thb() -> float:
    """คืนค่าอัตราแลกเปลี่ยน THB/USD ล่าสุดพร้อม fallback."""
    return _get_usdthb_rate()


def estimate_dime_fee_thb(
    trade_date: str | pd.Timestamp,
    shares: float,
    price_usd: float,
    fx_rate_thb: float,
) -> tuple[int, float]:
    """คำนวณลำดับเทรดของเดือนและค่าธรรมเนียม Dime โดยประมาณ.

    0.15% ทุก transaction (มติ 2026-07-16) — ลำดับเทรดของเดือนคงไว้เพื่อแสดงผลเท่านั้น
    ไม่มีผลต่อค่าธรรมเนียมอีกต่อไป
    """
    transaction_date = pd.to_datetime(trade_date)
    transactions = _load_transactions()
    if "tx_type" in transactions.columns:
        transactions = transactions[transactions["tx_type"] != TX_DIVIDEND]
    same_month_count = int(
        (transactions["date"].dt.to_period("M") == transaction_date.to_period("M")).sum()
    )
    trade_number_in_month = same_month_count + 1
    trade_value_usd = float(shares) * float(price_usd)
    fee_thb = dime_fee_thb(trade_value_usd, float(fx_rate_thb))
    return trade_number_in_month, fee_thb


def add_transaction(
    date: str,
    ticker: str,
    shares: float,
    price_usd: float,
    fx_rate_thb: float,
    amount_thb: float,
    note: str = "",
) -> dict[str, object]:
    """บันทึกรายการซื้อใหม่ลง CSV; คืนรายการที่บันทึก (มี ``tx_id`` สำหรับอ้างอิง/ลบ)."""
    if not ticker or shares <= 0 or price_usd <= 0 or fx_rate_thb <= 0 or amount_thb <= 0:
        raise ValueError("ticker, shares, price_usd, fx_rate_thb และ amount_thb ต้องมีค่ามากกว่า 0")

    _ensure_storage()
    trade_number_in_month, fee_thb = estimate_dime_fee_thb(
        trade_date=date,
        shares=float(shares),
        price_usd=float(price_usd),
        fx_rate_thb=float(fx_rate_thb),
    )

    row = {
        "tx_id": str(uuid.uuid4()),
        "date": pd.to_datetime(date).strftime("%Y-%m-%d"),
        "ticker": ticker.strip().upper(),
        "shares": float(shares),
        "price_usd": float(price_usd),
        "fx_rate_thb": float(fx_rate_thb),
        "amount_thb": float(amount_thb),
        "fee_thb": float(fee_thb),
        "note": note.strip(),
        "tx_type": TX_BUY,
    }
    pd.DataFrame([row], columns=CSV_COLUMNS).to_csv(
        TRANSACTIONS_FILE,
        mode="a",
        header=False,
        index=False,
    )
    return row


def add_dividend(
    date: str,
    ticker: str,
    amount_usd: float,
    fx_rate_thb: float,
    note: str = "",
) -> dict[str, object]:
    """บันทึกปันผลที่ได้รับจริง (Roadmap Phase 2 ข้อ 5).

    ``amount_usd`` = ยอด **สุทธิ** ที่เข้าบัญชีจริง (โบรกหักภาษี ณ ที่จ่าย 15% แล้ว)
    — บันทึกตามที่รับจริง ไม่คำนวณกลับ; ยอด gross/ภาษีเป็นชั้นแสดงผล (portfolio/costs.py)
    แถวปันผล: shares=0, price_usd=0 → ไม่กระทบ cost basis และไม่นับเป็นเทรด
    """
    if not ticker or amount_usd <= 0 or fx_rate_thb <= 0:
        raise ValueError("ticker, amount_usd และ fx_rate_thb ต้องมีค่ามากกว่า 0")

    _ensure_storage()
    row = {
        "tx_id": str(uuid.uuid4()),
        "date": pd.to_datetime(date).strftime("%Y-%m-%d"),
        "ticker": ticker.strip().upper(),
        "shares": 0.0,
        "price_usd": 0.0,
        "fx_rate_thb": float(fx_rate_thb),
        "amount_thb": float(amount_usd) * float(fx_rate_thb),
        "fee_thb": 0.0,
        "note": note.strip(),
        "tx_type": TX_DIVIDEND,
    }
    pd.DataFrame([row], columns=CSV_COLUMNS).to_csv(
        TRANSACTIONS_FILE,
        mode="a",
        header=False,
        index=False,
    )
    return row


def get_dividends(ticker: str | None = None) -> pd.DataFrame:
    """แถวปันผลจาก ledger (ใหม่→เก่า); เพิ่มคอลัมน์ ``amount_usd`` (สุทธิ ณ วันรับ)."""
    transactions = _load_transactions()
    reports = _reports_of(transactions)
    dividends = transactions[transactions["tx_type"] == TX_DIVIDEND].copy()
    if ticker:
        dividends = dividends[dividends["ticker"] == ticker.strip().upper()]
    if dividends.empty:
        return _with_reports(dividends, reports)
    dividends["amount_usd"] = dividends["amount_thb"] / dividends["fx_rate_thb"]
    result = dividends.sort_values("date", ascending=False).reset_index(drop=True)
    return _with_reports(result, reports)


def get_dividend_summary() -> dict[str, object]:
    """สรุปปันผลสุทธิที่รับจริงทั้งหมด (THB/USD) รวมและรายปีปัจจุบัน.

    ถ้าธุรกรรมบางแถวข้อมูลไม่ครบจนคิดเงินไม่ได้ แถวนั้นไม่อยู่ในยอดรวมข้างบน
    และอยู่ใน ``skipped_rows`` + ``skipped_reason`` **คีย์ชุดเดียวกับ**
    ``get_total_summary()`` — ยอดปันผลที่น้อยกว่าจริงต้องมีคำเตือนกำกับเสมอ
    ห้ามตัดแถวเงียบ ๆ (รอบเก็บกวาด C1)

    รายงานถูกกรองเหลือ **เฉพาะแถวปันผล** (C1.4) — เดิมยกรายงานของทั้งสมุดมาวาง
    ใต้หัวข้อ "ปันผลรับจริง" ไม้ซื้อที่ถูกตัดจึงถูกอ่านว่า "ปันผลที่บันทึกไว้หายไป"
    และคำเตือนเดียวกันโผล่ซ้ำสองที่ในหน้าเดียว · แถวที่ถูกตัดของประเภทอื่นยังอยู่ครบ
    ที่ ``get_total_summary()`` ตามเดิม (กรอง ไม่ใช่กลืน)
    """
    dividends = get_dividends()
    report = _report_keys(_reports_of(dividends), tx_type=TX_DIVIDEND)
    if dividends.empty:
        return {
            "total_thb": 0.0,
            "total_usd": 0.0,
            "count": 0,
            "this_year_thb": 0.0,
            "by_ticker_thb": {},
            **report,
        }
    this_year = dividends[dividends["date"].dt.year == pd.Timestamp.today().year]
    return {
        "total_thb": float(dividends["amount_thb"].sum()),
        "total_usd": float(dividends["amount_usd"].sum()),
        "count": int(len(dividends)),
        "this_year_thb": float(this_year["amount_thb"].sum()),
        "by_ticker_thb": dividends.groupby("ticker")["amount_thb"].sum().to_dict(),
        **report,
    }


def delete_transaction(tx_id: str) -> bool:
    """ลบธุรกรรมตาม ``tx_id``; คืน True ถ้าลบสำเร็จ."""
    target = str(tx_id).strip()
    if not target:
        return False

    _ensure_storage()
    df = pd.read_csv(TRANSACTIONS_FILE)
    if df.empty or "tx_id" not in df.columns:
        return False

    keep = df["tx_id"].astype(str).str.strip() != target
    if bool(keep.all()):
        return False

    df[keep].to_csv(TRANSACTIONS_FILE, index=False)
    return True


def get_portfolio_summary() -> pd.DataFrame:
    """สรุปพอร์ตปัจจุบันรายสินทรัพย์ พร้อม P&L และ % Return.

    คิดจากรายการซื้อเท่านั้น — ปันผล (tx_type=dividend) ไม่เข้า cost basis/จำนวนหุ้น
    (P&L ที่ได้จึงเป็นกำไรจากราคาล้วน; รายรับปันผลดูจาก ``get_dividend_summary``)
    """
    transactions = _load_transactions()
    reports = _reports_of(transactions)
    if "tx_type" in transactions.columns:
        transactions = transactions[transactions["tx_type"] != TX_DIVIDEND]
    if transactions.empty:
        return _with_reports(
            pd.DataFrame(
                columns=[
                    "Ticker",
                    "Shares",
                    "Avg Cost (USD)",
                    "Current Price (USD)",
                    "Invested (USD)",
                    "Invested (THB)",
                    "Current Value (USD)",
                    "Current Value (THB)",
                    "FX Rate (Buy)",
                    "Fee (THB)",
                    "P&L (USD)",
                    "P&L (THB)",
                    "Return (%)",
                    "Price OK",
                ]
            ),
            reports,
        )

    transactions["cost_usd"] = transactions["shares"] * transactions["price_usd"]
    transactions["cost_thb"] = transactions["cost_usd"] * transactions["fx_rate_thb"]
    transactions["fx_cost_weight"] = transactions["fx_rate_thb"] * transactions["cost_usd"]
    grouped = (
        transactions.groupby("ticker", as_index=False)
        .agg(
            shares=("shares", "sum"),
            invested_usd=("cost_usd", "sum"),
            invested_thb=("cost_thb", "sum"),
            fx_weight_sum=("fx_cost_weight", "sum"),
            total_fee_thb=("fee_thb", "sum"),
        )
        .sort_values("ticker")
    )
    grouped["avg_cost_usd"] = grouped["invested_usd"] / grouped["shares"]
    grouped["fx_rate_buy"] = grouped["fx_weight_sum"] / grouped["invested_usd"]

    tickers = grouped["ticker"].tolist()
    latest_prices = _get_latest_prices(tickers)
    # อัตราเดียวกับที่รายงานออกไป — ห้ามดึงซ้ำคนละครั้งกับที่คูณเข้ามูลค่าจริง (B9)
    fx_quote = _get_fx_quote()
    fx_rate = fx_quote[0]

    # ราคาที่ดึงไม่ได้ต้องเป็น NaN — เดิม fillna(0) ทำให้ P&L โชว์ -100% ปลอม (AUDIT.md C1)
    grouped["current_price_usd"] = grouped["ticker"].map(latest_prices)
    grouped["price_ok"] = grouped["current_price_usd"].notna()
    grouped["current_value_usd"] = grouped["shares"] * grouped["current_price_usd"]
    grouped["current_value_thb"] = grouped["current_value_usd"] * fx_rate
    grouped["pnl_usd"] = grouped["current_value_usd"] - grouped["invested_usd"]
    grouped["pnl_thb"] = grouped["current_value_thb"] - grouped["invested_thb"]
    grouped["return_pct"] = grouped["pnl_usd"] / grouped["invested_usd"] * 100.0

    return grouped.rename(
        columns={
            "ticker": "Ticker",
            "shares": "Shares",
            "avg_cost_usd": "Avg Cost (USD)",
            "current_price_usd": "Current Price (USD)",
            "invested_usd": "Invested (USD)",
            "invested_thb": "Invested (THB)",
            "current_value_usd": "Current Value (USD)",
            "current_value_thb": "Current Value (THB)",
            "fx_rate_buy": "FX Rate (Buy)",
            "total_fee_thb": "Fee (THB)",
            "pnl_usd": "P&L (USD)",
            "pnl_thb": "P&L (THB)",
            "return_pct": "Return (%)",
            "price_ok": "Price OK",
        }
    )[
        [
            "Ticker",
            "Shares",
            "Avg Cost (USD)",
            "Current Price (USD)",
            "Invested (USD)",
            "Invested (THB)",
            "Current Value (USD)",
            "Current Value (THB)",
            "FX Rate (Buy)",
            "Fee (THB)",
            "P&L (USD)",
            "P&L (THB)",
            "Return (%)",
            "Price OK",
        ]
    ].pipe(_with_reports, reports).pipe(_with_fx_source, fx_quote)


def get_total_summary() -> dict[str, object]:
    """สรุปภาพรวมพอร์ตทั้งหมดในหน่วย THB.

    **เงินลงทุนมีสองฐาน และต้องอ่านคู่กับป้ายของมันเสมอ** (AUDIT_2026-08-06 H9)

    - ``invested_thb_all`` — เงินที่จ่ายไปจริงทั้งหมด (ทุกแถวที่ใช้ได้)
    - ``invested_thb_priced`` — เฉพาะกองที่ดึงราคาปัจจุบันได้ = **ฐานเดียว**
      ที่ ``total_pnl_thb`` และ ``total_return_pct`` คิดมาจาก

    เดิมคืน ``total_invested_thb`` (ฐานแรก) คู่กับกำไรที่คิดจากฐานที่สอง ผู้ใช้จึงเห็น
    เลข 3 ตัวบนจอเดียวกันที่บวกลบกันไม่ลงตัว และ % ผลตอบแทน **สูงขึ้น** เมื่อดึงราคา
    ไม่ได้ (ราคาที่หายทำให้กองที่ขาดทุนหลุดออกจากตัวหาร) — ``total_invested_thb``
    ยังอยู่เป็นชื่อเดิมของ ``invested_thb_all`` เพื่อไม่ให้ผู้เรียกเดิมพัง
    แต่โค้ดใหม่ควรใช้ชื่อที่ติดป้ายชัด

    ``current_value_thb`` / ``total_pnl_thb`` / ``total_return_pct`` เป็น **NaN**
    เมื่อดึงราคาไม่ได้เลยสักกอง — "ไม่รู้มูลค่า" ห้ามกลายเป็น ``0.00`` ซึ่งอ่านได้ว่า
    "เท่าทุนพอดี" (``portfolio_service`` แปลงต่อเป็น ``None`` ให้ฝั่ง JSON)

    รายชื่อกองที่ดึงราคาไม่ได้อยู่ใน ``missing_prices``; ธุรกรรมที่ถูกตัด/ถูกซ่อม/
    ขัดกันเองอยู่ใน ``skipped_rows`` / ``derived_fx_rows`` / ``inconsistent_rows``
    พร้อมข้อความไทยคู่กัน — ผู้เรียกต้องแสดงทั้งหมด ห้ามตัดเงียบ ๆ

    ``fx_rate_thb`` / ``fx_is_live`` คืออัตราแลกเปลี่ยนที่ใช้แปลงมูลค่าวันนี้เป็นบาท
    และที่มาของมัน (AUDIT_2026-08-06 B9/C1.5) — ``False`` = ใช้ค่าสำรองจาก config
    (ตัวเลขบาททั้งก้อนคลาดเคลื่อน ต้องเตือนผู้ใช้แบบเดียวกับ ``missing_prices``)
    · ``None`` = ไม่ทราบที่มา/ไม่มีการแปลงค่าเงิน (สมุดว่าง)
    """
    holdings = get_portfolio_summary()
    report = _report_keys(_reports_of(holdings))
    fx_source = _fx_source_of(holdings)
    if holdings.empty:
        # สมุดว่าง = 0 คือคำตอบจริง (คนละเรื่องกับ "ดึงราคาไม่ได้")
        # ไม่มีการแปลงค่าเงินเกิดขึ้นเลย → fx_rate_thb/fx_is_live เป็น None (ไม่ใช่ 0/False)
        return {
            "invested_thb_all": 0.0,
            "invested_thb_priced": 0.0,
            "total_invested_thb": 0.0,
            "current_value_thb": 0.0,
            "total_pnl_thb": 0.0,
            "total_return_pct": 0.0,
            "total_fee_thb": 0.0,
            "missing_prices": [],
            **fx_source,
            **report,
        }

    ok = holdings[holdings["Price OK"]]
    missing_prices = holdings.loc[~holdings["Price OK"], "Ticker"].astype(str).tolist()

    invested_all = float(holdings["Invested (THB)"].sum())
    invested_priced = float(ok["Invested (THB)"].sum())
    if ok.empty:
        # ไม่มีราคาเลยสักกอง → มูลค่า/กำไร/% "ไม่รู้" ห้ามเป็น 0.0
        current_value = float("nan")
        total_pnl = float("nan")
        total_return_pct = float("nan")
    else:
        current_value = float(ok["Current Value (THB)"].sum())
        total_pnl = current_value - invested_priced
        total_return_pct = (
            total_pnl / invested_priced * 100.0 if invested_priced else float("nan")
        )
    total_fee_thb = float(holdings["Fee (THB)"].sum())
    return {
        "invested_thb_all": invested_all,
        "invested_thb_priced": invested_priced,
        # ชื่อเดิม = invested_thb_all (ยอดที่จ่ายไปจริงทั้งหมด) — ผู้เรียกเก่ายังอ่านได้
        "total_invested_thb": invested_all,
        "current_value_thb": current_value,
        "total_pnl_thb": total_pnl,
        "total_return_pct": total_return_pct,
        "total_fee_thb": total_fee_thb,
        "missing_prices": missing_prices,
        **fx_source,
        **report,
    }


def get_transactions(ticker: str | None = None) -> pd.DataFrame:
    """ดึงประวัติการซื้อขายทั้งหมด หรือกรองตาม ticker.

    แถวที่ข้อมูลไม่ครบไม่อยู่ในผลลัพธ์ แต่รายงานไว้ที่ ``.attrs['skipped_rows']``
    """
    transactions = _load_transactions()
    reports = _reports_of(transactions)
    if ticker:
        ticker_upper = ticker.strip().upper()
        transactions = transactions[transactions["ticker"] == ticker_upper]
    result = transactions.sort_values("date", ascending=False).reset_index(drop=True)
    return _with_reports(result, reports)
