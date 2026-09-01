# -*- coding: utf-8 -*-
"""ไฟล์ใน repo ต้องไม่มี UTF-8 BOM (AUDIT_ROUND2_2026-08-07).

``dashboard/app.py`` เป็นไฟล์เดียวที่ขึ้นต้นด้วย ``EF BB BF`` — Python import ได้ปกติ
เพราะตัวอ่านไฟล์ของ interpreter ตัด BOM ให้ แต่เครื่องมือใด ๆ ที่อ่านด้วย
``read_text(encoding="utf-8")`` แล้ว ``ast.parse`` จะพังทันทีด้วย
``SyntaxError: invalid non-printable character U+FEFF`` ที่บรรทัด 1 — ซึ่งรวมถึง
**เทสต์ที่ตรวจโครงสร้างไฟล์เอง** (เช่นเทสต์ที่ตรึงรายการหน้าจอในแถบข้าง) linter และ codemod
บรรทัด ``# -*- coding: utf-8 -*-`` ที่ตามมาทำหน้าที่บอก encoding อยู่แล้ว BOM จึงเกินมาเปล่า ๆ

เทสต์นี้กันทั้ง repo ไม่ใช่เฉพาะไฟล์เดียว เพราะเอดิเตอร์บน Windows เติม BOM ให้เงียบ ๆ ได้
ทุกเมื่อ และอาการที่ได้กลับมาอ่านไม่ออกเลยว่าเกิดจากอะไร
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"

# นามสกุลที่เป็นไฟล์ข้อความซึ่ง BOM สร้างปัญหาจริง (ไบนารีไม่เกี่ยว)
TEXT_SUFFIXES = {".py", ".md", ".json", ".yml", ".yaml", ".toml", ".txt", ".cfg", ".ini", ".sh"}

# ไดเรกทอรีที่ไม่ใช่ซอร์สของโปรเจกต์ — และ **ข้อมูลจริงของผู้ใช้** ที่เทสต์ไม่ควรไปอ่าน
# (``git ls-files`` ใช้ไม่ได้: คอนเทนเนอร์ ``tests`` ไม่มี git ติดตั้งไว้)
SKIP_DIRS = {
    ".git",
    ".github",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    ".docker-data",
}
SKIP_RELATIVE_DIRS = {Path("portfolio/data"), Path("alerts/data")}


def _repo_text_files() -> list[Path]:
    """ไฟล์ข้อความทั้ง repo (เดินไดเรกทอรีเอง เพื่อให้ผลเหมือนกันทั้งในและนอก Docker)."""
    paths: list[Path] = []
    for root, dirs, files in os.walk(REPO_ROOT):
        root_path = Path(root)
        dirs[:] = [
            d
            for d in dirs
            if d not in SKIP_DIRS
            and (root_path / d).relative_to(REPO_ROOT) not in SKIP_RELATIVE_DIRS
        ]
        for name in files:
            path = root_path / name
            if path.suffix.lower() in TEXT_SUFFIXES and path.is_file():
                paths.append(path)
    return paths


def test_ไม่มีไฟล์ไหนใน_repo_ขึ้นต้นด้วย_utf8_bom():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _repo_text_files()
        if path.read_bytes()[:3] == BOM
    ]
    assert not offenders, (
        f"ไฟล์เหล่านี้มี UTF-8 BOM: {offenders} — เครื่องมือที่อ่านด้วย utf-8 ธรรมดา "
        "แล้ว ast.parse จะพังด้วย SyntaxError: invalid non-printable character U+FEFF "
        "(ตัดออกด้วย: sed -i '1s/^\\xEF\\xBB\\xBF//' <ไฟล์>)"
    )


def test_dashboard_app_ต้อง_ast_parse_ได้ด้วย_utf8_ธรรมดา():
    """เคสที่ทำให้เจอบั๊กนี้ตั้งแต่แรก — อ่านแบบ utf-8 (ไม่ใช่ utf-8-sig) แล้ว parse."""
    source = (REPO_ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")

    ast.parse(source)  # ต้องไม่โยน SyntaxError จาก U+FEFF


def test_ทุกไฟล์_py_ใน_repo_parse_ได้ด้วย_utf8_ธรรมดา():
    broken: list[str] = []
    for path in _repo_text_files():
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # ระบุชื่อไฟล์ให้ชัด ไม่ใช่ traceback ลอย ๆ
            broken.append(f"{path.relative_to(REPO_ROOT)}: {exc}")
    assert not broken, f"ไฟล์ .py ที่ parse ไม่ผ่าน: {broken}"
