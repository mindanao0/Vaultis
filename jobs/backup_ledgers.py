# -*- coding: utf-8 -*-
"""สำรองสมุดที่ **ทดแทนไม่ได้** (gitignored ทั้งหมด) เป็นสแนปช็อตรายวัน — และตรวจว่าสมุดแบบต่อท้ายอย่างเดียวไม่ถูกแก้/ย่อ.

สมุดที่สำรอง: คำทำนาย PREDICT / TRADE (หลักฐาน forward test ทั้งก้อน) · พอร์ตกระดาษ STOCK-DCA · สมุด DAR · สมุด SELECT · สมุดพอร์ตหลัก · คลัง price alert
รันโดย scheduler ทุกวัน 06:50 (หลังงานบันทึกคำทำนาย) และตอนเริ่มโปรเซส · ไม่ต้องใช้ webhook ไม่มี LLM ไม่มีค่าใช้จ่าย · **ไม่โยน exception**

* เก็บเป็น ``<ชื่อ>.<วันที่>.<hash 8 ตัว>.<นามสกุล>.gz`` ใน ``BACKUP_DIR`` (compose ชี้ไป ``/data/backups`` = ``./.docker-data/backups`` บน host)
  สร้างสแนปช็อตใหม่ **เมื่อเนื้อไฟล์เปลี่ยนเท่านั้น** (hash ต่างจากชุดล่าสุด) · ไม่เคยเขียนทับ/ลบสแนปช็อตที่ยังอยู่ในช่วงเก็บ
* เก็บทุกชุดของ ``KEEP_DAILY_DAYS`` วันล่าสุด + ชุดสุดท้ายของแต่ละเดือนที่เก่ากว่านั้น (ไม่ลบทิ้งหมด — เดือนเก่าคือหลักฐาน)
* **ตรวจสมุดแบบต่อท้ายอย่างเดียว** (PREDICT / TRADE): ไฟล์ปัจจุบันต้องขึ้นต้นด้วยเนื้อหาของสแนปช็อตล่าสุดเป๊ะ ๆ ถ้าไม่ใช่ (มีคนแก้/ลบ/ย่อ) = log ERROR + ``tamper`` ในผลลัพธ์
  แต่ **ยังเก็บสแนปช็อตของไฟล์ที่ผิดปกตินั้นไว้ด้วย** และสแนปช็อตเดิมยังอยู่ครบ — กู้คืนด้วย ``gunzip -c <ไฟล์>.gz > <ปลายทาง>`` (ตัวเก่าสุดที่ยังขึ้นต้นถูกต้อง)
* ที่เก็บอยู่ดิสก์เดียวกับสมุด: กันการลบ/แก้พลาด ไม่กันดิสก์เสีย — คัดลอก ``BACKUP_DIR`` ไปที่อื่นเป็นระยะ
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR: Path = Path(os.environ.get("VAULTIS_BACKUP_DIR", "").strip() or REPO_ROOT / ".docker-data" / "backups")
KEEP_DAILY_DAYS = 30
APPEND_ONLY = ("predict_log", "trade_log")
_NAME = re.compile(r"^(?P<label>.+?)\.(?P<date>\d{4}-\d{2}-\d{2})\.(?P<hash>[0-9a-f]{8})\.(?P<ext>[A-Za-z0-9]+)\.gz$")


def _sources() -> dict[str, Path]:
    """path ปัจจุบันของแต่ละสมุด (อ่านตอนเรียก — เทสต์ monkeypatch ค่าคงที่ของโมดูลเจ้าของได้)."""
    from alerts import price_alert
    from portfolio import dar_ledger, predict_ledger, select_ledger, stock_ledger, tracker, trade_ledger

    return {
        "predict_log": predict_ledger.PREDICT_LEDGER_PATH, "trade_log": trade_ledger.TRADE_LEDGER_PATH,
        "stock_transactions": stock_ledger.STOCK_LEDGER_PATH, "dar_transactions": dar_ledger.DAR_LEDGER_PATH,
        "select_transactions": select_ledger.SELECT_LEDGER_PATH, "transactions": tracker.TRANSACTIONS_FILE,
        "price_alerts": price_alert.ALERTS_PATH,
    }


def _snapshots(label: str, directory: Path) -> list[tuple[str, str, Path]]:
    """(วันที่, hash, path) ของสแนปช็อตของ ``label`` เรียงเก่า→ใหม่ (ชื่อวันที่เรียงได้ตรง ๆ; วันเดียวกันหลายชุดเรียงตามเวลาแก้ไขไฟล์)."""
    out = []
    for p in directory.glob(f"{label}.*.gz"):
        m = _NAME.match(p.name)
        if m and m.group("label") == label:
            out.append((m.group("date"), m.group("hash"), p))
    return sorted(out, key=lambda x: (x[0], x[2].stat().st_mtime))


def _write_gz(src_bytes: bytes, dest: Path) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    with gzip.open(tmp, "wb", compresslevel=6) as fh:
        fh.write(src_bytes)
    os.replace(tmp, dest)


def _prune(label: str, directory: Path, today: datetime) -> int:
    snaps = _snapshots(label, directory)
    cutoff = (today - timedelta(days=KEEP_DAILY_DAYS)).strftime("%Y-%m-%d")
    last_of_month: dict[str, Path] = {}
    for date, _h, p in snaps:
        last_of_month[date[:7]] = p                       # เรียงเก่า→ใหม่ ตัวสุดท้ายของเดือนชนะ
    removed = 0
    for date, _h, p in snaps:
        if date < cutoff and last_of_month[date[:7]] != p:
            p.unlink()
            removed += 1
    return removed


def run_backup(now: datetime | None = None, directory: Path | None = None) -> dict[str, Any]:
    """คืน ``{"ok", "backed_up", "unchanged", "missing", "tamper", "pruned", "error"}`` — ไม่โยน exception."""
    d = Path(directory or BACKUP_DIR)
    today = now or datetime.now(timezone(timedelta(hours=7)))
    res: dict[str, Any] = {"ok": True, "backed_up": [], "unchanged": [], "missing": [], "tamper": [], "pruned": 0, "error": None}
    try:
        d.mkdir(parents=True, exist_ok=True)
        for label, path in _sources().items():
            path = Path(path)
            if not path.exists():
                res["missing"].append(label)                # ยังไม่เคยมีไฟล์ (เช่น สมุดที่ยังไม่ได้ใช้) ไม่ใช่ข้อผิดพลาด
                continue
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()[:8]
            snaps = _snapshots(label, d)
            if snaps and snaps[-1][1] == digest:
                res["unchanged"].append(label)
                continue
            if label in APPEND_ONLY and snaps:
                with gzip.open(snaps[-1][2], "rb") as fh:
                    prev = fh.read()
                if not data.startswith(prev):
                    res["tamper"].append(label)
                    logger.error("สมุด %s ไม่ได้ขึ้นต้นด้วยเนื้อหาของสแนปช็อตล่าสุด (%s) — ถูกแก้/ย่อ/ลบบางส่วน? เก็บสแนปช็อตของไฟล์ปัจจุบันไว้ "
                                 "สแนปช็อตเดิมยังอยู่ครบ กู้คืนด้วย gunzip -c", label, snaps[-1][2].name)
            name = f"{label}.{today:%Y-%m-%d}.{digest}.{path.suffix.lstrip('.') or 'dat'}.gz"
            _write_gz(data, d / name)
            res["backed_up"].append(label)
            res["pruned"] += _prune(label, d, today)
        res["ok"] = not res["tamper"]
        if res["backed_up"]:
            logger.info("สำรองสมุด: %s", ", ".join(res["backed_up"]))
        return res
    except OSError as exc:
        logger.error("สำรองสมุดล้มเหลว: %s — สมุดจริงไม่ได้ถูกแตะ", exc)
        res.update({"ok": False, "error": str(exc)})
        return res
