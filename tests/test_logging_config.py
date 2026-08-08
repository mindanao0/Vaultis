# -*- coding: utf-8 -*-
"""log ระดับ INFO ของแอปต้องออกมาจริง — AUDIT_ROUND2_2026-08-07.

อาการก่อนแก้ (วัดจริงในคอนเทนเนอร์ที่รันอยู่):

    $ docker compose logs --no-color backend | grep -c 'Screener run complete'  → 0
    $ docker compose logs --no-color backend | grep -c 'scheduler started'       → 0
    $ docker compose logs --no-color backend | grep -c 'ปฏิเสธคำขอ'              → 13   (WARNING ผ่าน)
    $ grep -rn 'basicConfig|dictConfig' backend/ utils/                          → ไม่พบ

ไม่มีที่ไหนตั้งค่า logging เลย และ ``--log-level`` ของ uvicorn ก็ช่วยไม่ได้เพราะมัน
ตั้งเฉพาะ logger ชื่อ ``uvicorn*`` logger ของแอปจึงตกไปที่ root ซึ่งมีแต่ ``lastResort``
handler ระดับ WARNING ⇒ ``logger.info`` ทุกบรรทัดหายเงียบ รวมถึงบรรทัดสรุปของ screener
``"Screener run complete: %d/%d symbols passed, %d ตรวจไม่ได้"`` ที่เขียนไว้เพื่อกฎ C1
โดยเฉพาะ ผลคือแยกไม่ออกระหว่าง "งาน 07:00 รันแล้วไม่เจอสัญญาณ" กับ "งานไม่ได้รันเลย"

เทสต์ชุดนี้ตรึงสองอย่างที่ต่างกัน:

1. ``configure_logging()`` ทำงานถูก (ระดับ, ฟอร์แมต, env, ค่าที่ผิด) — วัดในโปรเซส
   โดยถอด handler ของ root ออกชั่วคราว เพราะ plugin logging ของ pytest ติด handler
   ไว้ที่ root แล้ว ``basicConfig`` จะไม่ทำอะไรเลยถ้าไม่ถอด (เจตนา: ทางเข้าตั้งค่า
   "เมื่อยังไม่มีใครตั้ง" ไม่ใช่แย่งของคนอื่น)
2. **การ import ``backend.main`` ต้องตั้งค่าให้เอง** — ข้อนี้คือหัวใจ วัดในโปรเซสลูก
   ที่สะอาด เพราะภายใต้ pytest ไม่มีทางแยกได้ว่า INFO ที่เห็นมาจากโค้ดเราหรือจาก pytest
3. **ทางเข้าที่สองต้องทำเหมือนกัน** — ``python main.py`` (คอนเทนเนอร์
   ``vaultis-scheduler``) ถูกลืมไปในรอบแรก ทั้งที่เป็นคอนเทนเนอร์ที่บรรทัด INFO
   สำคัญที่สุด: ``analysis/llm.py`` log จำนวนโทเคน+ค่าใช้จ่ายเป็น INFO ซึ่งเป็น
   หลักฐานชิ้นเดียวว่ารอบที่ตั้ง ``VAULTIS_LLM_AUTO=1`` ใช้เงินไปเท่าไร และ
   ``sentiment_analyzer`` log ``"ข้าม sentiment — LLM ปิดอยู่"`` ซึ่งเป็นตัวแยก
   "งานรันแล้วข้ามตัวเอง" ออกจาก "งานไม่ได้รัน"  ตาข่ายเดิมมองไม่เห็นช่องนี้เพราะ
   ``_ENTRY_POINTS`` มีชื่อเดียวและการไล่ ``basicConfig`` ก็ดูแค่โฟลเดอร์ ``backend/``
"""

import logging
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# ไฟล์เดียวในโปรเจกต์ที่ได้รับอนุญาตให้ **นิยาม** การตั้งค่า logging (เรียก
# ``basicConfig``/``dictConfig``) — "one definition per concept" ตาม CLAUDE.md
_LOGGING_OWNER = "backend/main.py"

# "ทางเข้า" ของโปรเซสทุกทางที่ระบบมี — แต่ละทางต้อง **เรียก** ``configure_logging()``
# ของ ``_LOGGING_OWNER`` เอง ไม่ใช่เขียนนิยามที่สอง
#
# เดิมชุดนี้มีแค่ ``backend/main.py`` และเทสต์ท้ายไฟล์ก็ไล่เฉพาะโฟลเดอร์ ``backend/``
# ⇒ ตาข่ายมองไม่เห็นเลยว่าอีกทางเข้าหนึ่ง (``python main.py`` = คอนเทนเนอร์
# ``vaultis-scheduler``) ยังรันด้วย root logger เปล่า ๆ ระดับ WARNING อยู่
# ทั้งที่นั่นคือคอนเทนเนอร์ที่บรรทัด INFO สำคัญที่สุด: ค่าใช้จ่าย LLM ต่อรอบ
# (AUDIT_ROUND2_2026-08-07 — ช่องโหว่ที่เหลือหลังปิดรอบแรก)
_ENTRY_POINTS = {_LOGGING_OWNER, "main.py"}

# โฟลเดอร์ที่ไม่ใช่ซอร์สของโปรเจกต์ (หรือเป็นชุดเทสต์เอง ซึ่งพูดถึง basicConfig
# ในคำอธิบายได้ตามปกติ)
_SKIP_DIRS = {
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


@contextmanager
def _fresh_root_logger(stream):
    """จำลอง root logger ของโปรเซสที่เพิ่งเริ่ม (ไม่มี handler) แล้วคืนสภาพเดิมเสมอ.

    ``logging.basicConfig`` สร้าง ``StreamHandler(sys.stderr)`` โดยอ่าน ``sys.stderr``
    ตอนถูกเรียก จึงต้องสลับ ``sys.stderr`` ก่อนเรียก ไม่ใช่หลัง
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_stderr = sys.stderr
    root.handlers = []
    root.setLevel(logging.WARNING)  # ระดับดีฟอลต์ของ root ตอนโปรเซสเริ่ม
    sys.stderr = stream
    try:
        yield
    finally:
        sys.stderr = saved_stderr
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


class _Buffer:
    """สตรีมจิ๋วที่ ``StreamHandler`` เขียนได้ (io.StringIO ใช้ได้เหมือนกัน
    แต่ตัวนี้อ่านง่ายเวลาเทสต์แดงเพราะเก็บทุกบรรทัดไว้)."""

    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, text: str) -> int:
        self.chunks.append(text)
        return len(text)

    def flush(self) -> None:
        pass

    @property
    def text(self) -> str:
        return "".join(self.chunks)


def _configure(level=None) -> _Buffer:
    from backend.main import configure_logging

    buf = _Buffer()
    with _fresh_root_logger(buf):
        configure_logging(level)
        logging.getLogger("backend.screener.engine").info(
            "Screener run complete: %d/%d symbols passed, %d ตรวจไม่ได้", 2, 5, 1
        )
        logging.getLogger("backend.screener.engine").warning("ปฏิเสธคำขอ")
    return buf


def test_บรรทัดสรุปของ_screener_ระดับ_info_ออกมาจริง():
    """บรรทัดที่เขียนไว้เพื่อรายงานจำนวน "ตรวจไม่ได้" (กฎ C1) ต้องถึงตาคนอ่าน log
    ไม่งั้นตัวเลขที่ตั้งใจให้เตือนก็เท่ากับไม่มี"""
    out = _configure().text
    assert "Screener run complete: 2/5 symbols passed, 1 ตรวจไม่ได้" in out, (
        "logger.info ของแอปไม่ออกมาเลยหลังเรียก configure_logging() — "
        f"สิ่งที่ได้: {out!r}"
    )


def test_ฟอร์แมตมีเวลา_ระดับ_และชื่อ_logger():
    """log ที่ไม่บอกว่าใครพูดตอนไหน ใช้สอบสวนงานตามเวลาไม่ได้"""
    line = _configure().text.splitlines()[0]
    assert "INFO" in line and "backend.screener.engine" in line, line
    assert line.split()[0].count("-") == 2, f"ไม่มีวันที่นำหน้าบรรทัด log: {line!r}"


def test_ระดับปรับได้ด้วย_VAULTIS_LOG_LEVEL(monkeypatch):
    """ต้องหรี่ได้โดยไม่ต้องแก้โค้ด — และหรี่แล้ว WARNING/ERROR ต้องยังผ่าน"""
    monkeypatch.setenv("VAULTIS_LOG_LEVEL", "WARNING")
    out = _configure().text
    assert "Screener run complete" not in out, (
        f"ตั้ง VAULTIS_LOG_LEVEL=WARNING แล้ว INFO ยังออก: {out!r}"
    )
    assert "ปฏิเสธคำขอ" in out, "หรี่เป็น WARNING แล้ว WARNING ต้องยังออก"


def test_ดีฟอลต์คือ_INFO_เมื่อไม่ได้ตั้ง_env(monkeypatch):
    monkeypatch.delenv("VAULTIS_LOG_LEVEL", raising=False)
    assert "Screener run complete" in _configure().text


def test_ค่าระดับที่พิมพ์ผิดไม่ทำให้_backend_ล่ม_แต่ต้องเตือน(monkeypatch):
    """``basicConfig(level="INFOO")`` โยน ValueError — ถ้าปล่อยไว้ตัวแปรที่พิมพ์ผิด
    ตัวเดียวทำให้ backend ทั้งตัว import ไม่ขึ้น การตั้งค่า log ผิดต้องไม่ล้มระบบ
    แต่ต้อง "ดัง" ว่าค่าที่ให้มาไม่ถูกใช้ ไม่ใช่เงียบแล้วแอบใช้ค่าอื่น"""
    monkeypatch.setenv("VAULTIS_LOG_LEVEL", "VERBOSE")
    out = _configure().text
    assert "VERBOSE" in out and "VAULTIS_LOG_LEVEL" in out, (
        f"ระดับ log ที่ไม่รู้จักถูกกลืนเงียบ: {out!r}"
    )
    assert "Screener run complete" in out, "ถอยไปใช้ INFO ตามที่โฆษณาไว้"


def test_ไม่แย่ง_handler_ที่มีคนตั้งไว้ก่อน():
    """deploy ที่ตั้ง dictConfig ของตัวเองไว้แล้ว (หรือ plugin logging ของ pytest)
    ต้องไม่ถูกทางเข้าเขียนทับ — ห้ามใช้ ``force=True``"""
    from backend.main import configure_logging

    root = logging.getLogger()
    before = root.handlers[:]
    if not before:
        pytest.skip("รันด้วย -p no:logging — ไม่มี handler เดิมให้ทดสอบว่าไม่ถูกแย่ง")
    configure_logging()  # ตอนนี้ root มี handler ของ pytest อยู่แล้ว
    assert root.handlers == before, (
        "configure_logging() ไปยุ่งกับ handler ที่มีอยู่ก่อน — "
        "การตั้งค่าของ deploy/ชุดเทสต์จะถูกกลืนหาย"
    )


def test_การ_import_backend_main_ตั้งค่า_logging_ให้เอง(tmp_path):
    """ข้อนี้คือบั๊กตัวจริง: ต่อให้มีฟังก์ชันที่ถูกต้อง ถ้าไม่มีใครเรียกตอนโหลดแอป
    uvicorn ก็ยังทิ้ง INFO ทั้งหมดเหมือนเดิม

    ต้องวัดในโปรเซสลูกที่สะอาด — ภายใต้ pytest มี handler ที่ root อยู่แล้ว
    จึงแยกไม่ออกว่า INFO ที่เห็นมาจากโค้ดเราหรือจาก pytest
    """
    env = dict(os.environ)
    env["VAULTIS_DB_PATH"] = str(tmp_path / "probe.db")  # ห้ามแตะ vaultis.db ของจริง
    env.pop("VAULTIS_LOG_LEVEL", None)
    env.pop("DATABASE_URL", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import logging\n"
            "import backend.main  # noqa: F401  — การ import ต้องพอ\n"
            "logging.getLogger('backend.screener.engine').info('PROBE-INFO-ห้ามหาย')\n",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"import backend.main ล้มเหลว: {proc.stderr[-2000:]}"
    combined = proc.stdout + proc.stderr
    assert "PROBE-INFO-ห้ามหาย" in combined, (
        "โหลด backend.main แล้ว logger.info ยังหายเงียบ — "
        "แปลว่าไม่มีใครเรียก configure_logging() ตอน import "
        f"(stderr: {proc.stderr[-2000:]!r})"
    )


def test_ไม่มีโมดูลไหนในโปรเจกต์ตั้งค่า_logging_เองนอกจากเจ้าของ():
    """``basicConfig`` ที่โรยไว้ตามโมดูลจะทำงานตามลำดับการ import ที่เดาไม่ได้
    ตัวแรกที่ถูกเรียกชนะ ตัวอื่นกลายเป็น no-op เงียบ ๆ ⇒ ระดับ log ของโปรเซสจริง
    ขึ้นกับว่าใคร import ก่อน ไลบรารีต้อง ``getLogger(__name__)`` เฉย ๆ
    แล้วปล่อยให้ทางเข้าเป็นคนตั้งค่า

    เดิมเทสต์นี้ไล่เฉพาะโฟลเดอร์ ``backend/`` — ซึ่งแปลว่าทั้ง ``main.py``,
    ``dashboard/``, ``analysis/``, ``jobs/``, ``scripts/`` เขียน ``basicConfig``
    ของตัวเองได้โดยไม่มีอะไรทัก ตอนนี้ไล่ทั้ง repo และเว้นให้ **ที่เดียว** คือ
    ``_LOGGING_OWNER``  ทางเข้าที่สอง (``main.py``) ก็ถูกตรวจด้วย: มันต้อง
    **ยืม** ``configure_logging()`` ไม่ใช่เขียนนิยามที่สอง
    (AUDIT_ROUND2_2026-08-07)
    """
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == _LOGGING_OWNER:
            continue
        if _SKIP_DIRS & set(Path(rel).parts):
            continue
        src = path.read_text(encoding="utf-8")
        body = "\n".join(
            line for line in src.splitlines() if not line.lstrip().startswith("#")
        )
        if "basicConfig" in body or "dictConfig" in body:
            offenders.append(rel)
    assert not offenders, (
        "โมดูลที่ไม่ใช่เจ้าของการตั้งค่า logging ตั้งค่าเอง: "
        + ", ".join(offenders)
        + f" — ที่นิยามได้มีที่เดียวคือ {_LOGGING_OWNER} "
        "(ทางเข้าอื่นต้อง import ฟังก์ชันนั้นไปเรียก ไม่ใช่เขียนใหม่)"
    )


# --------------------------------------------------------------------------
# ทางเข้าที่สอง: ``python main.py`` (คอนเทนเนอร์ vaultis-scheduler)
# --------------------------------------------------------------------------


def _run_entry_probe(tmp_path, code: str) -> subprocess.CompletedProcess:
    """รันโค้ดสั้น ๆ ในโปรเซสลูกที่สะอาด โดยกันไม่ให้แตะข้อมูลจริงของผู้ใช้.

    ต้องเป็นโปรเซสลูกจริง ๆ: ภายใต้ pytest มี handler ค้างอยู่ที่ root logger แล้ว
    จึงแยกไม่ออกว่าบรรทัด INFO ที่เห็นมาจากโค้ดเราหรือจาก plugin ของ pytest
    """
    env = dict(os.environ)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(exist_ok=True)
    # ``backend.database`` สร้างไฟล์ SQLite ตอน import และ tracker/price_alert
    # อ่าน path ของสมุดบัญชี/คลัง alert ตอน import — ชี้ออกไปนอกไฟล์จริงทั้งหมด
    env["VAULTIS_DB_PATH"] = str(sandbox / "probe.db")
    env["VAULTIS_LEDGER_PATH"] = str(sandbox / "transactions.csv")
    env["VAULTIS_ALERTS_PATH"] = str(sandbox / "price_alerts.json")
    env.pop("VAULTIS_LOG_LEVEL", None)  # ต้องได้ดีฟอลต์ INFO
    env.pop("DATABASE_URL", None)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_ทางเข้า_scheduler_ตั้งค่า_logging_ก่อนแยกงาน(tmp_path):
    """``python main.py`` คือคำสั่งของ service ``vaultis-scheduler`` ใน docker-compose
    ก่อนแก้ มันรันด้วย root logger ที่ไม่มี handler เลย ⇒ ทุกบรรทัด INFO ในคอนเทนเนอร์
    นั้นหายเงียบ (AUDIT_ROUND2_2026-08-07 — ข้อเดียวกับ backend แต่หลุดไปหนึ่งทางเข้า)

    ยิงด้วย ``--job`` ที่ไม่มีอยู่จริงโดยตั้งใจ: มันเดินผ่านการตั้งค่า logging แล้ว
    ไปจบที่ ``ValueError`` ทันที ไม่ต่อเน็ต ไม่แตะไฟล์ของผู้ใช้ และพิสูจน์ด้วยว่า
    การตั้งค่าเกิด **ก่อน** การแยกงาน ไม่ใช่ซ่อนอยู่ในสาขาใดสาขาหนึ่ง
    """
    proc = _run_entry_probe(
        tmp_path,
        "import runpy, sys\n"
        "sys.argv = ['main.py', '--job', '__probe_unknown_job__']\n"
        "runpy.run_path('main.py', run_name='__main__')\n",
    )
    combined = proc.stdout + proc.stderr
    assert "__probe_unknown_job__" in combined, (
        f"ทางเข้าไม่ได้เดินไปถึงจุดแยกงานเลย: {combined[-3000:]!r}"
    )
    assert re.search(r"INFO\s+vaultis\.scheduler", combined), (
        "รัน main.py แล้วไม่มีบรรทัด INFO ของ scheduler ออกมาเลย — แปลว่า root logger "
        "ยังเป็นค่าเริ่มต้น (ระดับ WARNING ไม่มี handler) เหมือนก่อนแก้\n"
        f"ที่ได้: {combined[-3000:]!r}"
    )


def test_บรรทัดค่าใช้จ่าย_llm_ระดับ_info_ออกมาได้จากทางเข้า_scheduler(tmp_path):
    """หัวใจของข้อนี้: บรรทัดที่ ``analysis/llm.py`` เขียนเป็น INFO (จำนวนโทเคน +
    ค่าใช้จ่ายโดยประมาณ) คือ **หลักฐานชิ้นเดียว** ว่ารอบที่ตั้ง ``VAULTIS_LLM_AUTO=1``
    ใช้เงินไปเท่าไร ถ้ามันหายในคอนเทนเนอร์ scheduler ผู้ใช้จะรู้ค่าใช้จ่ายอีกที
    ตอนเปิดบิลของ Anthropic

    ปล่อยโพรบด้วยชื่อ logger จริงของโมดูลนั้น **หลัง** ทางเข้าทำงานจบ — เทสต์ก่อนหน้า
    ตรวจ logger ของตัว scheduler เอง ข้อนี้ตรวจว่า logger ของ *ไลบรารี* ก็ผ่านด้วย
    (ระดับถูกตั้งที่ root ไม่ใช่ที่ logger ตัวใดตัวหนึ่ง)
    """
    proc = _run_entry_probe(
        tmp_path,
        "import logging, runpy, sys\n"
        "sys.argv = ['main.py', '--job', '__probe_unknown_job__']\n"
        "try:\n"
        "    runpy.run_path('main.py', run_name='__main__')\n"
        "except ValueError:\n"
        "    pass\n"
        "logging.getLogger('analysis.llm').info('PROBE-ค่าใช้จ่าย-LLM-ห้ามหาย')\n",
    )
    combined = proc.stdout + proc.stderr
    assert "PROBE-ค่าใช้จ่าย-LLM-ห้ามหาย" in combined, (
        "logger.info ของ analysis.llm ยังถูกทิ้งหลังทางเข้า scheduler ทำงาน — "
        "ค่าใช้จ่าย LLM ของคอนเทนเนอร์นี้จะไม่มีร่องรอยใน log เลย\n"
        f"ที่ได้: {combined[-3000:]!r}"
    )
