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

รอบสอง (AUDIT_ROUND2_2026-08-07) เพิ่มอีกสามข้อในตระกูลเดียวกัน — เอกสาร/ไฟล์ deploy
พูดคนละเรื่องกับโค้ด และคราวนี้พิสูจน์ได้ด้วยการดึงความจริงจาก ``backend.main.app`` ตรง ๆ:

    R2-A  ตาราง "Backend Router Map" ประกาศตัวเองว่าเป็นแผนที่ router แต่ลิสต์ 11
          จาก 18 prefix ที่ลงทะเบียนจริง — /api/goals /api/reports /api/networth
          /api/debt /api/cashflow /api/emergency-fund /api/dca /api/macro ไม่ถูก
          พูดถึงที่ใดเลย ⇒ คนอ่าน (รวมถึง Claude Code ที่ใช้ CLAUDE.md เป็น context หลัก)
          ไม่รู้ว่ามีอยู่ แล้วเขียน route ซ้ำ ซึ่งเป็นบั๊กที่ tests/test_route_uniqueness.py
          ถูกเขียนขึ้นมากันพอดี
    R2-B  หัวข้อ "Scheduled Jobs" เขียน "APScheduler — daily screener only" แต่
          backend/main.py:77 ลงทะเบียน generate_and_save_report เป็น cron วันที่ 1
          08:00 ซึ่งยิง Telegram จริง · และย่อหน้าขึ้นต้นว่า "Two separate scheduling
          systems" แล้วไล่รายการ 3 ข้อ ⇒ คนที่อ่านเพื่อนับว่า "อะไรบ้างที่ส่งออกไป
          ข้างนอกโดยอัตโนมัติ" นับไม่ครบ
    R2-C  step "Run Sentiment Analysis" ใน .github/workflows/scheduler.yml ส่ง secrets
          5 ตัวเข้าไปทุกวันจันทร์ แต่ไม่เคยตั้ง VAULTIS_LLM_AUTO ที่ run_sentiment_job()
          บังคับ ⇒ ทุกรอบ return ก่อนแตะฐานข้อมูล ตาราง sentiment ว่างเปล่าถาวร
    R2-D  ตัวแปรสองตัวที่เพิ่มเข้ามาในรอบนี้ (VAULTIS_WS_URL, VAULTIS_LOG_LEVEL) ถูก
          โค้ดอ่านจริงแต่ไม่มีชื่ออยู่ในตาราง Environment Variables ของ CLAUDE.md
          ไม่มีใน .env.example และไม่มีใน docker-compose.yml เลยสักที่:
            $ grep -n "VAULTIS_WS_URL" docker-compose.yml .env.example README.md → ไม่พบ
          ⇒ โหมดรันหลักของโปรเจกต์ (Docker) เป็นโหมดเดียวที่ *ต้อง* ตั้ง VAULTIS_WS_URL
          แต่ไม่มีทางรู้ว่ามีตัวแปรนี้อยู่ (แถบราคาเรียลไทม์ขึ้น "⚠️ ดึงไม่ได้" ตลอด)
          เทสต์ท้ายไฟล์จึงไล่ชื่อ VAULTIS_* ที่โค้ดอ่านจริงมาเทียบกับเอกสาร แทนที่จะ
          หวังให้คนเขียนจำได้เอง
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
BACKEND_MAIN = ROOT / "backend" / "main.py"
SCHEDULER_WORKFLOW = ROOT / ".github" / "workflows" / "scheduler.yml"

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
    """ชื่อโมเดลที่เส้นทาง slip OCR ยิงจริง อ่านจากซอร์ส

    เดิมตัวอ่านนี้จับ ``model="..."`` ตรง ๆ ในคำขอ เพราะโค้ดเขียนชื่อโมเดลเป็น literal
    ตรงจุดที่เรียก Anthropic แต่ 2026-08-08 โค้ดย้ายชื่อไปเป็นค่าคงที่ ``OCR_MODEL``
    **โดยตั้งใจ**: ชื่อเดียวกันนี้ถูกใช้สองบทบาทพร้อมกัน — เป็นโมเดลที่ยิงจริง และเป็นคีย์ที่
    เปิดตาราง ``_MODEL_PRICES_USD_PER_MTOK`` ตอน ``log_anthropic_usage()`` คิดต้นทุน
    ถ้าปล่อยให้เป็น literal สองที่ การอัปเกรดโมเดลแล้วลืมแก้อีกที่จะทำให้ log รายงานราคาของ
    โมเดลผิดตัวโดยไม่มีอะไรร้อง (AUDIT_ROUND2_2026-08-07)

    ตัวอ่านจึงตามไปที่ค่าคงที่ก่อน แล้วค่อยถอยไปหา literal ในคำขอ เผื่อวันหนึ่งมีคนเขียนกลับเป็น
    literal — สิ่งที่เทสต์นี้ตรวจไม่เปลี่ยน คือ "ชื่อที่โค้ดยิงจริงต้องตรงกับที่ CLAUDE.md เขียน"
    และถ้าหาไม่เจอทั้งสองรูป ต้องล้มเสียงดังพร้อมบอกว่าให้ไปแก้ตัวอ่าน ไม่ใช่ ``AttributeError``
    บน ``None`` ที่อ่านไม่ออกว่าตาข่ายขาดตรงไหน
    """
    src = TRANSACTIONS.read_text(encoding="utf-8")
    match = re.search(r'^OCR_MODEL\s*=\s*"([^"]+)"', src, re.MULTILINE) or re.search(
        r'model="([^"]+)"', src
    )
    assert match is not None, (
        "อ่านไม่ออกว่า backend/routers/transactions.py ยิงโมเดลอะไรสำหรับ slip OCR "
        '(ไม่เจอทั้งค่าคงที่ OCR_MODEL = "..." และ literal model="...") — '
        "ถ้าเปลี่ยนวิธีประกาศชื่อโมเดล ต้องอัปเดตตัวอ่านนี้ด้วย ไม่งั้นตาข่ายที่เทียบเอกสารกับ"
        "โค้ดจะขาดเงียบ ๆ"
    )
    return match.group(1)


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


# ถ้อยคำที่แปลว่า "ค่าใช้จ่ายของ slip OCR ไม่ถูก log" — ใช้ตรวจทั้งสองทิศ
_ยังไม่ได้_LOG = ("ไม่ log", "ไม่ได้ log", "does not go through", "not go through",
                  "never shows up", "ไม่ผ่าน _log_cost", "ไม่เข้า log")


def test_claude_md_กับโค้ดต้องตรงกันว่า_slip_ocr_log_ค่าใช้จ่ายหรือไม่():
    """เอกสารกับโค้ดต้องเห็นตรงกันว่าเงินที่จ่ายให้ slip OCR ปรากฏใน log ต้นทุนหรือเปล่า

    เดิมเทสต์นี้ตรวจทิศเดียว: โค้ดไม่ log ⇒ CLAUDE.md ต้องเขียนข้อยกเว้นไว้ ตัวคุมคือ
    ``if "_log_cost" in src: return`` เพราะตอนนั้น "log เอง" แปลว่าเรียก ``_log_cost()`` ตรง ๆ

    2026-08-08 โค้ดเปลี่ยนโดยตั้งใจ: ``routers/transactions.py`` เรียก
    ``analysis.llm.log_anthropic_usage()`` ซึ่งเป็นตัวที่ funnel เข้า ``_log_cost()`` อีกที
    ผลคือคำว่า ``_log_cost`` หายไปจากซอร์ส ตัวคุมจึงไม่ทำงาน เทสต์ไหลไปที่ assert เดิมและ
    **ผ่านด้วยเหตุผลผิด** — บรรทัด CLAUDE.md บรรทัดใหม่บังเอิญมีสตริง ``_log_cost()`` อยู่ใน
    ประโยคที่พูดตรงกันข้าม (ว่า OCR *log* แล้ว) ⇒ ตาข่ายนี้ผ่านทั้งที่โค้ด log และไม่ log
    เท่ากับไม่ได้ตรวจอะไรเลย

    เขียนใหม่ให้ตรวจสองทิศ เพราะความเสี่ยงมีสองด้านจริง ๆ และอันตรายพอกัน:
    เงินที่จ่ายจริงแต่ไม่โผล่ใน log (ผู้ใช้เห็นยอดต่ำกว่าความจริง) กับเอกสารที่ยังบอกว่า
    "ไม่ log" ทั้งที่โค้ด log แล้ว (คนอ่านไปแก้ตามเอกสารที่ตายแล้ว) — AUDIT_ROUND2_2026-08-07
    """
    src = TRANSACTIONS.read_text(encoding="utf-8")
    # "log เอง" = **เรียก** _log_cost() ตรง ๆ หรือ log_anthropic_usage() ซึ่ง funnel เข้าที่เดียวกัน
    # ต้องเป็นการเรียก (มีวงเล็บตาม) ไม่ใช่แค่ชื่อโผล่ในไฟล์ — ตอนพิสูจน์ด้วย mutation พบว่า
    # ถ้าเช็กแค่ substring การถอดบรรทัดที่เรียกออกจะยังผ่าน เพราะ ``from analysis.llm import
    # log_anthropic_usage`` ที่หัวไฟล์ทำให้ชื่อยังอยู่ครบ = ตรวจ import ไม่ได้ตรวจว่าใช้จริง
    โค้ด_log_เอง = re.search(r"(?:_log_cost|log_anthropic_usage)\s*\(", src) is not None
    lines = _claude_md_lines_with("slip OCR")
    assert lines, "หาบรรทัดที่พูดถึง slip OCR ใน CLAUDE.md ไม่เจอ"

    if โค้ด_log_เอง:
        ตกยุค = [line for line in lines if any(n in line for n in _ยังไม่ได้_LOG)]
        assert not ตกยุค, (
            "โค้ด slip OCR รายงานต้นทุนเองแล้ว (log_anthropic_usage/_log_cost) "
            "แต่ CLAUDE.md ยังเขียนว่าค่าใช้จ่ายของมันไม่เข้า log — "
            "เอกสารที่ตกยุคแบบนี้อันตรายกว่าไม่มีเอกสาร เพราะคนอ่านจะไปแก้ตามมัน: "
            + " | ".join(l.strip()[:200] for l in ตกยุค)
        )
        return

    # ต้องเป็นการ "เขียนข้อยกเว้นให้ชัด" จริง ๆ ไม่ใช่แค่เอ่ยชื่อ ``_log_cost`` ลอย ๆ
    # เดิมยอมรับ ``"_log_cost" in line`` เป็นตัวแทนของ "เอกสารพูดถึงข้อยกเว้นนี้" ซึ่งใช้ได้
    # ตอนที่เหตุผลเดียวที่เอกสารจะเอ่ย ``_log_cost`` ข้าง ๆ คำว่า slip OCR คือการบอกข้อยกเว้น
    # พอเอกสารเปลี่ยนไปเอ่ยชื่อเดียวกันเพื่อบอกเรื่อง**ตรงกันข้าม** (ว่า OCR log แล้ว)
    # ตัวแทนนั้นก็ตายทันที — mutation ที่ถอดการ log ออกยังผ่านฉลุย จึงต้องเรียกร้องถ้อยคำที่
    # ยืนยันว่า "ไม่ log" ตรง ๆ
    assert any(any(n in line for n in _ยังไม่ได้_LOG) for line in lines), (
        "CLAUDE.md โฆษณาว่าทุกการเรียก LLM จะ log โทเคน+ค่าใช้จ่าย แต่เส้นทาง slip OCR "
        "ไม่เรียก _log_cost()/log_anthropic_usage() และเอกสารไม่ได้เขียนข้อยกเว้นนี้ไว้ "
        "⇒ เงินที่จ่ายให้ OCR ทุกใบจะหายไปจากยอดที่ผู้ใช้มองเห็น โดยไม่มีอะไรบอก"
    )


# --------------------------------------------------------------------------
# R2-A — "Backend Router Map" ต้องครบทุก prefix ที่แอปลงทะเบียนจริง
# --------------------------------------------------------------------------

# path ที่ FastAPI/Starlette แถมมาเอง ไม่ใช่ router ของโปรเจกต์
_FRAMEWORK_PATHS = {"/health", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


def _prefix_of(path: str) -> str | None:
    """ย่อ path เป็น "กลุ่ม" ที่ตารางในเอกสารพูดถึง.

    ``/api/goals/{goal_id}/progress`` → ``/api/goals`` · ``/ws/prices`` → ``/ws/prices``
    (นอก ``/api`` ไม่มีชั้นให้ย่อ จึงเทียบทั้งเส้น)
    """
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    if parts[0] == "api":
        return f"/api/{parts[1]}" if len(parts) > 1 else None
    return "/" + "/".join(parts)


def _registered_prefixes() -> set[str]:
    """ความจริงจากโค้ด — ดึงจาก ``backend.main.app.routes`` ไม่ใช่จากการอ่านซอร์สด้วยตา."""
    import os
    import tempfile

    # ``backend.database`` สร้างไฟล์ SQLite ตอน import — ชี้ไป tmp ไม่ให้แตะฐานจริง
    os.environ.setdefault(
        "VAULTIS_DB_PATH", str(Path(tempfile.gettempdir()) / "vaultis_test_docs.db")
    )
    # อ่าน app.routes เฉย ๆ ไม่เข้า lifespan ⇒ scheduler ไม่ถูกจุด ไม่มีการยิงเน็ต
    from backend.main import app

    out = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path or path in _FRAMEWORK_PATHS:
            continue
        prefix = _prefix_of(path)
        if prefix:
            out.add(prefix)
    return out


def _documented_prefixes() -> set[str]:
    """prefix ที่ตาราง "Backend Router Map" ใน CLAUDE.md พูดถึง (ย่อแบบเดียวกัน)."""
    section = _claude_md_section("Backend Router Map")
    out = set()
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells:
            continue
        m = re.search(r"`([^`]+)`", cells[0])
        if not m:
            continue
        prefix = _prefix_of(m.group(1))
        if prefix:
            out.add(prefix)
    return out


def _claude_md_section(title: str) -> str:
    """ข้อความใต้หัวข้อ ``## <title>`` จนถึงหัวข้อระดับ ``##`` ตัวถัดไป."""
    text = CLAUDE_MD.read_text(encoding="utf-8")
    m = re.search(rf"^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    assert m, f"หาหัวข้อ '## {title}' ใน CLAUDE.md ไม่เจอ"
    return m.group(1)


def test_backend_router_map_ครบทุก_prefix_ที่ลงทะเบียนจริง():
    """CLAUDE.md คือ context หลักของทั้งคนและ Claude Code — prefix ที่ไม่ถูกพูดถึง
    เท่ากับไม่มีอยู่ในสายตาคนเขียนโค้ดคนถัดไป แล้ว route ซ้ำก็เกิดขึ้นแบบเงียบ ๆ
    (FastAPI จับคู่ตัวแรกที่ตรงเสมอ ตัวหลังกลายเป็นโค้ดตายโดยไม่มี error)"""
    missing = sorted(_registered_prefixes() - _documented_prefixes())
    assert not missing, (
        f"ตาราง 'Backend Router Map' ใน CLAUDE.md ตกไป {len(missing)} prefix: {missing} — "
        "ทุก prefix ที่ backend.main.app ลงทะเบียนต้องมีแถวของตัวเองในตาราง"
    )


def test_backend_router_map_ไม่ลิสต์_prefix_ที่ไม่มีอยู่จริง():
    """ทางกลับกันก็เป็นเอกสารที่โกหก — คนยิงตามแล้วได้ 404"""
    ghosts = sorted(_documented_prefixes() - _registered_prefixes())
    assert not ghosts, (
        f"CLAUDE.md ลิสต์ prefix ที่แอปไม่ได้ลงทะเบียนแล้ว: {ghosts}"
    )


# --------------------------------------------------------------------------
# R2-B — หัวข้อ Scheduled Jobs ต้องตรงกับ job ที่ backend/main.py ลงทะเบียนจริง
# --------------------------------------------------------------------------

_ADD_JOB = re.compile(r"scheduler\.add_job\(\s*([A-Za-z_]\w*)\s*,\s*[\"']cron[\"']([^)]*)\)")
_NUMBER_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def _apscheduler_jobs() -> list[tuple[str, str]]:
    """[(ชื่อฟังก์ชัน, "HH:MM"), ...] ที่ ``backend/main.py`` ลงทะเบียนกับ APScheduler."""
    jobs = []
    for func, args in _ADD_JOB.findall(BACKEND_MAIN.read_text(encoding="utf-8")):
        hour = re.search(r"hour\s*=\s*(\d+)", args)
        minute = re.search(r"minute\s*=\s*(\d+)", args)
        jobs.append((func, f"{int(hour.group(1)):02d}:{int(minute.group(1)) if minute else 0:02d}"))
    return jobs


def test_เอกสารบอกเวลาของงาน_apscheduler_ครบทุกงาน():
    """เดิมเอกสารเขียนว่า APScheduler รัน "daily screener only" ทั้งที่ lifespan
    ลงทะเบียนรายงานรายเดือน (วันที่ 1 08:00) ที่ **ส่ง Telegram** ไว้ด้วย ⇒ คนที่อ่าน
    เอกสารเพื่อนับว่า "อะไรบ้างที่ส่งออกไปข้างนอกโดยอัตโนมัติ" นับไม่ครบ
    (AUDIT_ROUND2_2026-08-07)"""
    jobs = _apscheduler_jobs()
    assert jobs, "อ่าน scheduler.add_job(...) จาก backend/main.py ไม่เจอเลย"
    section = _claude_md_section("Scheduled Jobs")
    missing = [f"{func} @ {clock}" for func, clock in jobs if clock not in section]
    assert not missing, (
        f"หัวข้อ 'Scheduled Jobs' ใน CLAUDE.md ไม่ได้พูดถึงงานเหล่านี้: {missing} — "
        f"backend/main.py ลงทะเบียนไว้ {len(jobs)} งาน"
    )


def test_จำนวนระบบตั้งเวลาที่เขียนไว้ตรงกับรายการที่ไล่จริง():
    """ย่อหน้าขึ้นต้นว่า "Two separate scheduling systems" แล้วไล่รายการ 3 ข้อ —
    ตัวเลขที่ขัดกับรายการข้างล่างตัวเองคือสัญญาณว่าเอกสารถูกแก้ทีละครึ่ง"""
    section = _claude_md_section("Scheduled Jobs")
    m = re.search(r"\b(One|Two|Three|Four|Five)\b\s+separate scheduling systems", section)
    assert m, "หาประโยค '<N> separate scheduling systems' ในหัวข้อ Scheduled Jobs ไม่เจอ"
    listed = len(re.findall(r"^\d+\.\s", section, re.MULTILINE))
    assert _NUMBER_WORDS[m.group(1)] == listed, (
        f"เขียนว่า '{m.group(1)} separate scheduling systems' แต่ไล่รายการไว้ {listed} ข้อ"
    )


# --------------------------------------------------------------------------
# R2-C — งาน sentiment ใน GitHub Actions ต้องไม่ "รันแล้วเงียบ"
# --------------------------------------------------------------------------


def _workflow_steps(path: Path) -> list[str]:
    """แบ่งไฟล์ workflow เป็นบล็อกละ step (ตัดคอมเมนต์ทิ้งก่อน).

    ต้องตัดคอมเมนต์ ไม่งั้นข้อความอธิบายเหนือ step หนึ่งจะถูกนับเป็นเนื้อของ step
    ก่อนหน้า — และที่แย่กว่าคือคอมเมนต์ที่เอ่ยชื่อตัวแปรจะทำให้เทสต์เขียวทั้งที่
    ตัวแปรนั้นไม่ได้ถูกตั้งจริง (image ของชุดเทสต์ไม่มี PyYAML — ดู tests/test_ci_workflow.py)
    """
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    starts = [i for i, line in enumerate(lines) if re.match(r"^\s*-\s+name:", line)]
    blocks = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        blocks.append("\n".join(lines[start:end]))
    return blocks


def _sentiment_steps() -> list[str]:
    return [b for b in _workflow_steps(SCHEDULER_WORKFLOW) if "run_sentiment_job" in b]


def test_step_sentiment_ใน_ci_ตั้ง_VAULTIS_LLM_AUTO():
    """``run_sentiment_job()`` เช็ค ``auto_enabled()`` เป็นบรรทัดแรกแล้ว return ทันที
    ถ้าไม่ได้ตั้ง ``VAULTIS_LLM_AUTO`` — step ที่ส่ง secrets 5 ตัวเข้าไปทุกวันจันทร์
    โดยไม่ตั้งตัวแปรนี้จึงไม่ได้ทำอะไรเลยตั้งแต่วันที่เขียน และตาราง sentiment
    ก็ว่างเปล่าถาวรจนหน้า AI Advisor สัญญาว่า "รอ scheduled job รอบถัดไป" ลอย ๆ
    (AUDIT_ROUND2_2026-08-07)

    ถ้าตัดสินใจปิดงานนี้ ให้ลบ step ทิ้งไปเลย — เทสต์ยอมทั้งสองทาง แต่ไม่ยอมทาง
    ที่สาม: มี step อยู่ ใช้ secrets จริง แล้วไม่มีวันทำงาน"""
    for block in _sentiment_steps():
        assert "VAULTIS_LLM_AUTO" in block, (
            "step ที่เรียก run_sentiment_job() ใน scheduler.yml ไม่ได้ตั้ง VAULTIS_LLM_AUTO "
            "⇒ job จะ return ทันทีทุกรอบ (แต่ยังส่ง secrets เข้า runner):\n" + block
        )


def test_step_sentiment_ต้องมีสวิตช์เปิดปิดที่มองเห็นได้():
    """งานอัตโนมัติที่ "จ่ายเงินทุกรอบ" ต้องไม่ถูกเปิดโดยปริยาย — CLAUDE.md บังคับว่า
    LLM ปิดไว้ก่อนเสมอ ``VAULTIS_LLM_AUTO`` ที่ฮาร์ดโค้ดใน step แปลว่ารันเมื่อไรก็จ่าย
    จึงต้องมีเงื่อนไข ``if:`` ที่อ้าง ``vars.``/``inputs.`` ให้เจ้าของ repo เปิดเอง"""
    for block in _sentiment_steps():
        if "VAULTIS_LLM_AUTO" not in block:
            continue
        cond = " ".join(
            line for line in block.splitlines() if re.match(r"^\s*if\s*:", line)
        )
        assert "vars." in cond or "inputs." in cond, (
            "step sentiment ตั้ง VAULTIS_LLM_AUTO=1 (จ่ายเงินจริง) แต่ไม่มีสวิตช์ให้ปิด — "
            f"เงื่อนไขที่พบ: {cond!r}"
        )


# --------------------------------------------------------------------------
# R2-D — ตัวแปร VAULTIS_* ที่โค้ดอ่านจริง ต้องมีชื่ออยู่ในเอกสาร
# --------------------------------------------------------------------------

ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE = ROOT / "docker-compose.yml"

# โฟลเดอร์ที่ไม่ใช่ซอร์สของแอป (ชุดเทสต์ตั้ง env ของตัวเองเต็มไปหมด ไม่ใช่ "โค้ดอ่านค่า")
_NON_SOURCE_DIRS = {
    ".git",
    ".pytest_cache",
    ".docker-data",
    "__pycache__",
    "tests",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
}

# ตัวแปรที่ **ผู้ใช้เป็นคนตั้งเอง** จึงต้องมีช่องว่างรออยู่ใน .env.example
# (ตัวที่เหลือเป็น path ภายในคอนเทนเนอร์ซึ่ง compose ตั้งให้ ไม่ใช่ปุ่มของผู้ใช้)
_USER_FACING_ENV = {"VAULTIS_API_KEY", "VAULTIS_ALLOWED_ORIGINS", "VAULTIS_WS_URL", "VAULTIS_LOG_LEVEL"}


def _env_names_read_by_code() -> dict[str, str]:
    """``{ชื่อตัวแปร: ไฟล์ที่พบ}`` ของทุกชื่อ ``VAULTIS_*`` ที่ซอร์ส (ไม่นับชุดเทสต์) เขียนไว้เป็นสตริง.

    เกณฑ์คือ "สตริงลิเทอรัล" ไม่ใช่ตัวระบุทั่วไป ด้วยเหตุผลสองข้อ:

    - ``analysis/ai_advisor.py`` มีค่าคงที่ชื่อ ``VAULTIS_ADVISOR_SYSTEM_PROMPT`` ซึ่งเป็น
      *ข้อความ prompt* ไม่ใช่ตัวแปรสภาพแวดล้อม — ไล่ตามชื่อตัวแปรจะได้ของปลอมติดมา
    - หลายจุดอ่าน env ผ่านค่าคงที่ (``os.getenv(LOG_LEVEL_ENV)`` / ``os.getenv(THAI_FONT_ENV)``)
      การไล่เฉพาะรูป ``os.getenv("...")`` จึงมองข้าม ``VAULTIS_LOG_LEVEL`` ไปทั้งตัว
    """
    found: dict[str, str] = {}
    pattern = re.compile(r"""['"](VAULTIS_[A-Z0-9_]+)['"]""")
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if _NON_SOURCE_DIRS & set(Path(rel).parts):
            continue
        for name in pattern.findall(path.read_text(encoding="utf-8")):
            found.setdefault(name, rel)
    return found


def test_ทุกตัวแปร_vaultis_ที่โค้ดอ่านมีแถวของตัวเองใน_claude_md():
    """ตัวแปรที่ไม่มีใครเขียนถึง = ตัวแปรที่ไม่มีใครตั้ง

    ``VAULTIS_WS_URL`` ถูกเพิ่มพร้อมโค้ดที่อ่านมันในรอบนี้ แต่ไม่ถูกเอ่ยถึงใน CLAUDE.md,
    .env.example หรือ docker-compose.yml เลยสักที่ — และ Docker คือโหมดเดียวที่ *ต้อง*
    ตั้งมัน (BACKEND_URL ในคอนเทนเนอร์เป็นชื่อ DNS ภายในที่เบราว์เซอร์ resolve ไม่ได้)
    ผลคือแถบราคาเรียลไทม์ขึ้น "⚠️ ดึงไม่ได้ (WS error)" ตลอดโดยไม่มีเบาะแสว่าต้องตั้งอะไร
    ``VAULTIS_LOG_LEVEL`` มาแบบเดียวกัน (AUDIT_ROUND2_2026-08-07)

    เทสต์นี้ทำให้ "ลืมเขียนเอกสาร" กลายเป็นเทสต์แดง แทนที่จะเป็นสิ่งที่ผู้ใช้ไปเจอเอง
    """
    section = _claude_md_section("Environment Variables")
    missing = sorted(
        f"{name} (อ่านที่ {where})"
        for name, where in _env_names_read_by_code().items()
        if name not in section
    )
    assert not missing, (
        "โค้ดอ่านตัวแปรเหล่านี้แต่หัวข้อ 'Environment Variables' ใน CLAUDE.md ไม่พูดถึง: "
        + " · ".join(missing)
    )


def test_env_example_มีช่องให้ตัวแปรที่ผู้ใช้ต้องตั้งเอง():
    """.env.example คือที่แรกที่คนอ่านตอน `cp .env.example .env` — ตัวแปรที่ไม่อยู่ในนั้น
    เท่ากับไม่มีอยู่จริงสำหรับคนตั้งค่า"""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    known = set(_env_names_read_by_code())
    missing = sorted(name for name in _USER_FACING_ENV & known if name not in text)
    assert not missing, f".env.example ไม่มีตัวแปรที่ผู้ใช้ต้องตั้งเอง: {missing}"


def _compose_text() -> str:
    """docker-compose.yml โดย **ตัดบรรทัดคอมเมนต์ทิ้งก่อน** (image ของชุดเทสต์ไม่มี
    PyYAML — เหตุผลเดียวกับ tests/test_ci_workflow.py จึงอ่านด้วย regex)

    การตัดคอมเมนต์ไม่ใช่เรื่องความสะอาด แต่เป็นเรื่องที่เทสต์จะ "เขียวปลอม":
    คำอธิบายเหนือค่าหนึ่ง ๆ มักเอ่ยชื่อตัวแปรและรูปแบบที่ห้ามใช้ไว้ด้วย ถ้านับรวม
    คอมเมนต์ การลบ *ค่าจริง* ทิ้งจะยังผ่านเพราะชื่อยังอยู่ในคอมเมนต์ (เจอตอนลอง
    mutate จริงระหว่างเขียนเทสต์ชุดนี้) ตัดเฉพาะบรรทัดที่ขึ้นต้นด้วย ``#`` เท่านั้น
    — ห้ามตัด ``#`` ที่ต่อท้ายค่า ไม่งั้นค่าอย่าง ``"127.0.0.1:5432:5432"`` จะเพี้ยน
    """
    return "\n".join(
        line
        for line in COMPOSE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _compose_service_block(name: str) -> str:
    """เนื้อของ service หนึ่งใน docker-compose.yml (ไม่รวมคอมเมนต์)."""
    m = re.search(
        rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", _compose_text(), re.MULTILINE | re.DOTALL
    )
    assert m, f"หา service '{name}' ใน docker-compose.yml ไม่เจอ"
    return m.group(1)


def test_compose_ตั้ง_ws_url_ให้_dashboard_และไม่ใช่ค่าเดียวกับ_backend_url():
    """สองค่านี้อยู่**คนละมุมมองเครือข่าย**: ``BACKEND_URL`` ถูกใช้จากในคอนเทนเนอร์
    ส่วน WS URL ถูกยัดเข้า ``new WebSocket(...)`` ที่รันในเบราว์เซอร์ของผู้ใช้
    คัดลอกค่าเดียวกันมาใส่ = ``ws://backend:8000`` ซึ่ง resolve ไม่ได้นอกเครือข่าย compose
    (AUDIT_ROUND2_2026-08-07)"""
    text = _compose_text()
    ws_lines = [line for line in text.splitlines() if "VAULTIS_WS_URL" in line and ":" in line]
    assert ws_lines, (
        "docker-compose.yml ไม่ได้ตั้ง VAULTIS_WS_URL เลย — โหมดรันหลักของโปรเจกต์คือ "
        "โหมดเดียวที่ต้องตั้งมัน ไม่งั้นแถบราคาเรียลไทม์ขึ้น '⚠️ ดึงไม่ได้' ทุกตัว"
    )
    assert "VAULTIS_WS_URL" in _compose_service_block("dashboard"), (
        "VAULTIS_WS_URL ต้องอยู่ที่ service dashboard (service เดียวที่อ่านมัน)"
    )
    backend_host = re.search(r"BACKEND_URL:\s*\S+://([^:/\s]+)", text)
    assert backend_host, "อ่านโฮสต์ของ BACKEND_URL จาก docker-compose.yml ไม่ได้"
    for line in ws_lines:
        assert backend_host.group(1) not in line, (
            f"VAULTIS_WS_URL ใช้โฮสต์เดียวกับ BACKEND_URL ({backend_host.group(1)}) ซึ่ง "
            f"เบราว์เซอร์บนโฮสต์ resolve ไม่ได้: {line.strip()!r}"
        )


def test_compose_ประกาศ_log_level_ให้เห็นว่าหรี่ได้():
    """ระดับ log เป็นปุ่มที่คนหาเวลาไล่ปัญหาในคอนเทนเนอร์ — ประกาศไว้ใน compose
    เพื่อให้เห็นว่ามีอยู่ (ค่าดีฟอลต์ INFO อยู่ในโค้ดอยู่แล้ว)"""
    assert "VAULTIS_LOG_LEVEL" in _compose_text(), (
        "docker-compose.yml ไม่ได้ตั้ง VAULTIS_LOG_LEVEL ให้ service ไหนเลย"
    )


def test_service_ที่ประกาศ_environment_เองต้อง_merge_ของกลางเข้ามาด้วย():
    """กับดักของ YAML: ``<<: *app`` รวมแบบ **ชั้นเดียว** — service ที่เขียน
    ``environment:`` ของตัวเองจะ *แทนที่* ก้อนของ x-app ทั้งก้อน ไม่ใช่รวมกัน
    เติมตัวแปรตัวเดียวให้ dashboard โดยลืม ``<<: *app-env`` = BACKEND_URL /
    VAULTIS_DB_PATH / DATABASE_URL หายจาก service นั้นทั้งชุด **โดยไม่มี error ให้เห็น**
    (dashboard จะวิ่งไปหา backend ที่ Render แทนตัวในเครื่อง)"""
    for service in ("backend", "dashboard", "scheduler"):
        block = _compose_service_block(service)
        if not re.search(r"^\s{4}environment:\s*$", block, re.MULTILINE):
            continue  # ใช้ของกลางผ่าน <<: *app ตรง ๆ
        env_block = re.search(
            r"^\s{4}environment:\s*$\n(.*?)(?=^\s{4}\S|\Z)", block, re.MULTILINE | re.DOTALL
        ).group(1)
        assert "<<:" in env_block, (
            f"service '{service}' ประกาศ environment: ของตัวเองโดยไม่ merge ของกลาง — "
            "ตัวแปรร่วมทั้งหมดจะหายไปจาก service นี้เงียบ ๆ:\n" + env_block
        )
