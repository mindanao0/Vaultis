# -*- coding: utf-8 -*-
"""ชุดเทสต์ต้อง collect และรันได้โดยไม่ต้องต่อเน็ต (AUDIT_2026-08-06 ข้อ 0-B / D1).

อาการเดิมที่วัดได้:

```
docker run --network none ... pytest -q
ERROR tests/test_etf_analysis.py - ValueError: no OHLCV
ERROR tests/test_screener.py    - IndexError: single positional indexer is out-of-bounds
!!! Interrupted: 2 errors during collection !!!    2 errors in 5.19s
```

สองไฟล์นั้นเขียน ``asyncio.run(test())`` ไว้ที่ระดับโมดูล จึงยิง yfinance จริง
**ตั้งแต่ตอน collect** ⇒ เน็ตล่มเมื่อไหร่ไม่มีเทสต์สักตัวได้รัน และเลข "ผ่าน N ตัว"
ขึ้นกับสถานะเซิร์ฟเวอร์ภายนอกที่โปรเจกต์ไม่ได้คุม

ไฟล์นี้ตรึงสัญญา 4 ข้อ:

1. ไม่มีโมดูลเทสต์ไหนรันงานจริงตอน import (ห้าม call ระดับโมดูล)
2. ``pytest --collect-only`` ต้องสำเร็จเมื่อ socket ถูกปิดสนิท
3. การรัน pytest แบบไม่ใส่ตัวกรอง ต้องไม่เลือกเทสต์ที่พึ่งเน็ตมารัน
4. ทุกไฟล์ ``tests/test_*.py`` ต้องมีเทสต์อย่างน้อย 1 ตัว — ไฟล์ที่ collect ได้
   0 ตัวคือไฟล์ที่ดูเหมือนถูกทดสอบแต่ไม่เคยถูกรันเลย ("ไม่มีเทสต์" ถูกอ่านเป็น "ผ่าน")

และข้อ 5: เทสต์ที่พึ่งเน็ตต้องยัง **เรียกกลับมารันด้วยมือได้** — เป้าหมายคือแยกออก
จากเส้นทางอัตโนมัติ ไม่ใช่ลบทิ้ง
"""

from __future__ import annotations

import ast
import functools
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = _ROOT / "tests"

# โมดูลที่ยอมรับกันว่าต้องต่อเน็ตจริง (ยิง yfinance / Prophet บนข้อมูลจริง)
_NETWORK_MODULES = {
    "test_etf_analysis",
    "test_screener",
    "test_backtest",
    "test_forecast",
}

# ปลั๊กอินปิด socket ก่อน pytest เริ่ม collect — เลียนแบบ `docker run --network none`
# ลำพังตัวนี้ไม่พอ: yfinance 0.2.61 ออกเน็ตผ่าน curl_cffi (libcurl) ซึ่งไม่ผ่าน
# โมดูล socket ของ Python เลย จึงต้องใช้ proxy ที่ต่อไม่ติดคู่กัน (_OFFLINE_ENV)
_NETBLOCK_PLUGIN = '''
"""ปลั๊กอินปิดเน็ต: ทำให้ทุกการเชื่อมต่อขาออกล้มเหมือนเครื่องไม่มีเน็ต."""
import socket


def _deny(*args, **kwargs):
    raise socket.gaierror("network disabled by test")


socket.getaddrinfo = _deny
socket.create_connection = _deny
socket.socket.connect = _deny
socket.socket.connect_ex = _deny
'''

# พอร์ต 1 ไม่มีใครฟัง — ทุกไลบรารีที่เคารพตัวแปร proxy (requests, curl_cffi/libcurl,
# urllib) จะต่อไม่ติดทันที ใช้แทน `--network none` ที่สั่งจากในคอนเทนเนอร์ไม่ได้
_DEAD_PROXY = "http://127.0.0.1:1"
_OFFLINE_ENV = {
    "HTTP_PROXY": _DEAD_PROXY,
    "HTTPS_PROXY": _DEAD_PROXY,
    "http_proxy": _DEAD_PROXY,
    "https_proxy": _DEAD_PROXY,
    "NO_PROXY": "",
    "no_proxy": "",
}

# โมดูลที่แตะเน็ตตรง ๆ — เรียกที่ระดับโมดูลเมื่อไหร่คือยิงเน็ตตอน import
_NETWORK_LIBS = ("yfinance", "yf", "requests", "httpx", "urllib", "praw")


def _import_time_invocations(path: Path) -> list[str]:
    """คืน statement ระดับโมดูลที่ "รันงานจริง" ตอน import.

    จับ 3 รูปแบบที่ทำให้ collect มีผลข้างเคียง:

    * ``asyncio.run(...)`` / ``loop.run_until_complete(...)``
    * เรียกฟังก์ชันที่นิยามในไฟล์เดียวกัน (เช่น ``test_backtest()``) นอก
      ``if __name__ == "__main__"``
    * เรียกไลบรารีเครือข่ายตรง ๆ (``yfinance.download(...)`` ฯลฯ)

    ไม่จับ call ระดับโมดูลทั่วไปที่ไม่มีผลข้างเคียงออกนอกกระบวนการ (เช่น
    ``os.environ.setdefault`` หรือการเตรียมค่าในแคชในหน่วยความจำ)
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    local_funcs = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    found: list[str] = []
    for node in tree.body:
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            continue
        src = ast.unparse(node.value)
        root = src.split("(", 1)[0].split(".", 1)[0]
        callee_tail = src.split("(", 1)[0].rsplit(".", 1)[-1]
        if (
            src.startswith(("asyncio.run(", "asyncio.get_event_loop("))
            or callee_tail == "run_until_complete"
            or src.split("(", 1)[0] in local_funcs
            or any(f"{lib}." in src.split("(", 1)[0] for lib in _NETWORK_LIBS)
            or root in local_funcs
        ):
            found.append(src)
    return found


def _run_pytest(args: list[str], extra_env: dict[str, str] | None = None):
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=str(_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )


@functools.lru_cache(maxsize=None)
def _collect_nodeids(args: tuple[str, ...]) -> tuple[str, ...]:
    """node id ทั้งหมดที่ pytest เลือกได้ด้วยอาร์กิวเมนต์ชุดนี้."""
    proc = _run_pytest(["--collect-only", "-q", *args])
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"collect ล่ม (args={args}):\n{combined[-3000:]}"
    return tuple(
        line.strip()
        for line in proc.stdout.splitlines()
        if "::" in line and not line.strip().startswith(("=", "<", "ERROR", "E "))
    )


def test_no_test_body_runs_at_import_time():
    """ห้ามโมดูลเทสต์รันงานจริงตอน import — collect ต้องไม่มีผลข้างเคียง."""
    offenders = {
        path.name: bad
        for path in sorted(_TESTS_DIR.glob("*.py"))
        if (bad := _import_time_invocations(path))
    }
    assert not offenders, (
        "โมดูลเทสต์รันโค้ดตอน import — เน็ตล่ม/ข้อมูลเปลี่ยนเมื่อไหร่ collect ล่มทั้งชุด: "
        f"{offenders}"
    )


def test_collect_only_succeeds_with_network_disabled(tmp_path):
    """ปิด socket แล้ว `pytest --collect-only` ต้องยังสำเร็จ ไม่มี error สักตัว."""
    plugin_dir = tmp_path / "netblock_pkg"
    plugin_dir.mkdir()
    (plugin_dir / "vaultis_netblock.py").write_text(_NETBLOCK_PLUGIN, encoding="utf-8")

    offline_env = dict(_OFFLINE_ENV)
    offline_env["PYTHONPATH"] = f"{plugin_dir}{os.pathsep}{_ROOT}"
    proc = _run_pytest(
        [
            "--collect-only",
            "-q",
            "-m",
            "network or not network",
            "-p",
            "vaultis_netblock",
        ],
        extra_env=offline_env,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        "collect ล่มเมื่อไม่มีเน็ต — ยังมีโมดูลเทสต์ที่ยิงเน็ตตอน import:\n"
        + combined[-4000:]
    )


def test_default_run_does_not_select_network_tests():
    """รัน pytest แบบปกติ ต้องไม่เลือกเทสต์ที่ต้องต่อเน็ตมารัน."""
    leaked = sorted(
        {
            nid
            for nid in _collect_nodeids(())
            if Path(nid.split("::", 1)[0]).stem in _NETWORK_MODULES
        }
    )
    assert not leaked, (
        "เทสต์ที่ต้องต่อเน็ตยังถูกเลือกมารันแบบปกติ — ผลชุดเทสต์จะขึ้นกับ Yahoo ไม่ใช่โค้ด: "
        f"{leaked}"
    )


def test_every_test_file_collects_at_least_one_test():
    """ไฟล์ชื่อ test_*.py ที่ collect ได้ 0 ตัว = เทสต์ปลอมที่ไม่เคยถูกรัน."""
    with_tests = {
        nid.split("::", 1)[0] for nid in _collect_nodeids(("-m", "network or not network"))
    }
    empty = sorted(
        path.name
        for path in _TESTS_DIR.glob("test_*.py")
        if f"tests/{path.name}" not in with_tests
    )
    assert not empty, f"ไฟล์ชื่อ test_*.py ที่ pytest เก็บเทสต์ได้ 0 ตัว: {empty}"


def test_network_tests_are_still_runnable_on_demand():
    """`pytest -m network` ต้องยังเรียกทั้ง 4 ไฟล์กลับมารันได้ — แยกออก ไม่ใช่ลบทิ้ง."""
    stems = {Path(nid.split("::", 1)[0]).stem for nid in _collect_nodeids(("-m", "network"))}
    missing = sorted(_NETWORK_MODULES - stems)
    assert not missing, (
        f"หายไปจาก `pytest -m network`: {missing} — เทสต์ที่พึ่งเน็ตต้องยังรันด้วยมือได้"
    )
