"""บันทึก/อ่านประวัติผล screener บน PostgreSQL (ไม่ตั้ง ``DATABASE_URL`` = ข้ามเงียบ ๆ)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from backend.screener.models import ScreenerResult

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

# DDL เคยเป็นแค่คอมเมนต์บนหัวไฟล์ ไม่มีโค้ดไหนสร้างตารางให้เลย — ฐานใหม่ทุกใบจึงเก็บ
# ประวัติไม่ได้ แล้วไปโผล่เป็น `save_results error: relation ... does not exist` ในล็อก
# เท่านั้น (`scripts/init_db.py` เรียกฟังก์ชันด้านล่างแล้ว)
# ``created_at`` ต้องเป็น TIMESTAMPTZ — เดิมเป็น ``TIMESTAMP`` (timestamp without time
# zone) แล้วเก็บผลของ ``NOW()`` ในคอนเทนเนอร์ที่ตั้ง ``TZ=Asia/Bangkok`` ⇒ ค่าที่
# driver คืนกลับมาเป็น datetime ไร้ tz ที่จริง ๆ แล้วเป็นเวลาไทย ผู้อ่านนำไปเทียบกับ
# cutoff แบบ aware ไม่ได้ (``TypeError: can't compare offset-naive and offset-aware``)
# ⇒ รายงานรายเดือนตายทั้งงานทันทีที่มีสัญญาณแรก (AUDIT_2026-08-06 H3)
SCREENER_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS screener_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    symbol VARCHAR(20),
    preset_name VARCHAR(50),
    matched_rules TEXT,
    price FLOAT,
    signal_strength FLOAT,
    created_at TIMESTAMPTZ DEFAULT now()
)
"""

SCREENER_HISTORY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_screener_history_symbol_created_at
    ON screener_history (symbol, created_at)
"""

# timezone ที่ค่าเก่า (คอลัมน์ไร้ tz) ถูกบันทึกไว้จริง — คอนเทนเนอร์ postgres ตั้ง
# ``TZ: Asia/Bangkok`` ใน docker-compose.yml จึงต้องตีความค่าเก่าเป็นเวลาไทย
# ผู้อ่านที่ยังเจอ datetime ไร้ tz (ฐานเก่าที่ยังไม่ได้ migrate) ใช้ค่านี้เช่นกัน
LEGACY_NAIVE_TZ = "Asia/Bangkok"

SCREENER_HISTORY_TZ_MIGRATION_DDL = f"""
ALTER TABLE screener_history
    ALTER COLUMN created_at TYPE TIMESTAMPTZ
    USING created_at AT TIME ZONE '{LEGACY_NAIVE_TZ}'
"""

_CREATED_AT_TYPE_SQL = """
SELECT data_type FROM information_schema.columns
WHERE table_name = 'screener_history' AND column_name = 'created_at'
"""

# ไม่ได้ตั้ง DATABASE_URL = ระบบไม่ได้เปิดใช้ประวัติ ไม่ใช่ความล้มเหลว
# (CLAUDE.md: ``DATABASE_URL`` เป็น optional) แต่ก็ไม่ใช่ "ไม่มีสัญญาณ" เช่นกัน
HISTORY_OFF_REASON = "ไม่ได้ตั้ง DATABASE_URL — ระบบไม่ได้เก็บประวัติ screener ไว้"


def create_screener_history_table() -> None:
    """สร้างตาราง screener_history; ไม่ตั้ง ``DATABASE_URL`` = โยนออกไปให้ผู้เรียกเห็น.

    ตารางที่สร้างไว้ก่อนหน้านี้จะถูก migrate ``created_at`` เป็น ``TIMESTAMPTZ``
    โดยตีความค่าเดิมเป็นเวลาไทย (ตรงกับ ``TZ`` ของคอนเทนเนอร์ที่เขียนค่าเหล่านั้น)
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set in .env")
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text(SCREENER_HISTORY_DDL))
        current_type = conn.execute(text(_CREATED_AT_TYPE_SQL)).scalar()
        if current_type == "timestamp without time zone":
            print(
                "[screener_history] migrate created_at → TIMESTAMPTZ "
                f"(ตีความค่าเดิมเป็น {LEGACY_NAIVE_TZ})"
            )
            conn.execute(text(SCREENER_HISTORY_TZ_MIGRATION_DDL))
        conn.execute(text(SCREENER_HISTORY_INDEX_DDL))
    engine.dispose()


class ScreenerHistoryService:
    def __init__(self) -> None:
        self.engine: Engine | None = None
        if DATABASE_URL:
            # Expects a psycopg2-backed URL like postgresql+psycopg2://...
            self.engine = create_engine(DATABASE_URL, pool_pre_ping=True)

    async def save_results(self, results: list[ScreenerResult], preset_name: str):
        if self.engine is None or not results:
            return

        insert_sql = text(
            """
            INSERT INTO screener_history
                (symbol, preset_name, matched_rules, price, signal_strength, created_at)
            VALUES
                (:symbol, :preset_name, :matched_rules, :price, :signal_strength, NOW())
            """
        )

        try:
            with self.engine.begin() as conn:
                for result in results:
                    conn.execute(
                        insert_sql,
                        {
                            "symbol": result.symbol,
                            "preset_name": preset_name,
                            "matched_rules": json.dumps(result.matched_rules),
                            "price": result.price,
                            "signal_strength": result.signal_strength,
                        },
                    )
        except Exception as e:
            print(f"[screener_history] save_results error: {e}")

    async def get_history_with_status(
        self, symbol: str = None, limit: int = 50
    ) -> tuple[list[dict], dict]:
        """ประวัติ + สถานะของแหล่งข้อมูล.

        คืน ``(rows, {"status": "ok"|"error"|"off", "detail": str})``
        — ``off`` = ไม่ได้ตั้ง ``DATABASE_URL`` (ไม่ใช่ความล้มเหลว แต่ก็ไม่ใช่
        "ไม่มีสัญญาณ") · ``error`` = ต่อฐานไม่ได้/คิวรีพัง ซึ่ง **ห้าม**ถูกอ่านเป็น
        ลิสต์ว่างธรรมดา ไม่งั้นรายงานรายเดือนจะพิมพ์ "0 รายการ" ทั้งที่ยังไม่รู้เลย
        ว่ามีกี่รายการ (AUDIT_2026-08-06 M-R3 · กฎ "ดึงไม่สำเร็จ ≠ ไม่มีข้อมูล")
        """
        if self.engine is None:
            return [], {"status": "off", "detail": HISTORY_OFF_REASON}

        try:
            query = """
                SELECT id, symbol, preset_name, matched_rules, price, signal_strength, created_at
                FROM screener_history
            """
            params: dict = {"limit": limit}
            if symbol:
                query += " WHERE symbol = :symbol"
                params["symbol"] = symbol
            query += " ORDER BY created_at DESC LIMIT :limit"

            with self.engine.begin() as conn:
                rows = conn.execute(text(query), params).mappings().all()

            return [dict(row) for row in rows], {"status": "ok", "detail": ""}
        except Exception as e:
            print(f"[screener_history] get_history error: {e}")
            return [], {"status": "error", "detail": str(e)}

    async def get_history(self, symbol: str = None, limit: int = 50) -> list[dict]:
        """เฉพาะรายการ — ผู้เรียกที่ต้องแยก "ดึงไม่สำเร็จ" ออกจาก "ไม่มีข้อมูล"
        ให้ใช้ :meth:`get_history_with_status` (สำนวนเดียวกับ ``analysis/news_fetcher``)
        """
        rows, _status = await self.get_history_with_status(symbol=symbol, limit=limit)
        return rows
