# -*- coding: utf-8 -*-
"""เอกสารและ dependency ต้องตรงกับสิ่งที่โค้ดทำจริง — AUDIT_2026-08-06 ข้อ D4.

เทสต์ชุดนี้ตรึง "สัญญาที่เอกสารประกาศเอง" ไม่ใช่พฤติกรรมรันไทม์ เพราะสี่ข้อใน D4
ล้วนเป็นกรณีที่เอกสาร/ไฟล์ deploy พูดคนละเรื่องกับโค้ด ซึ่งวัดได้ทางเดียวคือเทียบ
ข้อความในไฟล์กับความจริงที่ดึงจากโค้ด/สภาพแวดล้อมที่ติดตั้งจริง

อาการก่อนแก้ (วัดจริงในคอนเทนเนอร์ของชุดเทสต์ 2026-08-07):

    D4.1  pip list --format=freeze  → ติดตั้งจริง 134 · requirements.txt pin 28
          ที่ลอยรวมถึงตัวที่ผลิตตัวเลข: scipy 1.17.1, numba 0.66.0, llvmlite 0.48.0,
          starlette 0.41.3, urllib3 2.7.0
          python -c "import requests"
            → RequestsDependencyWarning: urllib3 (2.7.0) or chardet (7.4.3)/
              charset_normalizer (3.4.9) doesn't match a supported version!
            (ต้นเหตุ: reportlab ต้องการ ``chardet`` แบบไม่ระบุเวอร์ชัน แล้วมันไหลถึง 7.4.3
             ขณะที่ requests.check_compatibility ยืนยันว่าต้อง < 6)
    D4.2  render.yaml ประกาศ GROQ_API_KEY / GOOGLE_API_KEY ที่ถอดออกจากโค้ดแล้ว
          และคอมเมนต์เขียนว่า "LLM ตัวหลักคือ Claude Haiku 4.5" ขณะที่
          analysis/llm.ANTHROPIC_MODEL = "claude-sonnet-5"
    D4.3  README.md บรรทัด 26/153/288 อ้าง Groq เป็นผู้ให้บริการปัจจุบัน และบรรทัด 253
          ลิสต์ GOOGLE_API_KEY/GROQ_API_KEY โดยไม่มี ANTHROPIC_API_KEY เลย (repo เป็น public)
    D4.4  CLAUDE.md เขียน "slip OCR (Claude Sonnet 5)" ขณะที่
          backend/routers/transactions.py ฮาร์ดโค้ด model="claude-haiku-4-5"
"""

import re
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "requirements.txt"
REQ_DEV = ROOT / "requirements-dev.txt"
RENDER = ROOT / "render.yaml"
README = ROOT / "README.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
TRANSACTIONS = ROOT / "backend" / "routers" / "transactions.py"

# เครื่องมือของ pip เอง ไม่ใช่ dependency ของโปรเจกต์
_TOOLCHAIN = {"pip", "setuptools", "wheel", "pkg-resources"}


def _norm_pkg(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _pins(path: Path) -> dict[str, str]:
    """อ่าน ``ชื่อ==เวอร์ชัน`` จากไฟล์ requirements (ข้ามคอมเมนต์/บรรทัดว่าง)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, version = line.split("==", 1)
        out[_norm_pkg(name)] = version.strip()
    return out


def _installed() -> dict[str, str]:
    from importlib.metadata import distributions

    out: dict[str, str] = {}
    for dist in distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name:
            out[_norm_pkg(name)] = dist.version
    return out


def _closure(roots: list[str]) -> set[str]:
    """แพ็กเกจทั้งหมดที่ ``pip install`` จะลากมาให้ ``roots`` ตามเมทาดาทาที่ติดตั้งจริง
    (ข้าม requirement ที่ผูกกับ extra — โปรเจกต์ไม่ได้ติดตั้ง extra ตัวไหนเลย)"""
    from importlib.metadata import distributions
    from packaging.requirements import Requirement

    dists = {}
    for dist in distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name:
            dists[_norm_pkg(name)] = dist

    seen: set[str] = set()
    stack = [_norm_pkg(r) for r in roots]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in dists:
            continue
        seen.add(cur)
        for raw in dists[cur].requires or []:
            try:
                req = Requirement(raw)
            except Exception:
                continue
            if req.marker is not None:
                try:
                    if not req.marker.evaluate({"extra": ""}):
                        continue
                except Exception:
                    continue
            stack.append(_norm_pkg(req.name))
    return seen


def _norm_text(s: str) -> str:
    """ตัดทุกอย่างที่ไม่ใช่ตัวอักษร/ตัวเลขออก เพื่อเทียบ ``claude-haiku-4-5``
    กับรูปที่คนอ่าน ``Claude Haiku 4.5`` ได้โดยไม่ผูกกับเครื่องหมายวรรคตอน."""
    return re.sub(r"[^0-9a-z]+", "", s.lower())


# --------------------------------------------------------------------------
# D4.1 — "Dependencies are pinned" ต้องเป็นเรื่องจริง
# --------------------------------------------------------------------------


def test_ทุกแพ็กเกจที่ติดตั้งจริงถูก_pin_ไว้ในไฟล์_requirements():
    """CLAUDE.md ประกาศว่า "requirements.txt pins every package" และ CI ติดตั้งใหม่
    จากไฟล์นี้ทุกรอบ — ตัวที่ไม่ถูก pin คือตัวที่เปลี่ยนเวอร์ชันเองได้ระหว่างสองรอบ
    ที่ควรให้ผลเหมือนกัน scipy/numba เข้าเส้นทางตัวเลขโดยตรง (vectorbt, prophet,
    scikit-learn) ⇒ ผลลัพธ์เชิงตัวเลขเปลี่ยนได้โดยไม่มี commit ไหนเปลี่ยน

    วัดจาก transitive closure ของสิ่งที่ประกาศไว้เอง ไม่ใช่ทุกอย่างที่บังเอิญติดตั้ง
    อยู่ในเครื่อง — นักพัฒนาที่ลง ruff/ipdb เพิ่มในเวอร์ชวลเอ็นวีของตัวเองต้องไม่ทำให้แดง"""
    pins = {**_pins(REQ), **_pins(REQ_DEV)}
    declared = list(_pins(REQ)) + list(_pins(REQ_DEV))
    needed = _closure(declared) - _TOOLCHAIN
    installed = _installed()
    unpinned = sorted(needed - set(pins))
    assert not unpinned, (
        f"มี {len(unpinned)} แพ็กเกจที่ pip ลากเข้ามาแต่ไม่ถูก pin "
        f"(closure ทั้งหมด {len(needed)} · pin ไว้ {len(pins)}): "
        + ", ".join(f"{n}=={installed.get(n, '?')}" for n in unpinned[:12])
        + (" ..." if len(unpinned) > 12 else "")
    )


def test_เวอร์ชันที่_pin_ตรงกับที่ติดตั้งจริง():
    """pin ที่ไม่ตรงกับสภาพแวดล้อมที่ชุดเทสต์ผ่านอยู่ = pin ที่ไม่มีใครทดสอบ
    (ยกเว้น chardet ที่ตั้งใจ pin ต่ำกว่าที่ค้างอยู่ใน image ปัจจุบัน ดูเทสต์ถัดไป)"""
    installed = _installed()
    mismatch = []
    for name, version in {**_pins(REQ), **_pins(REQ_DEV)}.items():
        if name == "chardet":
            continue
        if name in installed and installed[name] != version:
            mismatch.append(f"{name}: pin={version} ติดตั้งจริง={installed[name]}")
    assert not mismatch, "pin ไม่ตรงกับที่ติดตั้งจริง: " + " · ".join(mismatch)


def test_chardet_ถูก_pin_ในช่วงที่_requests_ยอมรับ():
    """``reportlab`` ต้องการ ``chardet`` แบบไม่ระบุเวอร์ชัน พอปล่อยลอยมันไหลถึง 7.4.3
    แล้ว ``requests.check_compatibility`` (ซึ่งเช็ค chardet ที่ติดตั้ง ไม่ว่าจะติดตั้ง
    ผ่าน extra หรือไม่) ยิง ``RequestsDependencyWarning`` ทุกครั้งที่ import requests
    — เป็นหลักฐานว่า dependency ที่ลอยชนกันจริงแล้ว ไม่ใช่ความเสี่ยงเชิงทฤษฎี"""
    pins = _pins(REQ)
    assert "chardet" in pins, (
        "chardet ไม่ถูก pin — reportlab ลากมันเข้ามาแบบไม่ระบุเวอร์ชัน "
        "แล้วมันไหลไปชนกับ requests"
    )
    assert Version(pins["chardet"]) < Version("6"), (
        f"chardet=={pins['chardet']} อยู่นอกช่วงที่ requests รองรับ (<6) "
        "→ import requests จะพ่น RequestsDependencyWarning ทุกครั้ง"
    )


# --------------------------------------------------------------------------
# D4.2 — render.yaml ต้องไม่ค้างอยู่ที่สภาพก่อนถอด Groq/Google
# --------------------------------------------------------------------------


def _render_env_keys() -> set[str]:
    # image ของชุดเทสต์ไม่มี PyYAML (เหตุผลเดียวกับ tests/test_ci_workflow.py)
    return set(re.findall(r"^\s*-\s*key:\s*(\S+)", RENDER.read_text(encoding="utf-8"), re.MULTILINE))


def test_render_yaml_ไม่ประกาศคีย์ของ_provider_ที่ถอดออกแล้ว():
    """`grep -rn groq --include=*.py .` ไม่เจอโค้ดเรียกเลย และ google-genai ถูกถอด
    ออกจาก requirements ตั้งแต่ 2026-07 — คีย์ที่เหลือค้างชวนให้คนตั้ง secret
    ที่ไม่มีอะไรอ่าน และทำให้เข้าใจผิดว่ายังมี fallback"""
    dead = {"GROQ_API_KEY", "GOOGLE_API_KEY"} & _render_env_keys()
    assert not dead, f"render.yaml ยังประกาศคีย์ที่ระบบไม่ใช้แล้ว: {sorted(dead)}"


def test_render_yaml_ประกาศตัวแปรที่_backend_ต้องใช้จริง():
    """ไม่มี VAULTIS_API_KEY = route ที่ป้องกันไว้ตอบ 503 ทั้งหมดบน Render
    (คำขอมาจาก IP สาธารณะ ข้อยกเว้น localhost ไม่มีผล) ส่วน DATABASE_URL /
    VAULTIS_DB_PATH / TELEGRAM_* / NEWSAPI_KEY คือช่องทางที่ฟีเจอร์อื่นต้องใช้"""
    keys = _render_env_keys()
    required = {
        "ANTHROPIC_API_KEY",
        "VAULTIS_API_KEY",
        "VAULTIS_ALLOWED_ORIGINS",
        "DATABASE_URL",
        "VAULTIS_DB_PATH",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "NEWSAPI_KEY",
    }
    missing = sorted(required - keys)
    assert not missing, f"render.yaml ไม่ได้ประกาศ: {missing}"


def test_render_yaml_ไม่ระบุชื่อโมเดลที่ขัดกับ_llm_py():
    """คอมเมนต์ในไฟล์ deploy เป็นเอกสารที่คนอ่านตอนตั้ง secret — ถ้ามันบอกชื่อโมเดล
    ต้องเป็นชื่อเดียวกับ analysis/llm.ANTHROPIC_MODEL"""
    text = RENDER.read_text(encoding="utf-8")
    model = re.search(
        r'^ANTHROPIC_MODEL\s*=\s*"([^"]+)"',
        (ROOT / "analysis" / "llm.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    ).group(1)
    for line in text.splitlines():
        if "claude" not in line.lower():
            continue
        assert _norm_text(model) in _norm_text(line), (
            f"render.yaml อ้างชื่อโมเดลที่ไม่ตรงกับ ANTHROPIC_MODEL={model!r}: {line.strip()!r}"
        )


# --------------------------------------------------------------------------
# D4.3 — README (repo สาธารณะ) ต้องไม่โฆษณา provider ที่ไม่มีโค้ดเรียก
# --------------------------------------------------------------------------

_REMOVAL_MARKERS = ("ถอด", "เดิม", "ยกเลิก", "ไม่ได้ใช้", "เลิกใช้", "removed")


def test_readme_ไม่เสนอ_groq_หรือ_google_เป็นผู้ให้บริการปัจจุบัน():
    text = README.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"groq|google\s*genai|GOOGLE_API_KEY|GROQ_API_KEY", line, re.I)
        and not any(m in line for m in _REMOVAL_MARKERS)
    ]
    assert not offenders, (
        "README เสนอ provider ที่ไม่มีโค้ดเรียกแล้วว่ายังใช้อยู่ "
        "(เขียนเป็นบันทึกว่า 'ถอดออกแล้ว' ได้ แต่ห้ามเขียนเป็นสถานะปัจจุบัน): "
        + " | ".join(offenders)
    )


def test_readme_ระบุ_ANTHROPIC_API_KEY_ซึ่งเป็นคีย์เดียวที่ระบบใช้():
    assert "ANTHROPIC_API_KEY" in README.read_text(encoding="utf-8"), (
        "README ไม่เอ่ยถึง ANTHROPIC_API_KEY เลย ทั้งที่เป็นคีย์ LLM ตัวเดียวของระบบ"
    )


# --------------------------------------------------------------------------
# D4.4 — CLAUDE.md ต้องบอกโมเดลของ slip OCR ให้ตรงกับโค้ด
# --------------------------------------------------------------------------


def _slip_ocr_model() -> str:
    src = TRANSACTIONS.read_text(encoding="utf-8")
    return re.search(r'model="([^"]+)"', src).group(1)


def _claude_md_lines_with(*needles: str) -> list[str]:
    return [
        line
        for line in CLAUDE_MD.read_text(encoding="utf-8").splitlines()
        if all(n in line for n in needles)
    ]


def test_claude_md_ระบุโมเดล_slip_ocr_ตรงกับที่โค้ดฮาร์ดโค้ด():
    model = _slip_ocr_model()
    lines = _claude_md_lines_with("slip OCR", "ANTHROPIC_API_KEY")
    assert lines, "หาแถวตาราง environment variable ที่พูดถึง slip OCR ใน CLAUDE.md ไม่เจอ"
    for line in lines:
        assert _norm_text(model) in _norm_text(line), (
            f"CLAUDE.md ระบุโมเดลของ slip OCR ไม่ตรงกับโค้ด (โค้ดใช้ {model!r}): {line.strip()!r}"
        )


def test_claude_md_บอกว่า_slip_ocr_ไม่ผ่านการ_log_ค่าใช้จ่าย():
    """เส้นทาง slip OCR สร้าง ``anthropic.Anthropic()`` เอง จึงไม่ผ่าน ``_log_cost()``
    ของ analysis/llm.py — ค่าใช้จ่ายของมันไม่ปรากฏใน log ที่ CLAUDE.md โฆษณาไว้
    ถ้าโค้ดยังไม่ log เอกสารต้องเขียนข้อยกเว้นนี้ให้ชัด"""
    src = TRANSACTIONS.read_text(encoding="utf-8")
    if "_log_cost" in src:
        return  # โค้ด log เองแล้ว — ไม่ต้องมีข้อยกเว้นในเอกสาร
    lines = _claude_md_lines_with("slip OCR")
    assert any(
        ("_log_cost" in line or "ไม่ log" in line or "ไม่ได้ log" in line) for line in lines
    ), (
        "CLAUDE.md โฆษณาว่าทุกการเรียก LLM จะ log โทเคน+ค่าใช้จ่าย แต่เส้นทาง slip OCR "
        "ไม่เรียก _log_cost() และเอกสารไม่ได้เขียนข้อยกเว้นนี้ไว้"
    )
