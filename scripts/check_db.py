# -*- coding: utf-8 -*-
"""ดูว่าฐาน SQLite ของ backend มีตารางอะไรและกี่แถว.

    python scripts/check_db.py

เดิมสคริปต์นี้เปิด ``sqlite3.connect("vaultis.db")`` ซึ่งเป็น path **สัมพัทธ์**
รันจากโฟลเดอร์ไหนก็ไปเปิดไฟล์ของโฟลเดอร์นั้น — และเพราะ sqlite สร้างไฟล์ให้เอง
เมื่อไม่มี ผลลัพธ์คือ "=== Tables ===" ว่างเปล่า (อ่านได้ว่า "ฐานไม่มีอะไรเลย")
พร้อมทิ้งไฟล์ขนาด 0 ไบต์ไว้ ทั้งที่ฐานจริงที่ ``VAULTIS_DB_PATH`` ชี้อยู่มีข้อมูลครบ
— AUDIT_2026-08-06 D3.5 ("ดึงไม่สำเร็จ" ≠ "ไม่มีข้อมูล")

ตอนนี้อ่าน path จาก ``backend.database`` แหล่งเดียว, เปิดแบบอ่านอย่างเดียว
(``mode=ro`` — ไม่สร้างไฟล์ใหม่เด็ดขาด) และพิมพ์ path ที่เปิดจริงเสมอ
"""

import os
import sqlite3
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.engine import make_url  # noqa: E402

from backend.database import DATABASE_URL  # noqa: E402


def resolve_db_path() -> Path:
    """path ของไฟล์ SQLite ที่ backend ใช้จริง (ตาม ``VAULTIS_DB_PATH``)."""
    return Path(make_url(DATABASE_URL).database).expanduser().resolve()


def main() -> int:
    db_path = resolve_db_path()
    print(f"ฐานข้อมูล: {db_path}")

    if not db_path.exists():
        print(
            f"ไม่พบไฟล์ฐานข้อมูล {db_path}\n"
            "— นี่คือ 'เปิดไม่ได้' ไม่ใช่ 'ฐานว่าง' "
            "(ตั้ง VAULTIS_DB_PATH ให้ตรงกับที่ backend ใช้ แล้วลองใหม่)",
            file=sys.stderr,
        )
        return 1

    # uri=True + mode=ro: อ่านอย่างเดียว และห้าม sqlite สร้างไฟล์ใหม่ให้เอง
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]

        print("=== Tables ===")
        if not tables:
            print("(ไม่มีตารางในไฟล์นี้ — รัน backend หนึ่งครั้งเพื่อให้สร้างตาราง)")
            return 0
        for table in tables:
            # ชื่อตารางมาจาก sqlite_master ของฐานตัวเอง ไม่ใช่ input ผู้ใช้
            count = cursor.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            print(f"- {table}: {count} rows")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
