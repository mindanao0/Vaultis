# -*- coding: utf-8 -*-
"""สำรองสมุดที่ทดแทนไม่ได้ + ตรวจสมุดแบบต่อท้ายอย่างเดียว — ไม่แตะสมุดจริง (ทุก path อยู่ใน tmp)."""
from __future__ import annotations

import gzip
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobs import backup_ledgers as bk

TZ = timezone(timedelta(hours=7))


def _day(y, m, d):
    return datetime(y, m, d, 7, 0, tzinfo=TZ)


@pytest.fixture
def env(tmp_path, monkeypatch):
    src = {n: tmp_path / "src" / f"{n}.csv" for n in ("predict_log", "trade_log", "stock_transactions", "transactions")}
    (tmp_path / "src").mkdir()
    src["predict_log"].write_text("date,x\n2026-10-02,1\n")
    src["trade_log"].write_text("date,x\n2026-10-02,1\n")
    src["transactions"].write_text("tx_id,ticker\n")
    monkeypatch.setattr(bk, "_sources", lambda: dict(src))
    d = tmp_path / "backups"
    return type("E", (), {"src": src, "dir": d, "run": staticmethod(lambda now=None: bk.run_backup(now=now or _day(2026, 10, 5), directory=d))})


def _read(p: Path) -> str:
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return fh.read()


def test_first_run_backs_up_existing_files_and_content_is_identical(env):
    r = env.run()
    assert r["ok"] and sorted(r["backed_up"]) == ["predict_log", "trade_log", "transactions"] and r["missing"] == ["stock_transactions"]
    for label in r["backed_up"]:
        (snap,) = list(env.dir.glob(f"{label}.*.gz"))
        assert _read(snap) == env.src[label].read_text()


def test_unchanged_files_are_not_rewritten(env):
    env.run()
    before = sorted(p.name for p in env.dir.iterdir())
    r = env.run(_day(2026, 10, 6))
    assert r["backed_up"] == [] and sorted(r["unchanged"]) == ["predict_log", "trade_log", "transactions"]
    assert sorted(p.name for p in env.dir.iterdir()) == before


def test_appending_makes_a_new_snapshot_without_alarm(env):
    env.run()
    with open(env.src["trade_log"], "a") as fh:
        fh.write("2026-10-05,2\n")
    r = env.run(_day(2026, 10, 6))
    assert r["backed_up"] == ["trade_log"] and r["tamper"] == [] and r["ok"]
    assert len(list(env.dir.glob("trade_log.*.gz"))) == 2


def test_edited_or_truncated_append_only_ledger_raises_alarm_but_keeps_everything(env, caplog):
    env.run()
    original = env.src["predict_log"].read_text()
    env.src["predict_log"].write_text(original.replace("2026-10-02,1", "2026-10-02,9") + "2026-10-05,2\n")     # แก้แถวเก่า + ต่อท้าย
    with caplog.at_level("ERROR"):
        r = env.run(_day(2026, 10, 6))
    assert r["tamper"] == ["predict_log"] and r["ok"] is False and "predict_log" in caplog.text
    snaps = sorted(env.dir.glob("predict_log.*.gz"))
    assert len(snaps) == 2 and original in {_read(p) for p in snaps}              # สแนปช็อตเดิมยังอยู่ครบ + เก็บของที่ผิดปกติไว้ด้วย
    env.src["predict_log"].write_text("date,x\n")                                 # ย่อทั้งไฟล์ — ต้องจับได้เหมือนกัน
    assert env.run(_day(2026, 10, 7))["tamper"] == ["predict_log"]


def test_ordinary_ledgers_may_be_edited_freely(env):
    env.run()
    env.src["transactions"].write_text("tx_id,ticker\nabc,VOO\n")                 # ผู้ใช้แก้สมุดพอร์ตหลักเป็นเรื่องปกติ
    r = env.run(_day(2026, 10, 6))
    assert r["backed_up"] == ["transactions"] and r["tamper"] == [] and r["ok"]


def test_retention_keeps_recent_days_and_last_snapshot_of_each_old_month(env):
    for i, (when, text) in enumerate([(_day(2026, 1, 5), "a"), (_day(2026, 1, 20), "b"), (_day(2026, 2, 10), "c"), (_day(2026, 3, 15), "d")]):
        env.src["transactions"].write_text(f"tx_id\n{text}\n")
        env.run(when)
    names = sorted(p.name for p in env.dir.glob("transactions.*.gz"))
    dates = [n.split(".")[1] for n in names]
    assert dates == ["2026-01-20", "2026-02-10", "2026-03-15"], dates              # 5 ม.ค. ถูกตัด (เดือนเดียวกับ 20 ม.ค.) · เดือนเก่ายังเหลือชุดสุดท้าย


def test_unwritable_backup_dir_reports_failure_and_never_raises(env, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    r = bk.run_backup(now=_day(2026, 10, 5), directory=blocker / "sub")
    assert r["ok"] is False and r["error"] and env.src["predict_log"].read_text().startswith("date,x")


def test_module_is_safe_by_construction():
    src = inspect.getsource(bk)
    assert "unlink" in src and "rmtree" not in src and "shutil" not in src.split("logger =")[1]            # ลบได้เฉพาะสแนปช็อตที่หมดช่วงเก็บ ไม่แตะสมุดต้นทาง
    assert ".write_text" not in src and "open(path" not in src                                             # ไม่เขียนทับสมุดต้นทาง


def test_registered_in_sandbox_compose_and_docs():
    root = Path(__file__).parent
    assert '"jobs.backup_ledgers"' in (root / "conftest.py").read_text(encoding="utf-8")
    compose = (root.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert "VAULTIS_BACKUP_DIR: /data/backups" in compose and "VAULTIS_BACKUP_DIR: /tmp/" in compose
    assert "VAULTIS_BACKUP_DIR" in (root.parent / "CLAUDE.md").read_text(encoding="utf-8")


def test_all_irreplaceable_ledgers_are_in_the_backup_list():
    labels = set(bk._sources())                                                                               # noqa: SLF001
    assert {"predict_log", "trade_log", "stock_transactions", "dar_transactions", "select_transactions", "transactions", "price_alerts"} <= labels
    assert set(bk.APPEND_ONLY) == {"predict_log", "trade_log"}
