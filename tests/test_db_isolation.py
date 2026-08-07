# -*- coding: utf-8 -*-
"""ชุดเทสต์ต้องไม่เห็นฐาน SQLite จริงของผู้ใช้ (AUDIT_2026-08-06 ข้อ 0-A / H1).

ที่มา: `docker-compose.yml` service `tests` เคย mount `./.docker-data:/data`
พร้อมตั้ง `VAULTIS_DB_PATH=/data/vaultis.db` — อะไรก็ตามที่รันในชุดเทสต์แล้วแตะ
`backend.database.SessionLocal` จึงเขียนทะลุถึงไฟล์บน host ได้ทันที และเคยเขียนไปแล้วจริง
(2 แถวขยะใน `investment_goals` และ `networth_snapshots` ลงวันที่ 2026-08-06 14:06 UTC)

เทสต์ชุดนี้เป็นตาข่ายกันไม่ให้ใครเผลอเติม mount/ค่า env กลับเข้ามา — ตรวจสองชั้น:
1. ชั้นรันจริง: path ที่ `backend.database` ใช้อยู่ ต้องไม่อยู่ใต้ `/data`
   และไฟล์ฐานจริงต้องมองไม่เห็นจากในกระบวนการเทสต์
2. ชั้นไฟล์ตั้งค่า: บล็อก service `tests` ใน `docker-compose.yml` ต้องไม่ผูก
   `./.docker-data` และต้องตั้ง `VAULTIS_DB_PATH` ออกนอก `/data`
"""

from pathlib import Path

import pytest

import backend.database as db

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# path ที่ถูก mount เป็นฐานจริง (`./.docker-data` บน host → `/data` ในคอนเทนเนอร์)
REAL_DATA_MOUNT = Path("/data")


def _is_relative_to(path: Path, parent: Path) -> bool:
    """เทียบว่า ``path`` อยู่ใต้ ``parent`` หรือไม่ (ไม่พึ่ง Path.is_relative_to)."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------- ชั้นรันจริง

def test_db_path_ไม่อยู่ใต้_data_mount():
    """`backend.database._db_path` ต้องไม่ชี้เข้าไปในฐานจริงที่ mount ไว้."""
    resolved = Path(db._db_path).expanduser().resolve()
    assert not _is_relative_to(resolved, REAL_DATA_MOUNT), (
        f"ชุดเทสต์กำลังชี้ SQLite ไปที่ {resolved} ซึ่งอยู่ใต้ {REAL_DATA_MOUNT} "
        "= ฐานจริงของผู้ใช้ที่ mount มาจาก ./.docker-data — "
        "ตั้ง VAULTIS_DB_PATH ให้ออกนอก /data และห้ามผูก volume นั้นกับ service tests"
    )


def test_engine_url_ไม่ชี้ฐานจริง():
    """URL ที่ SQLAlchemy ใช้จริงต้องสอดคล้องกัน — กันกรณีแก้ตัวแปรผิดตัว."""
    assert "/data/vaultis.db" not in db.DATABASE_URL, (
        f"engine ของชุดเทสต์ต่อไปที่ {db.DATABASE_URL} = ฐานจริงของผู้ใช้"
    )


def test_ฐานจริงต้องมองไม่เห็นจากในชุดเทสต์():
    """ไฟล์ `/data/vaultis.db` ต้องไม่ปรากฏเลย — ถ้าเห็น แปลว่า mount กลับมาแล้ว.

    นอก Docker path นี้ไม่มีอยู่แล้ว เทสต์จึงผ่านโดยปริยาย
    """
    real_db = REAL_DATA_MOUNT / "vaultis.db"
    assert not real_db.exists(), (
        f"{real_db} มองเห็นได้จากกระบวนการเทสต์ — service tests ยัง mount "
        "./.docker-data:/data อยู่ ให้ถอด volume นั้นออกจาก docker-compose.yml"
    )


# ------------------------------------------------------- ชั้นไฟล์ตั้งค่า compose

def _tests_service_block(*, keep_comments: bool = False) -> str:
    """คืนบล็อกข้อความของ service ``tests`` จาก docker-compose.yml.

    ไม่ใช้ PyYAML เพราะ image เทสต์ไม่ได้ติดตั้งไว้ (ยืนยันแล้ว: import yaml ไม่ผ่าน)
    จึงตัดบล็อกด้วยระดับ indent แทน

    ดีฟอลต์ตัดบรรทัดคอมเมนต์ทิ้ง — คอมเมนต์ที่อธิบายว่า "ห้าม mount ./.docker-data"
    ต้องไม่ถูกนับเป็นการ mount เสียเอง
    """
    lines = COMPOSE_FILE.read_text(encoding="utf-8").splitlines()
    if not keep_comments:
        lines = [line for line in lines if not line.lstrip().startswith("#")]
    start = None
    indent = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped in ("tests:", "tests: "):
            start = i
            indent = len(line) - len(line.lstrip())
            break
    assert start is not None, "หา service `tests` ใน docker-compose.yml ไม่เจอ"

    block = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.strip():
            block.append(line)
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    return "\n".join(block)


def test_compose_service_tests_ไม่ผูกฐานจริง():
    """service `tests` ต้องไม่ mount `./.docker-data` เข้าคอนเทนเนอร์."""
    block = _tests_service_block()
    assert ".docker-data" not in block, (
        "docker-compose.yml service `tests` ยังผูก ./.docker-data อยู่ — "
        "ชุดเทสต์จะเขียนทับฐาน goals / net worth / monthly reports ตัวจริงของผู้ใช้\n"
        f"--- บล็อกที่อ่านได้ ---\n{block}"
    )


def test_compose_service_tests_ตั้ง_db_path_ออกนอก_data():
    """`VAULTIS_DB_PATH` ของ service `tests` ต้องถูกเขียนทับให้ออกนอก `/data`.

    จำเป็นเพราะ Dockerfile ตั้ง ``VAULTIS_DB_PATH=/data/vaultis.db`` ไว้ใน image
    ถ้า compose ไม่เขียนทับ ค่านั้นจะติดมาเองแม้ถอด volume ออกแล้ว
    """
    block = _tests_service_block()
    values = [
        line.split(":", 1)[1].strip().strip("\"'")
        for line in block.splitlines()
        if line.strip().startswith("VAULTIS_DB_PATH:")
    ]
    assert values, (
        "service `tests` ไม่ได้ตั้ง VAULTIS_DB_PATH — จะตกไปใช้ค่าจาก Dockerfile "
        "(/data/vaultis.db) ซึ่งคือฐานจริงของผู้ใช้"
    )
    for value in values:
        assert not _is_relative_to(Path(value), REAL_DATA_MOUNT), (
            f"service `tests` ตั้ง VAULTIS_DB_PATH={value} ซึ่งอยู่ใต้ {REAL_DATA_MOUNT}"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
