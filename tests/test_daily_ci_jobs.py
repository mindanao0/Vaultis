# -*- coding: utf-8 -*-
"""งานอัตโนมัติรายวัน/รายสัปดาห์ — AUDIT_2026-08-06 ข้อ A7.

ครอบ 6 ข้อในสายเดียวกัน (``.github/workflows/scheduler.yml`` + ``jobs/daily_check.py`` + ``main.py``):

* **H12** — CI ส่ง "Daily Price Check" 2 ใบต่อวันไปเว็บฮุคเดียวกัน แล้วเลขขัดกันเอง
  เพราะใบหนึ่งคิดจากแท่งปิด EOD อีกใบคิดจาก ``fast_info['previous_close']``
* **M-CI-1** — ``secrets.BACKEND_URL`` ที่ยังไม่ตั้ง = สตริงว่าง ⇒ ค่า default ในโค้ดไม่มีผล
  และ ``timeout=30`` สั้นกว่า cold start ของ Render free (วัดได้ 80.78 วิ)
* **M-CI-2** — Weekly Summary กุ ``1W +0.00%`` จาก ``ffill()``
* **M-CI-3** — ``generate_daily_technical_alerts()`` ส่งราคาเก่าเป็นราคาวันนี้ (``previous_price == price``)
* **M-CI-4** — scheduler ตายถาวรเมื่อ job ใดโยน exception (``try/except`` อยู่นอกลูป)
* **L-CI-1** — ``permissions: contents: write`` ที่ไม่มีอะไรใช้แล้ว

รวมถึงข้อสืบเนื่องจาก H10: ``/api/etf/daily-snapshot`` ติดธง ``stale``/``error`` รายตัวแล้ว
แต่ ``jobs/daily_check`` ยังไม่เคารพธงนั้น (AUDIT_2026-08-06 บรรทัด "jobs/daily_check ต้องเคารพธง stale")

เทสต์ทั้งไฟล์ทำงานแบบออฟไลน์: yfinance / requests / Discord ถูก stub ทุกเส้นทาง
"""

from __future__ import annotations

import importlib
import re
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import jobs.daily_check as dc  # noqa: E402
import main as scheduler_main  # noqa: E402

WORKFLOW = _ROOT / ".github" / "workflows" / "scheduler.yml"


# --------------------------------------------------------------------------- #
# ตัวช่วยอ่าน workflow (image ของชุดเทสต์ไม่มี PyYAML — โครงที่ต้องอ่านตื้นพอ)
# --------------------------------------------------------------------------- #
def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _steps(text: str) -> list[dict[str, str]]:
    """แยกบล็อก ``steps:`` เป็นรายการ step — คืน ``{"raw", "name", "if"}`` ต่อ step."""
    lines = text.splitlines()
    out: list[dict[str, str]] = []
    inside = False
    indent = None
    current: list[str] | None = None
    for line in lines:
        if not inside:
            if re.match(r"^\s*steps:\s*$", line):
                inside = True
            continue
        if line.strip() and not line.startswith(" "):  # กลับไปคีย์ระดับบนสุด
            break
        stripped = line.lstrip()
        line_indent = len(line) - len(stripped)
        if stripped.startswith("- "):
            if indent is None:
                indent = line_indent
            if line_indent == indent:
                if current is not None:
                    out.append(_step_from(current))
                current = [line]
                continue
        if current is not None:
            current.append(line)
    if current is not None:
        out.append(_step_from(current))
    return out


def _step_from(block: list[str]) -> dict[str, str]:
    raw = "\n".join(block)
    body = "\n".join(l for l in block if not l.lstrip().startswith("#"))
    name = ""
    cond = ""
    for line in block:
        s = line.strip().lstrip("- ").strip()
        if s.startswith("name:") and not name:
            name = s[len("name:") :].strip()
        if s.startswith("if:") and not cond:
            cond = s[len("if:") :].strip()
    return {"raw": raw, "body": body, "name": name, "if": cond}


def _sends_daily_price_summary(step: dict[str, str]) -> bool:
    """step นี้ยิงสรุปราคารายวันเข้า Discord หรือไม่.

    สองคำสั่งที่ทำสิ่งเดียวกัน: ``main.py --job price_alert`` (→ ``check_alerts()``
    ซึ่งส่ง embed "Daily Price Check" ทุกครั้งที่รัน) และ ``jobs/daily_check.py``
    """
    body = step["body"]
    if "DISCORD_WEBHOOK_URL" not in body:
        return False
    return bool(
        re.search(r"main\.py\s+--job\s+price_alert", body)
        or re.search(r"jobs/daily_check\.py", body)
    )


# --------------------------------------------------------------------------- #
# H12 + L-CI-1 — scheduler.yml
# --------------------------------------------------------------------------- #
class TestWorkflow:
    def test_สรุปราคารายวันต้องมีใบเดียว(self):
        """H12: สอง step เงื่อนไข ``if:`` เดียวกัน เว็บฮุคเดียวกัน พาดหัวเดียวกัน

        แต่คนละฐานข้อมูล (แท่งปิด EOD vs ``fast_info['previous_close']``)
        → ผู้ใช้ได้ 2 ใบต่อวันที่ตัวเลขขัดกันแบบ deterministic ทุกวันทำการ
        """
        senders = [s for s in _steps(_workflow_text()) if _sends_daily_price_summary(s)]
        assert len(senders) <= 1, (
            "มีมากกว่าหนึ่ง step ที่ส่งสรุปราคารายวันเข้า Discord: "
            f"{[s['name'] for s in senders]} — เว็บฮุคเดียวกัน พาดหัว 'Daily Price Check' "
            "เหมือนกัน แต่คิด %เปลี่ยนแปลงคนละฐาน ⇒ เลขขัดกันเองทุกวันทำการ"
        )

    def test_ไม่มี_step_ที่เรียก_price_alert_ใน_ci(self):
        """CI มองไม่เห็น ``alerts/data/price_alerts.json`` (gitignore) —

        ``--job price_alert`` ที่นั่นจึงไม่ได้ตรวจ alert อะไรเลย เหลือแค่ผลข้างเคียง
        คือสรุปราคาใบที่สอง
        """
        text = _workflow_text()
        body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        assert not re.search(r"main\.py\s+--job\s+price_alert", body), (
            "scheduler.yml ยังเรียก `main.py --job price_alert` — ใน CI มันตรวจ alert ไม่ได้ "
            "(ไฟล์คลังถูก gitignore) เหลือแต่การส่งสรุปราคาซ้ำใบที่สอง"
        )

    def test_permissions_เป็น_read(self):
        """L-CI-1: step ที่ต้องเขียน repo ถูกถอดไปแล้ว — สิทธิ์เขียนที่เหลือคือความเสี่ยงเปล่า ๆ"""
        text = _workflow_text()
        body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        assert "contents: write" not in body, (
            "workflow ยังถือ `contents: write` ทั้งที่ไม่มี step ไหน git push / upload แล้ว"
        )
        assert "contents: read" in body, "ต้องระบุ `contents: read` ไว้ชัดเจน"

    def test_ไม่มี_step_ที่เขียน_repo(self):
        """ตรวจฝั่งเหตุผล: ถ้าไม่มีอะไรเขียน repo แล้ว สิทธิ์เขียนก็ไม่ควรมี"""
        body = "\n".join(
            l for l in _workflow_text().splitlines() if not l.lstrip().startswith("#")
        )
        assert not re.search(r"\bgit (push|commit)\b", body)
        assert "actions/upload" not in body


# --------------------------------------------------------------------------- #
# M-CI-1 — BACKEND_URL / timeout / การรายงานว่า backend ล่ม
# --------------------------------------------------------------------------- #
@pytest.fixture
def reload_daily_check(monkeypatch):
    """โหลด ``jobs.daily_check`` ใหม่ด้วย env ที่กำหนด แล้วคืนสภาพเดิมให้เสมอ"""

    def _load(**env: str | None):
        for key, value in env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        return importlib.reload(dc)

    yield _load
    importlib.reload(dc)


class TestBackendUrl:
    def test_env_ว่างต้องตกไปใช้ค่า_default(self, reload_daily_check):
        """M-CI-1: ``secrets.BACKEND_URL`` ที่ยังไม่ตั้ง ถูกส่งเข้ามาเป็น ``''``

        ``os.environ.get(k, default)`` คืน ``''`` (คีย์มีอยู่จริง) ⇒ URL กลายเป็น
        ``/api/etf/daily-snapshot`` → ``Invalid URL: No scheme supplied``
        """
        mod = reload_daily_check(BACKEND_URL="")
        assert mod.BACKEND_URL.startswith("http"), (
            f"BACKEND_URL = {mod.BACKEND_URL!r} เมื่อ env เป็นสตริงว่าง — "
            "ค่า default ในโค้ดไม่มีผล"
        )

    def test_env_มีช่องว่างล้วนก็ต้องตกไปใช้ค่า_default(self, reload_daily_check):
        mod = reload_daily_check(BACKEND_URL="   ")
        assert mod.BACKEND_URL.startswith("http")

    def test_env_ที่ตั้งจริงยังชนะ(self, reload_daily_check):
        mod = reload_daily_check(BACKEND_URL="http://localhost:8000")
        assert mod.BACKEND_URL == "http://localhost:8000"

    def test_timeout_ครอบ_cold_start(self, reload_daily_check):
        """Render free cold start วัดได้ 80.78 วิ ขณะที่โค้ดตั้ง ``timeout=30``

        ⇒ ถึง secret ถูกตั้งถูกต้อง CI ก็ได้ ``{}`` แล้วตกเส้นทางสำรองอยู่ดี
        """
        mod = reload_daily_check(BACKEND_URL=None)
        assert getattr(mod, "BACKEND_TIMEOUT_SEC", 30) >= 90, (
            "timeout ของคำขอไป backend สั้นกว่า cold start ของ Render free (80.78 วิ)"
        )


# --------------------------------------------------------------------------- #
# ตัวปลอมของ yfinance
# --------------------------------------------------------------------------- #
def _fake_yfinance(closes: dict[str, list[float]], *, fail: set[str] | None = None):
    """โมดูล yfinance ปลอม — ``fast_info`` ระเบิดทันทีที่ถูกแตะ

    (ฐานที่ถูกต้องคือแท่งปิด 2 แท่งจาก ``history()`` ตาม FIX_PLAN 2.2)
    """
    fail = fail or set()
    module = types.ModuleType("yfinance")

    class _Ticker:
        def __init__(self, symbol: str):
            self.symbol = symbol

        @property
        def fast_info(self):  # pragma: no cover - ต้องไม่ถูกเรียก
            raise AssertionError(
                "ห้ามใช้ fast_info['previous_close'] — ต้องใช้แท่งปิด 2 แท่งจาก history "
                "ให้เป็นฐานเดียวกับ /api/etf/daily-snapshot (FIX_PLAN 2.2)"
            )

        def history(self, *args, **kwargs):
            if self.symbol in fail:
                raise RuntimeError("yfinance rate limited")
            values = closes.get(self.symbol)
            if not values:
                return pd.DataFrame()
            idx = pd.bdate_range("2026-08-01", periods=len(values))
            return pd.DataFrame({"Close": values}, index=idx)

    module.Ticker = _Ticker
    return module


# --------------------------------------------------------------------------- #
# jobs/daily_check.py — ฐานของ %เปลี่ยนแปลง + ธง stale + การรายงาน backend ล่ม
# --------------------------------------------------------------------------- #
class TestDailyCheckMessage:
    @pytest.fixture(autouse=True)
    def _stub_alert_line(self, monkeypatch):
        """ตัดการอ่านคลัง alert จริงของผู้ใช้ออกจากเทสต์"""
        monkeypatch.setattr(dc, "_alert_status_line", lambda: "⚠️ Price Alerts: (stub)")

    def test_ดึง_yfinance_ไม่ได้ต้องไม่กลายเป็น_บวก_0_00(self, monkeypatch):
        """FIX_PLAN 2.3: ``except → return 0.0`` ทำให้ Discord โชว์ ``+0.00% 🟢``

        ทั้งที่ดึงไม่ได้ — พี่น้องของมันใน ``price_alert.py`` แก้ไปแล้ว job นี้ตกหล่น
        """
        monkeypatch.setitem(sys.modules, "yfinance", _fake_yfinance({}, fail={"VOO"}))
        message = dc.build_discord_message({}, {})
        assert "+0.00%" not in message, (
            f"ข้อความมี '+0.00%' ทั้งที่ดึงข้อมูลไม่ได้:\n{message}"
        )
        assert "🟢" not in message, "ดึงข้อมูลไม่ได้ต้องไม่ได้ไฟเขียว"
        assert "ดึงราคาไม่ได้" in message

    def test_ใช้แท่งปิดสองแท่งเป็นฐานของเปอร์เซ็นต์(self, monkeypatch):
        """H12 ท่อนที่สอง: ต้องเป็นฐานเดียวกับ ``/api/etf/daily-snapshot``"""
        monkeypatch.setitem(
            sys.modules,
            "yfinance",
            _fake_yfinance({"VOO": [100.0, 110.0]}),
        )
        message = dc.build_discord_message({}, {})
        voo = [l for l in message.splitlines() if l.startswith("VOO")]
        assert voo, message
        assert "+10.00%" in voo[0], (
            f"คิด %เปลี่ยนแปลงจากแท่งปิด 2 แท่ง (100 → 110) ไม่ได้: {voo[0]!r}"
        )

    def test_เคารพธง_stale_ของ_snapshot(self, monkeypatch):
        """ราคาของ 27/07 ห้ามถูกพิมพ์เป็น "วันนี้ +0.00% 🟢"

        (สืบเนื่องจาก H10 — ``get_etf_daily_eod_snapshot`` ติดธงให้แล้ว)
        """
        monkeypatch.setitem(sys.modules, "yfinance", _fake_yfinance({}, fail=set(dc.TICKERS)))
        snapshot = {
            "GLDM": {
                "price": 104.05,
                "previous_close": 104.05,
                "change_pct": 0.0,
                "date": "27/07/2026",
                "stale": True,
                "data_ok": False,
                "reason": "แท่งราคาล่าสุดของ GLDM คือ 27/07/2026 ตามหลังวันล่าสุดของชุดข้อมูล (30/07/2026)",
            }
        }
        message = dc.build_discord_message(snapshot, {})
        gldm = [l for l in message.splitlines() if l.startswith("GLDM")]
        assert gldm, message
        assert "+0.00%" not in gldm[0], f"ข้อมูลค้างถูกพิมพ์เป็น 'ราคาไม่เปลี่ยน': {gldm[0]!r}"
        assert "⚠️" in gldm[0], f"ข้อมูลค้างต้องมีสัญลักษณ์เตือน: {gldm[0]!r}"

    def test_ไม่ยืมวันที่ของ_ticker_อื่นมาเป็นของทุกแถว(self, monkeypatch):
        """เดิมพาดหัวและทุกแถวใช้ ``date`` ของ ticker ตัวแรกที่เจอ"""
        monkeypatch.setitem(
            sys.modules,
            "yfinance",
            _fake_yfinance({}, fail=set(dc.TICKERS)),
        )
        snapshot = {
            "VOO": {
                "price": 560.0,
                "previous_close": 559.0,
                "change_pct": 0.1789,
                "date": "05/08/2026",
                "stale": False,
                "data_ok": True,
            },
            "GLDM": {
                "price": 104.05,
                "previous_close": 104.0,
                "change_pct": 0.05,
                "date": "27/07/2026",
                "stale": True,
                "data_ok": False,
                "reason": "แท่งราคาล่าสุดของ GLDM คือ 27/07/2026",
            },
        }
        message = dc.build_discord_message(snapshot, {})
        voo = [l for l in message.splitlines() if l.startswith("VOO")][0]
        assert "27/07/2026" not in voo, f"VOO ยืมวันที่ของ GLDM มาใช้: {voo!r}"
        assert "05/08/2026" in voo

    def test_ticker_ที่_backend_บอกว่า_error_ต้องไม่เงียบ(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "yfinance", _fake_yfinance({}, fail=set(dc.TICKERS)))
        snapshot = {
            "XLV": {
                "price": None,
                "previous_close": None,
                "change_pct": None,
                "date": None,
                "stale": True,
                "data_ok": False,
                "error": "ดึงราคาไม่สำเร็จ — ไม่มีแท่งราคาของ ticker นี้เลย",
            }
        }
        message = dc.build_discord_message(snapshot, {})
        xlv = [l for l in message.splitlines() if l.startswith("XLV")][0]
        assert "N/A" in xlv or "⚠️" in xlv
        assert "0.00" not in xlv

    def test_บอกผู้ใช้เมื่อดึงจาก_backend_ไม่ได้(self, monkeypatch):
        """M-CI-1: เดิมความล้มเหลวไปโผล่แค่ ``print()`` ใน log ของ GitHub Actions

        คนที่อ่าน Discord ไม่มีทางรู้ว่าตัวเลขที่เห็นมาจากเส้นทางสำรอง
        """
        monkeypatch.setitem(sys.modules, "yfinance", _fake_yfinance({"VOO": [100.0, 101.0]}))
        message = dc.build_discord_message(
            {}, {}, backend_errors=["daily-snapshot: Invalid URL: No scheme supplied."]
        )
        assert "backend" in message.lower(), message
        assert "yfinance" in message.lower(), message
        assert "⚠️" in message


class TestBackendFetchReportsErrors:
    def test_fetch_คืนข้อความผิดพลาดออกมาด้วย(self, monkeypatch):
        """ผู้เรียกต้องแยก "backend ตอบว่าไม่มีข้อมูล" ออกจาก "ยิงไม่ถึง backend" ได้"""

        def _boom(*args, **kwargs):
            raise RuntimeError("Invalid URL: No scheme supplied.")

        monkeypatch.setattr(dc.requests, "get", _boom)
        data, error = dc.fetch_daily_snapshot_from_backend()
        assert data == {}
        assert error and "Invalid URL" in error

        data, error = dc.fetch_prices_from_backend()
        assert data == {}
        assert error and "Invalid URL" in error


# --------------------------------------------------------------------------- #
# M-CI-2 — Weekly Summary
# --------------------------------------------------------------------------- #
def _frame_with_stale_gldm(n: int = 300, stale_bars: int = 15) -> pd.DataFrame:
    """VOO ขึ้น · SCHD/QQQM/XLV ลง · GLDM ไม่มีแท่งท้าย ``stale_bars`` แท่ง"""
    idx = pd.bdate_range("2025-01-01", periods=n)
    frame = pd.DataFrame(
        {
            "VOO": [100.0 + i * 0.10 for i in range(n)],
            "SCHD": [180.0 - i * 0.10 for i in range(n)],
            "QQQM": [190.0 - i * 0.12 for i in range(n)],
            "XLV": [200.0 - i * 0.15 for i in range(n)],
            "GLDM": [50.0 + i * 0.03 for i in range(n)],
        },
        index=idx,
    )
    if stale_bars:  # ``index[-0:]`` = ทั้งคอลัมน์ — ต้องกันไว้
        frame.loc[frame.index[-stale_bars:], "GLDM"] = float("nan")
    return frame


class TestWeeklySummary:
    @pytest.fixture
    def captured(self, monkeypatch):
        box: dict[str, object] = {}

        def _send(webhook_url, title, description, is_positive=True, embed_color=None):
            box["title"] = title
            box["description"] = description
            box["is_positive"] = is_positive
            return {"success": True}

        monkeypatch.setattr(scheduler_main, "send_discord_webhook", _send)
        monkeypatch.setattr(scheduler_main, "send_line_message", lambda *a, **k: {"skipped": True})
        monkeypatch.setattr(
            scheduler_main, "DEFAULT_TICKERS", ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
        )
        return box

    def test_ไม่กุ_1w_0_00_จาก_ffill(self, monkeypatch, captured):
        """M-CI-2: ``prices.ffill().iloc[-1] / prices.ffill().iloc[-6]`` = 1.0 เป๊ะ

        เมื่อแท่งท้ายของ ticker นั้นหายไป → ``1W +0.00%`` ที่หน้าตาเป็นแถวปกติทุกไบต์
        """
        frame = _frame_with_stale_gldm()
        monkeypatch.setattr(scheduler_main, "fetch_adjusted_close_data", lambda *a, **k: frame)
        scheduler_main.generate_weekly_report_and_notify("https://discord.example/webhook/AAA")

        description = str(captured["description"])
        gldm = [l for l in description.splitlines() if l.startswith("GLDM")]
        assert gldm, description
        assert "1W +0.00%" not in gldm[0], f"1W ถูกกุจาก ffill: {gldm[0]!r}"
        assert "⚠️" in gldm[0], f"ticker ที่ข้อมูลค้างต้องมีสัญลักษณ์เตือน: {gldm[0]!r}"

    def test_ticker_ที่ข้อมูลค้างไม่ถูกนับเป็นบวก(self, monkeypatch, captured):
        """0.00% ที่กุขึ้นถูกนับเข้า ``positive_count`` ซึ่งกำหนดสีของ embed ทั้งใบ"""
        frame = _frame_with_stale_gldm()
        monkeypatch.setattr(scheduler_main, "fetch_adjusted_close_data", lambda *a, **k: frame)
        scheduler_main.generate_weekly_report_and_notify("https://discord.example/webhook/AAA")
        assert captured["is_positive"] is False, (
            "VOO ขึ้นตัวเดียว SCHD/QQQM/XLV ลง GLDM ข้อมูลค้าง — embed ต้องไม่เป็นสีบวก "
            "(เดิม 0.00% ที่กุขึ้นถูกนับเป็นบวกจนพลิกสีทั้งใบ)"
        )

    def test_ข้อมูลครบยังรายงานตามปกติ(self, monkeypatch, captured):
        frame = _frame_with_stale_gldm(stale_bars=0)
        monkeypatch.setattr(scheduler_main, "fetch_adjusted_close_data", lambda *a, **k: frame)
        scheduler_main.generate_weekly_report_and_notify("https://discord.example/webhook/AAA")
        description = str(captured["description"])
        gldm = [l for l in description.splitlines() if l.startswith("GLDM")][0]
        assert "⚠️" not in gldm
        assert "RSI" in gldm and "1W" in gldm


# --------------------------------------------------------------------------- #
# M-CI-3 — Daily technical alerts
# --------------------------------------------------------------------------- #
class TestDailyTechnicalAlerts:
    @pytest.fixture
    def sent(self, monkeypatch):
        alerts: list[dict] = []
        notices: list[str] = []

        def _tech(webhook_url, symbol, rsi, price, ma200, previous_price):
            alerts.append(
                {
                    "symbol": symbol,
                    "rsi": rsi,
                    "price": price,
                    "ma200": ma200,
                    "previous_price": previous_price,
                }
            )
            return {"success": True}

        def _send(webhook_url, title, description, is_positive=True, embed_color=None):
            notices.append(f"{title}\n{description}")
            return {"success": True}

        monkeypatch.setattr(scheduler_main, "send_technical_alert", _tech)
        monkeypatch.setattr(scheduler_main, "send_discord_webhook", _send)
        return {"alerts": alerts, "notices": notices}

    @staticmethod
    def _frame(stale_bars: int) -> pd.DataFrame:
        n = 400
        idx = pd.bdate_range("2024-01-01", periods=n)
        frame = pd.DataFrame(
            {
                "VOO": [100.0 + i * 0.10 for i in range(n)],
                "GLDM": [200.0 - i * 0.40 for i in range(n)],
            },
            index=idx,
        )
        if stale_bars:
            frame.loc[frame.index[-stale_bars:], "GLDM"] = float("nan")
        return frame

    def test_ราคาเก่าห้ามถูกส่งเป็นสัญญาณของวันนี้(self, monkeypatch, sent):
        """M-CI-3: ``previous_price`` เท่ากับ ``price`` เป๊ะ — ตัวตัดสินทิศทางตาบอดทันที"""
        monkeypatch.setattr(scheduler_main, "DEFAULT_TICKERS", ["VOO", "GLDM"])
        monkeypatch.setattr(
            scheduler_main, "fetch_adjusted_close_data", lambda *a, **k: self._frame(15)
        )
        scheduler_main.generate_daily_technical_alerts("https://discord.example/webhook/AAA")

        gldm = [a for a in sent["alerts"] if a["symbol"] == "GLDM"]
        assert not gldm, (
            f"ส่งสัญญาณจากราคาเก่า 3 สัปดาห์เป็นราคาวันนี้: {gldm}"
        )

    def test_ticker_ที่ข้อมูลค้างต้องถูกรายงาน_ไม่ใช่ตัดทิ้งเงียบ(self, monkeypatch, sent):
        monkeypatch.setattr(scheduler_main, "DEFAULT_TICKERS", ["VOO", "GLDM"])
        monkeypatch.setattr(
            scheduler_main, "fetch_adjusted_close_data", lambda *a, **k: self._frame(15)
        )
        scheduler_main.generate_daily_technical_alerts("https://discord.example/webhook/AAA")
        assert any("GLDM" in n for n in sent["notices"]), (
            "ตัด GLDM ออกจากการตรวจแล้วเงียบ — 'ตรวจไม่ได้' ต้องถูกรายงานออกไป"
        )

    def test_ข้อมูลครบยังแจ้งเตือนตามปกติ(self, monkeypatch, sent):
        monkeypatch.setattr(scheduler_main, "DEFAULT_TICKERS", ["VOO", "GLDM"])
        monkeypatch.setattr(
            scheduler_main, "fetch_adjusted_close_data", lambda *a, **k: self._frame(0)
        )
        scheduler_main.generate_daily_technical_alerts("https://discord.example/webhook/AAA")
        symbols = {a["symbol"] for a in sent["alerts"]}
        assert symbols == {"VOO", "GLDM"}
        for alert in sent["alerts"]:
            assert alert["previous_price"] != alert["price"]


# --------------------------------------------------------------------------- #
# M-CI-4 — scheduler ต้องไม่ตายเพราะ job เดียวพัง
# --------------------------------------------------------------------------- #
_BASE_CONFIG = {
    "dca": {"day_of_month": 1, "monthly_budget_thb": 5000.0},
    "notifications": {
        "discord_webhook_url": "",
        "weekly_summary": True,
        "dca_reminder": True,
        "rsi_alert": True,
    },
}


class TestSchedulerResilience:
    @pytest.fixture(autouse=True)
    def _clean(self):
        import schedule as schedule_lib

        schedule_lib.clear()
        yield
        schedule_lib.clear()

    def test_ลูปรอดเมื่อ_run_pending_โยน_exception(self, monkeypatch):
        """M-CI-4: ``try/except`` ครอบ ``while True`` ทั้งก้อน อยู่**นอก**ลูป

        → job เดียวพัง = ไม่มีใครเรียก ``run_pending`` อีกเลย งานที่เหลือรอไปตลอดกาล
        """
        state = {"pending": 0, "sleep": 0}

        def _boom():
            state["pending"] += 1
            raise RuntimeError("job พัง")

        def _sleep(_seconds):
            state["sleep"] += 1
            if state["sleep"] >= 3:
                raise KeyboardInterrupt

        monkeypatch.setattr(scheduler_main, "load_config", lambda: dict(_BASE_CONFIG))
        monkeypatch.setattr(scheduler_main.schedule, "run_pending", _boom)
        monkeypatch.setattr(scheduler_main.time, "sleep", _sleep)
        scheduler_main.run_scheduler()

        assert state["pending"] >= 3, (
            f"run_pending ถูกเรียกแค่ {state['pending']} ครั้ง — scheduler ตายหลัง job แรกพัง"
        )

    def test_job_ที่ลงทะเบียนไม่ปล่อย_exception_ออกมา(self, monkeypatch):
        """งานที่พังต้องถูกกันไว้ที่ตัวมันเอง ไม่ลามไปหยุดงานอื่น"""
        import schedule as schedule_lib

        def check_alerts():  # ชื่อเดิม — ``_names()`` ใน test_scheduler_startup อ่านจาก __name__
            raise RuntimeError("อ่านคลัง alert ไม่ได้")

        monkeypatch.setattr(scheduler_main, "check_alerts", check_alerts)
        monkeypatch.setattr(scheduler_main, "load_config", lambda: dict(_BASE_CONFIG))

        def _stop(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(scheduler_main.time, "sleep", _stop)
        scheduler_main.run_scheduler()

        jobs = list(schedule_lib.jobs)
        assert jobs, "ไม่มีงานถูกลงทะเบียนเลย"
        for job in jobs:
            job.job_func()  # ต้องไม่โยนออกมา

    def test_ชื่อของงานยังคงเดิม(self, monkeypatch):
        """ห่อด้วย wrapper แล้วต้องยัง introspect ชื่อได้ (functools.wraps)"""
        import schedule as schedule_lib

        monkeypatch.setattr(scheduler_main, "load_config", lambda: dict(_BASE_CONFIG))

        def _stop(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(scheduler_main.time, "sleep", _stop)
        scheduler_main.run_scheduler()

        names = {
            getattr(getattr(j.job_func, "func", j.job_func), "__name__", "")
            for j in schedule_lib.jobs
        }
        assert names == {"run_price_alert_job"}, names
