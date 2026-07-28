"""SQLite ของ backend (goals / net worth / reports / config).

path ตั้งค่าได้ด้วย ``VAULTIS_DB_PATH`` — ค่าเริ่มต้นคือ ``./vaultis.db`` เท่าเดิม
เพื่อให้การรันบนเครื่องตัวเองไม่เปลี่ยนพฤติกรรม

เหตุผลที่ต้องตั้งได้: เดิม path ผูกกับ working directory ตายตัว พอรันใน container
ไฟล์จะไปอยู่ใน image layer แล้วหายทุกครั้งที่ rebuild — ต้องชี้ไปยัง volume ได้
"""

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

_DEFAULT_DB_PATH = "./vaultis.db"

_db_path = (os.getenv("VAULTIS_DB_PATH") or _DEFAULT_DB_PATH).strip() or _DEFAULT_DB_PATH
# โฟลเดอร์ปลายทางต้องมีก่อน ไม่งั้น sqlite โยน "unable to open database file"
Path(_db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{_db_path}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
