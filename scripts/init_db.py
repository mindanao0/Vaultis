"""สร้างตารางบน PostgreSQL ให้ครบทั้ง 3 ตาราง (sentiment 2 + ประวัติ screener 1).

    python scripts/init_db.py

อ่าน ``DATABASE_URL`` จาก .env — รันซ้ำได้ ไม่ลบข้อมูลเดิม (CREATE TABLE IF NOT EXISTS)
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.screener.history_service import create_screener_history_table
from db.sentiment_models import create_tables

if __name__ == "__main__":
    create_tables()
    print("sentiment_results / sentiment_summary: พร้อมใช้งาน")
    create_screener_history_table()
    print("screener_history: พร้อมใช้งาน")
