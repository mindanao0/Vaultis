# -*- coding: utf-8 -*-
"""ลบเป้าหมายการลงทุนที่ ``target_date`` ไม่ใช่วันที่จริง (แถวขยะจากการทดสอบ API).

    python scripts/fix_goals.py            # dry-run: บอกว่าจะลบแถวไหน ไม่ลบจริง
    python scripts/fix_goals.py --apply    # สำรองไฟล์ฐานก่อน แล้วลบจริง

สามอย่างที่แก้จากเวอร์ชันเดิม (AUDIT_2026-08-06 D3.4):

1. เดิมเปิด ``sqlite3.connect("vaultis.db")`` เป็น path **สัมพัทธ์** — รันจาก
   โฟลเดอร์ที่ไม่มีไฟล์ sqlite จะสร้างฐานเปล่า 0 ไบต์ให้แล้วตายด้วย
   ``OperationalError`` ส่วนฐานจริงที่ ``VAULTIS_DB_PATH`` ชี้อยู่ไม่ถูกแตะเลย
   ตอนนี้อ่าน path จาก ``backend.database`` แหล่งเดียว และเปิดเฉพาะไฟล์ที่มีอยู่จริง
2. เดิม ``DELETE`` ทันทีที่รัน โดยไม่มี dry-run / ไม่ยืนยัน / ไม่สำรอง
   ตอนนี้ดีฟอลต์คือ dry-run และ ``--apply`` จะคัดลอกไฟล์ฐานเป็น ``.bak-<เวลา>`` ก่อนลบ
3. เดิมเทียบ ``target_date = 'string'`` ซึ่งเป็นค่าคงที่ที่หลุดมาจากการ debug
   (ตรงกับแถวที่ generate จาก openapi schema เท่านั้น) ตอนนี้ตัดสินจากสิ่งที่
   docstring เดิมตั้งใจไว้จริง ๆ คือ "แปลงเป็นวันที่ไม่ได้"
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.engine import make_url  # noqa: E402

from backend.database import DATABASE_URL  # noqa: E402


def resolve_db_path() -> Path:
    """path ของไฟล์ SQLite ที่ backend ใช้จริง (ตาม ``VAULTIS_DB_PATH``)."""
    return Path(make_url(DATABASE_URL).database).expanduser().resolve()


def is_valid_target_date(raw: object) -> bool:
    """``target_date`` ที่ใช้ได้ = แปลงเป็นวันที่ได้จริง (ค่าว่าง/None ถือว่าใช้ไม่ได้)."""
    if raw is None:
        return False
    if isinstance(raw, (date, datetime)):
        return True
    text = str(raw).strip()
    if not text:
        return False
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


def _backup(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="ลบจริง (ดีฟอลต์คือ dry-run) — จะสำรองไฟล์ฐานให้ก่อนเสมอ",
    )
    args = parser.parse_args(argv)

    db_path = resolve_db_path()
    print(f"ฐานข้อมูล: {db_path}")
    if not db_path.exists():
        print(
            f"ไม่พบไฟล์ฐานข้อมูล {db_path} — ไม่ทำอะไรทั้งสิ้น "
            "(ตั้ง VAULTIS_DB_PATH ให้ตรงกับที่ backend ใช้ แล้วลองใหม่)",
            file=sys.stderr,
        )
        return 1

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM investment_goals").fetchall()
        except sqlite3.OperationalError as exc:
            # ไม่มีตาราง ≠ ไม่มีเป้าหมายที่ต้องลบ — บอกให้ชัดว่าเปิดฐานผิดตัวหรือยังไม่ init
            print(
                f"อ่านตาราง investment_goals จาก {db_path} ไม่ได้: {exc}\n"
                "— ตรวจว่า VAULTIS_DB_PATH ชี้ไปที่ฐานที่ backend ใช้จริงหรือไม่",
                file=sys.stderr,
            )
            return 1
        bad = [row for row in rows if not is_valid_target_date(row["target_date"])]

        print(f"เป้าหมายทั้งหมด {len(rows)} แถว · target_date ใช้ไม่ได้ {len(bad)} แถว")
        for row in bad:
            print(f"  - id={row['id']} name={row['name']!r} target_date={row['target_date']!r}")

        if not bad:
            print("ไม่มีอะไรต้องลบ")
            return 0

        if not args.apply:
            print("(dry-run — ยังไม่ลบอะไร) สั่งลบจริงด้วย: python scripts/fix_goals.py --apply")
            return 0

        backup_path = _backup(db_path)
        print(f"สำรองไฟล์ฐานไว้ที่ {backup_path}")
        conn.executemany(
            "DELETE FROM investment_goals WHERE id = ?", [(row["id"],) for row in bad]
        )
        conn.commit()
        print(f"ลบแล้ว {len(bad)} แถว")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
