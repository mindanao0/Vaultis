# -*- coding: utf-8 -*-
"""ชุดเทสต์/โพรบ ต้องเขียนทับไฟล์ข้อมูลจริงของผู้ใช้ไม่ได้
(AUDIT_ROUND2_2026-08-07 ข้อ HIGH "ไฟล์ price alert จริงของผู้ใช้หายไป").

ที่มา: รอบ 0-A ปิดไปแล้ว **เฉพาะฐาน SQLite** (``tests/test_db_isolation.py``) แต่คำสั่ง
รันเทสต์จริงคือ ``docker compose --profile dev run --rm -v "$PWD:/app" tests`` —
repo ทั้งก้อนถูก mount ทับ ``/app`` ⇒ ``alerts/data/price_alerts.json`` และ
``portfolio/data/transactions.csv`` ยังเขียนทะลุถึงไฟล์บน host ได้ทันทีที่ใครเรียก
ฟังก์ชันเซฟโดยไม่ stub path (เกิดขึ้นจริงคืน 2026-08-07: โพรบตัวหนึ่งเรียก
``_save_alerts``/``delete_transaction`` ตรง ๆ แล้วคลัง alert ของผู้ใช้ถูกล้างเป็น
``{"alerts": []}``) ทั้งสองไฟล์ถูก gitignore จึงไม่มีสำเนาใน git ให้กู้

โครงเดียวกับ ``tests/test_db_isolation.py`` — ตรวจสามชั้น:

1. **ชั้น path ตอนรัน** — ค่าที่โมดูลถืออยู่ต้องไม่ใช่ไฟล์จริงของผู้ใช้
2. **ชั้นพฤติกรรม** — เรียกฟังก์ชันเซฟ *ตัวจริง* แล้วไฟล์ของผู้ใช้ต้องไม่ขยับสักไบต์
   (นี่คือข้อพิสูจน์ที่ตรงกับอาการ ไม่ใช่แค่ยืนยันค่าตัวแปร)
3. **ชั้นไฟล์ตั้งค่า** — service ``tests`` ใน ``docker-compose.yml`` ต้องตั้ง
   ``VAULTIS_LEDGER_PATH`` / ``VAULTIS_ALERTS_PATH`` ออกไปนอก repo

ตาข่ายที่ทำให้ชั้น 1–2 เป็นจริงแม้ **ลืมตั้ง env** อยู่ใน ``tests/conftest.py``
(fixture autouse ``_isolate_user_data_files``) — ชั้น 3 เป็นตัวบอกว่า "ตั้งค่าครบไหม"
ส่วน conftest เป็นตัวบอกว่า "ต่อให้ตั้งไม่ครบก็เขียนไม่โดน"
"""

import hashlib
import os
from pathlib import Path

import pytest

import alerts.price_alert as pa
import portfolio.tracker as tracker
import utils.config as cfg

# ด่านตัวจริงอยู่ใน conftest — เทสต์ชั้น "ตาข่ายทำงานไหม" เรียกฟังก์ชันเดียวกันนั้นตรง ๆ
import conftest

# ตัวแยกบล็อก service `tests` ออกจาก docker-compose.yml มีที่เดียว — ใช้ซ้ำ ห้ามเขียนใหม่
from test_db_isolation import _is_relative_to, _tests_service_block

REPO_ROOT = Path(__file__).resolve().parent.parent

REAL_ALERTS = REPO_ROOT / "alerts" / "data" / "price_alerts.json"
REAL_LEDGER = REPO_ROOT / "portfolio" / "data" / "transactions.csv"
REAL_CONFIG = REPO_ROOT / "config.json"

# path ของ repo ในคอนเทนเนอร์ทดสอบ (คำสั่งรันจริง mount `$PWD` มาที่นี่)
CONTAINER_REPO_MOUNT = Path("/app")


def _fingerprint(path: Path) -> tuple[int, int, str] | None:
    """ลายนิ้วมือไฟล์ — ``None`` = ไม่มีไฟล์ (ซึ่งก็เป็นสถานะที่ต้องไม่เปลี่ยนเหมือนกัน).

    เก็บทั้ง mtime ขนาด และ sha256: "เขียนทับด้วยเนื้อหาเดิม" ก็ยังเป็นการเขียน
    ไฟล์ของผู้ใช้ซึ่งไม่ควรเกิดจากชุดเทสต์
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_mtime_ns, stat.st_size, hashlib.sha256(path.read_bytes()).hexdigest())


def _assert_untouched(path: Path, before: tuple[int, int, str] | None, label: str) -> None:
    after = _fingerprint(path)
    assert after == before, (
        f"{label} ตัวจริงของผู้ใช้ถูกแตะระหว่างรันเทสต์: {path}\n"
        f"ก่อน={before}\nหลัง={after}\n"
        "ชุดเทสต์ต้องเขียนลงแซนด์บ็อกซ์เท่านั้น — ดู fixture `_isolate_user_data_files` "
        "ใน tests/conftest.py และค่า VAULTIS_LEDGER_PATH / VAULTIS_ALERTS_PATH "
        "ของ service `tests` ใน docker-compose.yml"
    )


# ------------------------------------------------------------ ชั้น 1: path ตอนรัน

def test_alerts_path_ไม่ใช่คลัง_alert_จริง():
    assert Path(pa.ALERTS_PATH).resolve() != REAL_ALERTS.resolve(), (
        f"alerts.price_alert.ALERTS_PATH ชี้ที่ {pa.ALERTS_PATH} = คลัง alert จริงของผู้ใช้ "
        "— การเรียก add_alert/check_alerts ในชุดเทสต์จะเขียนทับไฟล์นั้นทันที"
    )


def test_ledger_path_ไม่ใช่สมุดบัญชีจริง():
    assert Path(tracker.TRANSACTIONS_FILE).resolve() != REAL_LEDGER.resolve(), (
        f"portfolio.tracker.TRANSACTIONS_FILE ชี้ที่ {tracker.TRANSACTIONS_FILE} = สมุดบัญชีจริง"
    )
    assert Path(tracker.DATA_DIR).resolve() != REAL_LEDGER.parent.resolve(), (
        f"portfolio.tracker.DATA_DIR ชี้ที่ {tracker.DATA_DIR} = โฟลเดอร์สมุดบัญชีจริง — "
        "ต้องย้ายตาม TRANSACTIONS_FILE ไม่งั้น _ensure_storage() ยัง mkdir/เขียนที่เดิม"
    )


def test_config_path_ไม่ใช่_config_json_จริง():
    assert Path(cfg.CONFIG_PATH).resolve() != REAL_CONFIG.resolve(), (
        f"utils.config.CONFIG_PATH ชี้ที่ {cfg.CONFIG_PATH} = คอนฟิกจริงของผู้ใช้ — "
        "save_config() ในชุดเทสต์จะเขียนทับ (ไฟล์นี้อยู่ใน git แต่ค่าที่ผู้ใช้ตั้งเองจะหาย)"
    )


# --------------------------------------------------- ชั้น 2: เรียกฟังก์ชันเซฟตัวจริง

def test_เรียกฟังก์ชันเซฟของ_price_alert_แล้วคลังจริงไม่ขยับ():
    """เรียก add_alert / _save_alerts / delete_alert ตัวจริงโดย **ไม่** stub path เอง.

    นี่คือท่าเดียวกับโพรบที่ล้างคลัง alert ของผู้ใช้เมื่อคืน — ถ้าตาข่ายทำงาน
    ไฟล์จริงต้องไม่ขยับ และไฟล์ข้างเคียง (.bak/.lock/.tmp) ต้องไม่โผล่ข้าง ๆ ไฟล์จริง
    """
    before = _fingerprint(REAL_ALERTS)

    record = pa.add_alert("VOO", "below", 1.0, note="เทสต์แยกไฟล์ข้อมูล")
    pa._save_alerts([record])
    pa.delete_alert(str(record["id"]))
    pa._save_alerts([])

    _assert_untouched(REAL_ALERTS, before, "คลัง price alert")
    # ชื่อไฟล์ชั่วคราวผูกกับ pid ของกระบวนการนี้ — เทียบแบบนี้ไม่ชนกับ scheduler ที่รันอยู่จริง
    stray = REAL_ALERTS.with_name(f"{REAL_ALERTS.name}.tmp.{os.getpid()}")
    assert not stray.exists(), f"เทสต์ทิ้งไฟล์ชั่วคราวไว้ข้างคลังจริง: {stray}"


def test_เรียกฟังก์ชันเขียนของ_tracker_แล้วสมุดบัญชีจริงไม่ขยับ():
    """_ensure_storage / add_transaction / delete_transaction ตัวจริง ไม่ stub path."""
    before = _fingerprint(REAL_LEDGER)

    tracker._ensure_storage()
    row = tracker.add_transaction(
        date="2026-01-02",
        ticker="VOO",
        shares=1.0,
        price_usd=500.0,
        fx_rate_thb=35.0,
        amount_thb=17_525.0,
        note="เทสต์แยกไฟล์ข้อมูล",
    )
    assert tracker.delete_transaction(str(row["tx_id"])) is True

    _assert_untouched(REAL_LEDGER, before, "สมุดบัญชี (ledger)")


def test_เรียก_save_config_แล้ว_config_json_จริงไม่ขยับ():
    before = _fingerprint(REAL_CONFIG)
    cfg.save_config(cfg.load_config())
    _assert_untouched(REAL_CONFIG, before, "config.json")


def test_ค่าที่อ่านจาก_config_ในแซนด์บ็อกซ์ยังเป็นค่าจริงของผู้ใช้():
    """แซนด์บ็อกซ์ของ config ต้องเป็น **สำเนา** ไม่ใช่ไฟล์เปล่า.

    ไม่งั้นการแยกไฟล์จะเปลี่ยนค่าที่เทสต์อื่นอ่าน (tickers/งบ DCA) แบบเงียบ ๆ
    ซึ่งเป็นการ "ตัดข้อมูลทิ้ง" คนละแบบแต่ผลเหมือนกัน
    """
    if not REAL_CONFIG.exists():
        pytest.skip("เครื่องนี้ไม่มี config.json (fresh clone) — load_config() ใช้ค่า default อยู่แล้ว")
    assert Path(cfg.CONFIG_PATH).read_bytes() == REAL_CONFIG.read_bytes()


# ------------------------------------- ตาข่ายใน conftest ต้องทำงานแม้ลืมตั้ง env

def test_ด่านใน_conftest_ย้าย_path_ให้แม้ไม่ได้ตั้ง_env(monkeypatch, tmp_path):
    """จำลอง "ลืมตั้ง VAULTIS_*_PATH" — ค่าในโมดูลกลับไปเป็นไฟล์จริง แล้วด่านต้องย้ายให้.

    ใช้ ``monkeypatch.context()`` เพื่อคืนค่าให้เสร็จ **ภายในเทสต์นี้** — ระหว่างที่ค่าชี้
    ไฟล์จริงอยู่ ไม่มีการเรียกฟังก์ชันเซฟใด ๆ ทั้งสิ้น
    """
    with monkeypatch.context() as m:
        m.setattr(pa, "ALERTS_PATH", REAL_ALERTS)
        m.setattr(tracker, "TRANSACTIONS_FILE", REAL_LEDGER)
        m.setattr(tracker, "DATA_DIR", REAL_LEDGER.parent)
        m.setattr(cfg, "CONFIG_PATH", REAL_CONFIG)

        moved = conftest.redirect_user_data_paths(m, tmp_path / "sandbox")

        assert set(moved) == {"คลัง price alert", "สมุดบัญชี (ledger)", "config.json"}
        for path in (pa.ALERTS_PATH, tracker.TRANSACTIONS_FILE, tracker.DATA_DIR, cfg.CONFIG_PATH):
            assert _is_relative_to(Path(path).resolve(), (tmp_path / "sandbox").resolve()), (
                f"ด่านไม่ได้ย้าย {path} เข้าแซนด์บ็อกซ์"
            )


def test_ด่านใน_conftest_ฟ้องเมื่อ_path_ถูกชี้กลับไปไฟล์จริง(monkeypatch):
    """ตัวตรวจซ้ำหลังเทสต์จบต้องจับได้ ถ้าใคร setattr กลับไปที่ของจริงระหว่างทาง."""
    conftest.assert_user_data_paths_are_isolated()  # สถานะปกติต้องผ่านเงียบ ๆ

    with monkeypatch.context() as m:
        m.setattr(pa, "ALERTS_PATH", REAL_ALERTS)
        with pytest.raises(AssertionError, match="ไฟล์ข้อมูลจริงของผู้ใช้"):
            conftest.assert_user_data_paths_are_isolated()


def test_ด่านไม่แตะ_path_ที่เทสต์ตั้งเองไว้แล้ว(monkeypatch, tmp_path):
    """path ที่ถูก stub ไว้ที่อื่นแล้วต้องไม่ถูกย้ายซ้ำ — ไม่งั้นเทสต์เดิมพังทั้งชุด."""
    chosen = tmp_path / "ของเทสต์เอง" / "price_alerts.json"
    with monkeypatch.context() as m:
        m.setattr(pa, "ALERTS_PATH", chosen)
        moved = conftest.redirect_user_data_paths(m, tmp_path / "sandbox")
        assert "คลัง price alert" not in moved
        assert pa.ALERTS_PATH == chosen


# --------------------------------------------------- ชั้น 3: docker-compose.yml

def _tests_service_env_values(key: str) -> list[str]:
    block = _tests_service_block()
    return [
        line.split(":", 1)[1].strip().strip("\"'")
        for line in block.splitlines()
        if line.strip().startswith(f"{key}:")
    ]


def _assert_env_points_outside_repo(key: str, real_path: Path) -> None:
    values = _tests_service_env_values(key)
    assert values, (
        f"service `tests` ใน docker-compose.yml ไม่ได้ตั้ง {key} — คำสั่งรันเทสต์ mount "
        f"repo ทับ /app ไฟล์ {real_path.name} ตัวจริงของผู้ใช้จึงเขียนทะลุถึง host ได้"
    )
    for value in values:
        path = Path(value)
        assert path.is_absolute(), f"{key}={value} ต้องเป็น absolute path"
        assert not _is_relative_to(path, CONTAINER_REPO_MOUNT), (
            f"{key}={value} ยังอยู่ใต้ {CONTAINER_REPO_MOUNT} = repo ที่ mount มาจาก host"
        )
        assert _is_relative_to(path, Path("/tmp")), (
            f"{key}={value} ต้องอยู่ใต้ /tmp (หายไปพร้อม --rm) เหมือน VAULTIS_DB_PATH"
        )


def test_compose_service_tests_ตั้ง_ledger_path_ออกนอก_repo():
    _assert_env_points_outside_repo("VAULTIS_LEDGER_PATH", REAL_LEDGER)


def test_compose_service_tests_ตั้ง_alerts_path_ออกนอก_repo():
    _assert_env_points_outside_repo("VAULTIS_ALERTS_PATH", REAL_ALERTS)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
