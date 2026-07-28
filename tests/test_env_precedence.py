# -*- coding: utf-8 -*-
"""env จริงต้องมาก่อนไฟล์ ``.env`` เสมอทั้งโปรเจกต์.

``utils/config.py:77`` ประกาศนโยบายนี้ไว้ตรง ๆ ("env มาก่อนค่าในไฟล์เสมอ") แต่มี 6 จุด
ที่เรียก ``load_dotenv(..., override=True)`` ตอน import ซึ่งทำตรงข้าม — ไฟล์ทับ env จริง
เงียบ ๆ  ผลคือค่าที่ Docker (`environment:`) หรือ GitHub Secrets ตั้งไว้ ถูกไฟล์ `.env`
ที่บังเอิญติดมาในเครื่อง/ใน image เขียนทับ โดยไม่มีอะไรเตือน

เทสต์นี้สแกนซอร์สโดยตรง เพราะเป็นพฤติกรรมตอน **import** ซึ่งจับด้วย monkeypatch
ทีหลังไม่ได้ (โมดูลถูกโหลดไปแล้วตั้งแต่ตัวแรกที่ import)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", "node_modules", "build", "dist"}

_OVERRIDE_TRUE = re.compile(r"load_dotenv\s*\([^)]*override\s*=\s*True", re.DOTALL)


_SELF = Path(__file__).resolve()


def _python_files() -> list[Path]:
    """ไฟล์ .py ทั้งโปรเจกต์ ยกเว้นตัวเทสต์นี้เอง (มีสตริงที่กำลังห้ามอยู่ในคำอธิบาย)"""
    return [
        p
        for p in _ROOT.rglob("*.py")
        if p.resolve() != _SELF and not any(part in _SKIP_DIRS for part in p.parts)
    ]


def test_no_load_dotenv_with_override_true():
    offenders = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _OVERRIDE_TRUE.search(text):
            for i, line in enumerate(text.splitlines(), start=1):
                if "override=True" in line.replace(" ", "") or "override = True" in line:
                    offenders.append(f"{path.relative_to(_ROOT)}:{i}")

    assert not offenders, (
        "load_dotenv(override=True) ทำให้ไฟล์ .env ทับ env จริง — "
        "ขัดกับนโยบายใน utils/config.py: " + ", ".join(offenders)
    )


_FILE_HOOK = "https://file.example/hook"


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """ชี้ load_config ไปที่ config.json ชั่วคราว + ล้าง cache.

    ต้องล้าง ``_cache`` ด้วยเพราะ ``load_config`` จำผลไว้ตาม mtime ของไฟล์
    (utils/config.py:133) — เทสต์ก่อนหน้าในชุดเดียวกันอุ่น cache ไว้แล้ว
    ถ้าไม่ล้าง จะได้ค่าเก่ากลับมาและเทสต์นี้จะวัดอะไรไม่ได้เลย
    """
    import json as _json

    from utils import config as cfg

    path = tmp_path / "config.json"
    path.write_text(
        _json.dumps({"notifications": {"discord_webhook_url": _FILE_HOOK}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CONFIG_PATH", path)
    monkeypatch.setattr(cfg, "_cache", None)
    yield cfg
    monkeypatch.setattr(cfg, "_cache", None)


def test_env_wins_over_file(isolated_config, monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://env.example/hook")
    merged = isolated_config.load_config()
    assert merged["notifications"]["discord_webhook_url"] == "https://env.example/hook"


def test_empty_env_falls_back_to_file_value(isolated_config, monkeypatch):
    """env ว่าง = ไม่ได้ตั้ง → ใช้ค่าในไฟล์ (ไม่ใช่บังคับให้ว่างตาม)"""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "   ")
    merged = isolated_config.load_config()
    assert merged["notifications"]["discord_webhook_url"] == _FILE_HOOK


def test_unset_env_falls_back_to_file_value(isolated_config, monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    merged = isolated_config.load_config()
    assert merged["notifications"]["discord_webhook_url"] == _FILE_HOOK


@pytest.mark.parametrize(
    "module_name",
    ["alerts.notifier", "analysis.macro", "analysis.ai_advisor", "analysis.llm"],
)
def test_modules_still_import(module_name):
    """เปลี่ยน override แล้วต้องไม่พัง import"""
    import importlib

    assert importlib.import_module(module_name) is not None
