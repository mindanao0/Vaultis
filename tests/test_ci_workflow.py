# -*- coding: utf-8 -*-
"""ต้องมี CI ที่รัน pytest และผูกกับการแก้โค้ด — AUDIT_2026-08-06 ข้อ 0-C (= D2).

อาการก่อนแก้ (วัดจริง):

    ls .github/workflows/      → scheduler.yml (ไฟล์เดียว)
    grep -rn "pytest" .github/ → ไม่มีผลลัพธ์

และไฟล์เดียวที่มีนั้น trigger เป็น ``schedule:`` 3 cron + ``workflow_dispatch:``
เท่านั้น — ไม่มี ``on: push`` หรือ ``on: pull_request`` เลย ⇒ ไม่ใช่แค่ไม่มี pytest
แต่ไม่มี trigger ใดผูกกับการแก้โค้ด ตัวเลข "ชุดเทสต์ผ่านกี่ตัว" จึงพึ่งวินัยของคนรันล้วน ๆ

เทสต์นี้ตรึงคุณสมบัติที่ CI ต้องมี ไม่ใช่ตรึงชื่อไฟล์ — เปลี่ยนชื่อ/ยุบรวม workflow ได้
ตราบใดที่ยังมีตัวหนึ่งที่รัน pytest บน push + pull_request

หมายเหตุ: อ่าน YAML ด้วยการแยกบล็อกตามการเยื้องเอง เพราะ image ของชุดเทสต์
ไม่มี ``PyYAML`` (ยืนยันแล้ว: ``ModuleNotFoundError: No module named 'yaml'``)
โครงสร้างที่ต้องอ่านตื้นมาก (คีย์ระดับบนสุดกับลูกชั้นเดียว) จึงพอเพียง
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# บรรทัดที่สั่งรัน pytest จริง ๆ (`run: pytest -q`, `run: python -m pytest`, บล็อก | ก็ได้)
_RUNS_PYTEST = re.compile(r"^\s*(?:-\s*)?(?:run:\s*)?.*\bpytest\b", re.MULTILINE)


def _workflow_files() -> list[Path]:
    if not WORKFLOW_DIR.is_dir():
        return []
    return sorted(
        p for p in WORKFLOW_DIR.iterdir() if p.suffix in (".yml", ".yaml")
    )


def _runs_pytest(text: str) -> bool:
    """workflow นี้มีขั้นตอนที่สั่ง pytest ไหม (ไม่นับคอมเมนต์)."""
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    return bool(_RUNS_PYTEST.search(body))


def _top_level_block(text: str, key: str) -> list[str]:
    """คืนบรรทัดลูกของคีย์ระดับบนสุด ``key`` (ไม่รวมบรรทัดคีย์เอง).

    หยุดเมื่อเจอคีย์ระดับบนสุดตัวถัดไป (คอลัมน์ 0 และไม่ใช่คอมเมนต์)
    """
    lines = text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if not inside:
            if re.match(rf"^{re.escape(key)}\s*:", line):
                inside = True
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if not line[:1].isspace():  # คีย์ระดับบนสุดตัวถัดไป
            break
        out.append(line)
    return out


def _child_keys(block: list[str]) -> set[str]:
    """คีย์ลูกชั้นแรกของบล็อก (ระดับการเยื้องที่ตื้นที่สุดในบล็อกนั้น)."""
    if not block:
        return set()
    indent = min(len(line) - len(line.lstrip()) for line in block)
    keys = set()
    for line in block:
        if len(line) - len(line.lstrip()) != indent:
            continue
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:", line)
        if m:
            keys.add(m.group(1))
    return keys


def _pytest_workflow() -> Path:
    """workflow ที่รัน pytest และผูกกับการแก้โค้ด (push/pull_request).

    workflow ที่รัน pytest แต่ trigger เป็น schedule/workflow_dispatch ล้วน ๆ
    (เช่น smoke test รายคืนที่ยิงเน็ตจริง) ไม่นับ — คนละหน้าที่กัน
    """
    runs_pytest = [p for p in _workflow_files() if _runs_pytest(p.read_text(encoding="utf-8"))]
    candidates = [
        p
        for p in runs_pytest
        if _child_keys(_top_level_block(p.read_text(encoding="utf-8"), "on"))
        & {"push", "pull_request"}
    ]
    if not candidates:
        have = [p.name for p in _workflow_files()] or ["(ไม่มีไฟล์ workflow เลย)"]
        extra = (
            f" (รัน pytest แต่ trigger ไม่ผูกกับการแก้โค้ด: {[p.name for p in runs_pytest]})"
            if runs_pytest
            else ""
        )
        pytest.fail(
            "ไม่มี GitHub Actions workflow ตัวไหนรัน pytest ตอน push/pull_request เลย — "
            f"พบเฉพาะ {have}{extra} ⇒ การแก้โค้ดไม่ถูกตรวจโดยอัตโนมัติ (AUDIT ข้อ 0-C)"
        )
    assert len(candidates) == 1, (
        f"มี workflow ที่รัน pytest บน push/PR มากกว่าหนึ่งตัว {[p.name for p in candidates]} — "
        "นิยาม 'ชุดเทสต์ของ CI' ต้องมีที่เดียว"
    )
    return candidates[0]


def test_มี_workflow_ที่รัน_pytest():
    wf = _pytest_workflow()
    assert wf.is_file()


def test_trigger_ผูกกับการแก้โค้ด():
    """schedule/workflow_dispatch อย่างเดียวไม่พอ — ต้องยิงตอน push และ pull_request."""
    text = _pytest_workflow().read_text(encoding="utf-8")
    # PyYAML 1.1 จะอ่านคีย์ `on:` เป็น True แต่ที่นี่อ่านเป็นข้อความจึงใช้ชื่อคีย์ตรง ๆ
    triggers = _child_keys(_top_level_block(text, "on"))
    assert "push" in triggers, f"workflow ไม่ได้ trigger ตอน push (พบ {sorted(triggers)})"
    assert "pull_request" in triggers, (
        f"workflow ไม่ได้ trigger ตอน pull_request (พบ {sorted(triggers)})"
    )


def test_ติดตั้ง_requirements_dev():
    """ไม่มี pytest-asyncio = 2 ไฟล์ล่มทั้งที่โค้ดปกติดี (ดูหัวไฟล์ requirements-dev.txt).

    ต้องดูเฉพาะบรรทัดที่ ``pip install`` จริง ๆ — ชื่อไฟล์ยังโผล่ที่
    ``cache-dependency-path`` ได้โดยไม่ได้ติดตั้งอะไรเลย (เคยเขียนเช็คแบบ
    "มีสตริงนี้ในไฟล์ไหม" แล้วเทสต์ผ่านทั้งที่ถอด -r requirements-dev.txt ออกไปแล้ว)
    """
    text = _pytest_workflow().read_text(encoding="utf-8")
    installs = [
        line
        for line in text.splitlines()
        if "pip install" in line and not line.lstrip().startswith("#")
    ]
    assert installs, "workflow ไม่มีขั้นตอนติดตั้ง dependencies เลย"
    joined = " ".join(installs)
    assert "requirements-dev.txt" in joined, (
        f"CI ไม่ได้ติดตั้ง requirements-dev.txt (คำสั่งที่พบ: {installs}) — "
        "pytest.ini ตั้ง asyncio_mode = auto ถ้าไม่มี pytest-asyncio "
        "ชุดเทสต์จะแดงเพราะเครื่องมือขาด ไม่ใช่เพราะโค้ด"
    )
    assert "requirements.txt" in joined


def test_interpreter_ตรงกับ_dockerfile():
    """เวอร์ชันที่ CI ทดสอบต้องเป็นเวอร์ชันเดียวกับที่ deploy จริง."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"^FROM python:(\d+\.\d+)", dockerfile, re.MULTILINE)
    assert m, "อ่านเวอร์ชัน python จาก Dockerfile ไม่ได้"
    expected = m.group(1)
    text = _pytest_workflow().read_text(encoding="utf-8")
    versions = re.findall(r"python-version:\s*['\"]?([\d.]+)", text)
    assert versions, "workflow ไม่ได้ระบุ python-version"
    assert all(v == expected for v in versions), (
        f"CI รัน python {versions} แต่ Dockerfile ใช้ {expected} — "
        "บั๊กเฉพาะเวอร์ชันจะโผล่ที่ production ก่อน CI"
    )


def test_ci_ไม่แตะ_secrets():
    """งานอัตโนมัติต้องไม่จ่ายค่า LLM และต้องไม่ยิง webhook จริง (CLAUDE.md)."""
    text = _pytest_workflow().read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    leaked = re.findall(r"secrets\.([A-Za-z0-9_]+)", body)
    assert not leaked, (
        f"workflow ของชุดเทสต์อ้าง secrets: {sorted(set(leaked))} — "
        "เทสต์ที่ต้องมีคีย์จริงถึงจะผ่านคือเทสต์ที่ผิด ไม่ใช่ CI ที่ผิด "
        "(เผลอส่งคีย์เข้าไป = ทุก push อาจเสียเงินหรือยิงข้อความจริง)"
    )
