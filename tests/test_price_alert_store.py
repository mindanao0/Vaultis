# -*- coding: utf-8 -*-
"""คลัง price alert ต้องไม่แปลง "อ่านไม่ได้" เป็น "ไม่มี alert" (AUDIT_2026-08-06 ข้อ A1/H2 + D1).

อาการที่วัดได้ก่อนแก้ (รันบนโค้ด HEAD ในคอนเทนเนอร์เทสต์):

    [A1.1] _load_alerts() บนไฟล์เสีย = []
    [A1.2] check_alerts() = {'success': True, 'checked': 0, 'triggered': []}
           ไฟล์หลังรัน = {"alerts": []}          ← alert ของผู้ใช้หายถาวร
    [A1.3] มี .bak ไหม: False
    [D1.1] checked = 2 | triggered = 0 | มีคีย์ unchecked: False
           daily_summary เอ่ยถึง AAPL ไหม: False
    [D1.2] แท่งเดียว → 'VOO   $500.00  🟡 (+0.00%)'   ← "ราคาไม่เปลี่ยน" จากข้อมูลที่ไม่มี

ความปลอดภัยของไฟล์นี้ (ห้ามแตะข้อมูลจริงของผู้ใช้):
- ทุกเคสชี้ ``pa.ALERTS_PATH`` ไป ``tmp_path``
- stub ``load_config`` ให้ webhook ว่าง **และ** stub ``send_discord_webhook``
  (คอนเทนเนอร์โหลด ``.env`` จริงที่มี ``DISCORD_WEBHOOK_URL`` — ห้ามยิงจริง)
- stub ``get_price_snapshots`` — ไม่ยิง yfinance
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

import alerts.price_alert as pa

REAL_STORE = _ROOT / "alerts" / "data" / "price_alerts.json"


def _alert(ticker: str, alert_type: str = "below", price: float = 100.0, alert_id: str = "a1") -> dict:
    return {
        "id": alert_id,
        "ticker": ticker,
        "alert_type": alert_type,
        "price": price,
        "note": "",
        "triggered": False,
        "created_at": "2026-08-01T00:00:00",
        "triggered_at": None,
        "triggered_price": None,
    }


def _write(path: Path, alerts: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"alerts": alerts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """คลังชั่วคราว + ไม่มี webhook + ไม่มีเน็ต."""
    path = tmp_path / "price_alerts.json"
    monkeypatch.setattr(pa, "ALERTS_PATH", path)
    assert path.resolve() != REAL_STORE.resolve()

    sent: list[dict] = []
    monkeypatch.setattr(pa, "send_discord_webhook", lambda **kw: (sent.append(kw), {"success": True})[1])
    monkeypatch.setattr(pa, "load_config", lambda: {"notifications": {"discord_webhook_url": ""}})
    monkeypatch.setattr(pa, "get_price_snapshots", lambda tickers: {})
    return {"path": path, "sent": sent}


def _set_prices(monkeypatch, prices: dict[str, float]) -> None:
    snapshots = {
        ticker: {"latest_price": float(p), "previous_close": float(p)} for ticker, p in prices.items()
    }
    monkeypatch.setattr(pa, "get_price_snapshots", lambda tickers: snapshots)


def _corrupt(path: Path) -> str:
    """จำลองการเขียนที่ถูกขัดจังหวะ: ตัดไฟล์ครึ่งเดียว. คืนเนื้อไฟล์ที่เสียไว้เทียบทีหลัง."""
    full = json.dumps({"alerts": [_alert("VOO"), _alert("QQQM", alert_id="a2")]}, ensure_ascii=False, indent=2)
    broken = full[: len(full) // 2]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(broken, encoding="utf-8")
    return broken


# ---------------------------------------------------------------- A1 ชั้นที่ 1


class TestLoadFailsLoud:
    """"อ่านไม่ได้" ≠ "ไม่มี alert" — คนละความหมาย ต้องแยกกันทุกชั้น."""

    def test_unreadable_file_raises_instead_of_empty_list(self, store):
        _corrupt(store["path"])
        with pytest.raises(pa.AlertStoreUnavailable):
            pa._load_alerts()

    def test_missing_file_is_a_real_empty_list(self, store):
        assert not store["path"].exists()
        assert pa._load_alerts() == []

    def test_wrong_shape_raises(self, store):
        store["path"].parent.mkdir(parents=True, exist_ok=True)
        store["path"].write_text(json.dumps({"alerts": {"VOO": 1}}), encoding="utf-8")
        with pytest.raises(pa.AlertStoreUnavailable):
            pa._load_alerts()

    def test_empty_file_raises(self, store):
        store["path"].parent.mkdir(parents=True, exist_ok=True)
        store["path"].write_text("", encoding="utf-8")
        with pytest.raises(pa.AlertStoreUnavailable):
            pa._load_alerts()

    def test_list_alerts_propagates(self, store):
        _corrupt(store["path"])
        with pytest.raises(pa.AlertStoreUnavailable):
            pa.list_alerts()


# ---------------------------------------------------------------- A1 ชั้นที่ 2


class TestNoWriteBackWhenLoadFails:
    """โหลดไม่สำเร็จ = หยุดทันที ห้ามเขียนทับคลังของผู้ใช้."""

    def test_check_alerts_does_not_overwrite_broken_store(self, store):
        broken = _corrupt(store["path"])

        result = pa.check_alerts()

        assert _read_raw(store["path"]) == broken, "ไฟล์ที่อ่านไม่ได้ต้องคงเดิม ห้ามถูกทับด้วยลิสต์ว่าง"
        assert result["success"] is False
        assert result.get("store_error") is True
        assert result["checked"] == 0
        assert result["triggered"] == []

    def test_check_alerts_reports_store_failure_to_discord(self, store, monkeypatch):
        monkeypatch.setattr(
            pa, "load_config", lambda: {"notifications": {"discord_webhook_url": "https://example.invalid/hook"}}
        )
        _corrupt(store["path"])

        pa.check_alerts()

        assert len(store["sent"]) == 1, "ต้องแจ้งผู้ใช้ว่าอ่านคลัง alert ไม่ได้ ห้ามเงียบ"
        assert "alert" in store["sent"][0]["description"].lower()

    def test_add_alert_does_not_overwrite_broken_store(self, store):
        broken = _corrupt(store["path"])
        with pytest.raises(pa.AlertStoreUnavailable):
            pa.add_alert("VOO", "below", 400.0)
        assert _read_raw(store["path"]) == broken

    def test_add_or_update_alert_does_not_overwrite_broken_store(self, store):
        broken = _corrupt(store["path"])
        with pytest.raises(pa.AlertStoreUnavailable):
            pa.add_or_update_alert("VOO", "below", 400.0)
        assert _read_raw(store["path"]) == broken

    def test_delete_alert_does_not_overwrite_broken_store(self, store):
        broken = _corrupt(store["path"])
        with pytest.raises(pa.AlertStoreUnavailable):
            pa.delete_alert("a1")
        assert _read_raw(store["path"]) == broken


# ---------------------------------------------------------------- A1 ชั้นที่ 3


class TestAtomicSave:
    """เขียนแบบ .tmp + os.replace + สำรอง .bak — กันไฟล์เสียตั้งแต่แรก."""

    def test_previous_content_is_backed_up(self, store):
        _write(store["path"], [_alert("VOO", alert_id="old")])
        before = _read_raw(store["path"])

        pa.add_alert("QQQM", "below", 150.0)

        bak = store["path"].with_name(store["path"].name + ".bak")
        assert bak.exists(), "ต้องสำรองไฟล์เดิมก่อนทับ"
        assert _read_raw(bak) == before

    def test_no_temp_file_left_behind(self, store):
        _write(store["path"], [])
        pa.add_alert("VOO", "below", 400.0)
        leftovers = [p.name for p in store["path"].parent.glob("*.tmp*")]
        assert leftovers == []

    def test_failed_swap_leaves_original_intact(self, store, monkeypatch):
        _write(store["path"], [_alert("VOO", alert_id="keep")])
        before = _read_raw(store["path"])

        def _boom(src, dst):
            raise OSError("ดิสก์เต็ม")

        monkeypatch.setattr(pa.os, "replace", _boom)
        with pytest.raises(OSError):
            pa.add_alert("QQQM", "below", 150.0)

        assert _read_raw(store["path"]) == before, "การเขียนที่ล้มต้องไม่แตะไฟล์เดิม"
        assert [p.name for p in store["path"].parent.glob("*.tmp*")] == []


class TestNoLostUpdate:
    """alert ที่ผู้ใช้เพิ่มระหว่าง check_alerts ยิง yfinance อยู่ ต้องไม่หาย."""

    def test_alert_added_during_price_fetch_survives(self, store, monkeypatch):
        _write(store["path"], [_alert("VOO", "above", price=100.0, alert_id="voo")])

        def _slow_fetch(tickers):
            # จำลองแดชบอร์ดเพิ่ม alert ระหว่างหน้าต่างที่ check_alerts รอราคา (วัดได้ 0.72 วิ)
            pa.add_alert("SCHD", "below", 70.0, note="เพิ่มระหว่างตรวจ")
            return {"VOO": {"latest_price": 101.0, "previous_close": 100.0}}

        monkeypatch.setattr(pa, "get_price_snapshots", _slow_fetch)

        pa.check_alerts()

        tickers = {row["ticker"] for row in pa._load_alerts()}
        assert tickers == {"VOO", "SCHD"}, "alert ที่เพิ่มระหว่างตรวจถูกเขียนทับหาย (lost update)"

    def test_alert_added_mid_round_is_not_reported_as_a_price_failure(self, store, monkeypatch):
        """เหตุผลใน ``unchecked`` ต้องตรงกับความจริง — ไม่เคยขอราคา ≠ ขอแล้วดึงไม่ได้.

        ใช้ ``AAPL`` เพราะอยู่นอก ``DAILY_CHECK_TICKERS`` — ticker ในลิสต์นั้นถูกขอราคา
        ทุกรอบอยู่แล้ว "ดึงราคาไม่ได้" จึงเป็นเหตุผลที่ถูกต้องสำหรับมัน
        """
        assert "AAPL" not in pa.DAILY_CHECK_TICKERS
        _write(store["path"], [_alert("VOO", "above", price=100.0, alert_id="voo")])

        def _slow_fetch(tickers):
            assert "AAPL" not in tickers, "รอบนี้ยังไม่รู้จัก AAPL — ต้องไม่ถูกขอราคา"
            pa.add_alert("AAPL", "below", 70.0, note="เพิ่มระหว่างตรวจ")
            return {"VOO": {"latest_price": 99.0, "previous_close": 100.0}}

        monkeypatch.setattr(pa, "get_price_snapshots", _slow_fetch)

        result = pa.check_alerts()

        rows = {row["ticker"]: row["reason"] for row in result["unchecked"]}
        assert list(rows) == ["AAPL"]
        assert "ดึงราคาไม่ได้" not in rows["AAPL"], "ไม่เคยขอราคาให้ ticker นี้เลย — ห้ามรายงานว่าดึงไม่สำเร็จ"

    def test_requested_ticker_without_price_still_says_fetch_failed(self, store, monkeypatch):
        """ตัวคุม: ticker ที่ **ขอแล้ว** แต่ไม่ได้ราคา ต้องยังรายงานว่าดึงราคาไม่ได้."""
        _write(store["path"], [_alert("VOO", "above", price=1.0, alert_id="voo")])
        _set_prices(monkeypatch, {})

        result = pa.check_alerts()

        assert result["unchecked"][0]["reason"] == "ดึงราคาไม่ได้"


# ---------------------------------------------------------------- D1


class TestUncheckedReporting:
    """D1.1 — "ดึงราคาไม่ได้" ต้องไม่ถูกนับว่า "ตรวจแล้ว"."""

    def test_checked_counts_only_alerts_with_a_real_price(self, store, monkeypatch):
        _write(
            store["path"],
            [_alert("VOO", "above", alert_id="voo"), _alert("AAPL", "above", price=1.0, alert_id="aapl")],
        )
        _set_prices(monkeypatch, {"VOO": 99.0})

        result = pa.check_alerts()

        assert result["checked"] == 1
        assert [row["ticker"] for row in result["unchecked"]] == ["AAPL"]
        assert result["unchecked"][0]["reason"]

    def test_all_prices_missing_reports_every_pending_alert(self, store, monkeypatch):
        _write(
            store["path"],
            [_alert("VOO", "above", price=1.0, alert_id="voo"), _alert("SCHD", "above", price=1.0, alert_id="schd")],
        )
        _set_prices(monkeypatch, {})

        result = pa.check_alerts()

        assert result["checked"] == 0
        assert sorted(row["ticker"] for row in result["unchecked"]) == ["SCHD", "VOO"]

    def test_daily_summary_covers_tickers_outside_the_default_five(self, store, monkeypatch):
        _write(
            store["path"],
            [_alert("AAPL", "above", price=1.0, alert_id="a"), _alert("NVDA", "above", price=1.0, alert_id="n")],
        )
        _set_prices(monkeypatch, {"AAPL": 200.0, "NVDA": 900.0})

        result = pa.check_alerts()

        assert "AAPL" in result["daily_summary"]
        assert "NVDA" in result["daily_summary"]


class TestSingleBarChange:
    """D1.2 — มีแท่งปิดแท่งเดียว ≠ "ราคาไม่เปลี่ยน 0.00%"."""

    def test_previous_close_is_none_when_only_one_bar(self, monkeypatch):
        import pandas as pd

        frame = pd.DataFrame(
            {("VOO", "Close"): [500.0]},
            index=pd.to_datetime(["2026-08-06"]),
        )
        frame.columns = pd.MultiIndex.from_tuples(frame.columns)
        monkeypatch.setattr(pa.yf, "download", lambda **kwargs: frame)

        snapshots = pa.get_price_snapshots(["VOO"])

        assert snapshots["VOO"]["latest_price"] == pytest.approx(500.0)
        assert snapshots["VOO"]["previous_close"] is None, "แท่งเดียว = ไม่รู้ราคาก่อนหน้า ห้ามยัดราคาตัวเอง"

    def test_message_says_change_unavailable_not_zero_percent(self):
        msg = pa._build_daily_status_message(
            tracked_tickers=["VOO"],
            snapshots={"VOO": {"latest_price": 500.0, "previous_close": None}},
            triggered_items=[],
            unchecked=[],
        )
        line = [ln for ln in msg.splitlines() if ln.startswith("VOO")][0]
        assert "0.00%" not in line
        assert "$500.00" in line
        assert "⚠️" in line


class TestStoreStatus:
    """D1.3 — "ไม่เห็นคลัง" ≠ "ไม่มี alert ค้าง" (GitHub Actions ไม่มีไฟล์นี้เลย)."""

    def test_status_missing_when_file_absent(self, store):
        assert not store["path"].exists()
        assert pa.get_store_status()["status"] == "missing"

    def test_reading_never_creates_the_file(self, store):
        pa.list_alerts()
        assert not store["path"].exists(), "การอ่านต้องไม่สร้างคลังเปล่าให้เอง (จะกลบความต่างของ 'ไม่มีคลัง')"

    def test_status_ok_counts_pending_and_triggered(self, store):
        done = _alert("VOO", alert_id="done")
        done["triggered"] = True
        _write(store["path"], [_alert("SCHD", alert_id="wait"), done])
        status = pa.get_store_status()
        assert status["status"] == "ok"
        assert status["pending"] == 1
        assert status["triggered"] == 1

    def test_status_error_when_unreadable(self, store):
        _corrupt(store["path"])
        status = pa.get_store_status()
        assert status["status"] == "error"
        assert status["pending"] is None

    def test_daily_check_line_distinguishes_missing_store(self, store):
        from jobs import daily_check

        line = daily_check._alert_status_line()
        assert "0 รายการ" not in line, "ไม่มีคลัง ≠ รอ trigger 0 รายการ"
        assert "alert" in line.lower()

    def test_daily_check_line_reports_real_counts(self, store):
        from jobs import daily_check

        _write(store["path"], [_alert("SCHD", alert_id="wait")])
        assert "1 รายการ" in daily_check._alert_status_line()


# ------------------------------------------------- แถว alert ที่ "มีอยู่แต่ใช้ไม่ได้"


class TestUnusableRowsAreReportedNotSwallowed:
    """ค่าที่มีอยู่แต่ใช้ไม่ได้ (ไม่มีคีย์ราคา / NaN / สตริง / ชนิดเงื่อนไขมั่ว) ต้องไม่ผ่านด่าน.

    วัดได้บนโค้ดหลังแก้ A1/D1 (probe รอบหักล้าง 2026-08-07):

        [P2] แถวไม่มีคีย์ ``price`` → ``float(alert.get("price", 0.0))`` = 0.0
             → alert ชนิด above trigger ทันทีที่ราคา 555 แล้วปิดตัวเองถาวร
             checked=1 triggered=[{'target_price': 0.0, 'current_price': 555.0}]
        [P3] ``price`` เป็นสตริงไทย → ValueError หลุดออกจาก check_alerts ทั้งรอบ
             (alert SCHD ที่ควร trigger ไม่ถูกตรวจ และไม่มีสรุป Discord ออกเลย)
        [P5] ``alert_type`` = 'ABOVE!' → checked=1 triggered=0 unchecked=[]
             = "ตรวจแล้วยังไม่ถึงเงื่อนไข" ทั้งที่ไม่มีวันถึง
        [P1] ``price`` = NaN → checked=1 unchecked=[] เช่นกัน
    """

    def test_alert_without_price_is_not_treated_as_target_zero(self, store, monkeypatch):
        _write(store["path"], [{"id": "p1", "ticker": "VOO", "alert_type": "above", "triggered": False}])
        _set_prices(monkeypatch, {"VOO": 555.0})

        result = pa.check_alerts()

        assert result["triggered"] == [], "ไม่มีราคาเป้าหมาย = ตรวจไม่ได้ ห้ามกลายเป็นเป้า 0.0 แล้ว trigger"
        assert result["checked"] == 0
        assert [row["ticker"] for row in result["unchecked"]] == ["VOO"]
        stored = json.loads(_read_raw(store["path"]))["alerts"][0]
        assert stored.get("triggered") is False, "แถวที่ตรวจไม่ได้ต้องไม่ถูกปิดเป็น triggered"

    def test_nan_price_is_reported_not_counted_as_checked(self, store, monkeypatch):
        _write(store["path"], [_alert("VOO", "below", price=float("nan"), alert_id="n1")])
        _set_prices(monkeypatch, {"VOO": 400.0})

        result = pa.check_alerts()

        assert result["checked"] == 0
        assert [row["ticker"] for row in result["unchecked"]] == ["VOO"]

    def test_unparsable_price_does_not_kill_the_whole_round(self, store, monkeypatch):
        _write(
            store["path"],
            [
                {"id": "s1", "ticker": "VOO", "alert_type": "above", "price": "ห้าร้อย", "triggered": False},
                _alert("SCHD", "below", price=10.0, alert_id="s2"),
            ],
        )
        _set_prices(monkeypatch, {"VOO": 555.0, "SCHD": 5.0})

        result = pa.check_alerts()

        assert [row["ticker"] for row in result["triggered"]] == ["SCHD"], (
            "แถวเสีย 1 แถวต้องไม่ทำให้ทั้งรอบตาย — alert อื่นยังต้องถูกตรวจ"
        )
        assert [row["ticker"] for row in result["unchecked"]] == ["VOO"]

    def test_unknown_alert_type_is_not_counted_as_checked(self, store, monkeypatch):
        _write(store["path"], [_alert("VOO", "ABOVE!", price=1.0, alert_id="t1")])
        _set_prices(monkeypatch, {"VOO": 555.0})

        result = pa.check_alerts()

        assert result["checked"] == 0, "เงื่อนไขที่ไม่มีวันเป็นจริง ไม่ใช่ 'ตรวจแล้ว'"
        assert [row["ticker"] for row in result["unchecked"]] == ["VOO"]

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_add_alert_rejects_non_finite_price(self, store, bad):
        """``if price <= 0`` ดัก NaN ไม่ได้ (NaN เทียบอะไรก็ False) และ inf ก็ผ่านด่าน.

        ผลจริงก่อนแก้: ``POST /api/alerts`` ด้วย ``Infinity`` ตอบ 400 (render ผลลัพธ์ไม่ได้)
        **แต่ alert ถูกเขียนลงคลังไปแล้ว** และหลังจากนั้น ``GET /api/alerts`` ตอบ 500 ทุกครั้ง
        เพราะ JSON มาตรฐานไม่มี NaN/Infinity — คลังทั้งก้อนอ่านผ่าน API ไม่ได้อีกเลย
        """
        with pytest.raises(ValueError):
            pa.add_alert("VOO", "above", bad)
        assert not store["path"].exists(), "คำขอที่ถูกปฏิเสธต้องไม่ถูกเขียนลงคลัง"


class TestPartiallyBrokenStore:
    """แถวที่ไม่ใช่ระเบียน alert ต้องไม่ถูก "ตัดทิ้งเงียบ" แล้วโดนลบถาวรในการบันทึกครั้งถัดไป.

    รู้ที่การแก้ A1 สร้างขึ้นเอง: HEAD คืนลิสต์ทั้งก้อน (แถวเสียทำให้ทั้งรอบพังแบบดัง ๆ)
    แต่ตัวแก้ใส่ ``[item for item in payload["alerts"] if isinstance(item, dict)]``
    ⇒ แถวเสียหายเงียบ แล้ว ``_save_alerts`` เขียนลิสต์ที่กรองแล้วทับไฟล์ = ลบถาวร
    (probe รอบหักล้าง: ไฟล์ 3 แถว → _load_alerts() คืน 1 แถว → หลัง check_alerts เหลือ 1 แถวในไฟล์)
    """

    def test_non_dict_row_raises_instead_of_being_dropped(self, store):
        _write_raw = json.dumps({"alerts": [_alert("VOO"), "แถวเสีย", 42]}, ensure_ascii=False)
        store["path"].parent.mkdir(parents=True, exist_ok=True)
        store["path"].write_text(_write_raw, encoding="utf-8")

        with pytest.raises(pa.AlertStoreUnavailable):
            pa._load_alerts()

    def test_broken_row_is_never_erased_from_the_file(self, store, monkeypatch):
        raw = json.dumps({"alerts": [_alert("VOO", "above", price=1.0), "แถวเสีย"]}, ensure_ascii=False)
        store["path"].parent.mkdir(parents=True, exist_ok=True)
        store["path"].write_text(raw, encoding="utf-8")
        _set_prices(monkeypatch, {"VOO": 555.0})

        result = pa.check_alerts()

        assert result["store_error"] is True
        assert _read_raw(store["path"]) == raw, "แถวที่ระบบอ่านไม่เข้าใจ ต้องยังอยู่ในไฟล์ครบ"


class TestWritesOnlyWhenSomethingChanged:
    """"เขียนคลังเฉพาะตอนที่มีอะไรเปลี่ยนจริง" — กติกาที่ซอร์สเขียนคอมเมนต์บังคับตัวเองไว้.

    ก่อนมีเทสต์นี้ การถอดเงื่อนไข ``if triggered_items:`` ที่ครอบ ``_save_alerts(alerts)``
    (= เขียนไฟล์ทับทุกครั้งที่ตรวจ) รอดชุดเทสต์ทั้ง 1296 ตัวโดยไม่มีตัวไหนแดง
    (AUDIT_ROUND2_2026-08-07 · มิวแทนต์ M14)

    ทำไมถึงสำคัญ: ``alerts/data/price_alerts.json`` เป็นแหล่งเดียวของ price alert ตาม
    CLAUDE.md และ scheduler ตรวจวันละ 2 รอบ — การเขียนทับทุกรอบคือการเปิดหน้าต่างให้
    ไฟล์เสียตอนถูกฆ่ากลางคัน โดยไม่ได้อะไรกลับมาเลย

    วัดด้วย ``st_mtime_ns`` + ``st_ino`` เพราะ ``_save_alerts`` ใช้ ``os.replace``
    (เปลี่ยน inode ทุกครั้ง) — การเทียบเนื้อไฟล์อย่างเดียวจับ "เขียนทับด้วยเนื้อเดิม" ไม่ได้
    """

    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, int, str]:
        info = path.stat()
        return (info.st_mtime_ns, info.st_ino, _read_raw(path))

    def test_no_trigger_leaves_the_file_untouched(self, store, monkeypatch):
        _write(store["path"], [_alert("VOO", "below", price=100.0, alert_id="v")])
        _set_prices(monkeypatch, {"VOO": 500.0})  # ห่างเป้าลิบ — ไม่มีทางติด
        before = self._fingerprint(store["path"])

        result = pa.check_alerts()

        assert result["checked"] == 1 and result["triggered"] == [], "ต้องเป็นรอบที่ตรวจแล้วไม่ติด"
        assert self._fingerprint(store["path"]) == before, "ไม่มีอะไรเปลี่ยน = ห้ามแตะไฟล์คลัง"
        assert not pa._backup_path().exists(), "ไม่มีการเขียน ก็ต้องไม่มี .bak เกิดขึ้น"

    def test_unchecked_alerts_do_not_trigger_a_write_either(self, store, monkeypatch):
        """ดึงราคาไม่ได้ = ไม่รู้ผล ยิ่งห้ามเขียนทับคลังใหญ่."""
        _write(store["path"], [_alert("VOO", "below", price=100.0, alert_id="v")])
        _set_prices(monkeypatch, {})
        before = self._fingerprint(store["path"])

        result = pa.check_alerts()

        assert [row["ticker"] for row in result["unchecked"]] == ["VOO"]
        assert self._fingerprint(store["path"]) == before

    def test_a_real_trigger_is_still_persisted(self, store, monkeypatch):
        """อีกด้านของตาข่าย: ห้ามแก้จนกลายเป็น "ไม่เขียนเลย" — alert ที่ติดแล้วต้องถูกบันทึก."""
        _write(store["path"], [_alert("VOO", "below", price=1000.0, alert_id="v")])
        _set_prices(monkeypatch, {"VOO": 500.0})
        before = self._fingerprint(store["path"])

        result = pa.check_alerts()

        assert [row["ticker"] for row in result["triggered"]] == ["VOO"]
        assert self._fingerprint(store["path"]) != before
        saved = json.loads(_read_raw(store["path"]))["alerts"]
        assert saved[0]["triggered"] is True


_REAL_STORE_FINGERPRINT = (
    (REAL_STORE.stat().st_mtime_ns, REAL_STORE.stat().st_size) if REAL_STORE.exists() else None
)


def _assert_store_untouched(path: Path, fingerprint: tuple[int, int] | None) -> None:
    """เทียบลายนิ้วมือคลังจริงกับตอน import — แยก "ถูกแก้" ออกจาก "หายไปทั้งไฟล์".

    เดิมโค้ดเรียก ``REAL_STORE.stat()`` ตรง ๆ ⇒ ถ้าไฟล์ **หาย** ระหว่างรัน จะได้
    ``FileNotFoundError`` เปล่า ๆ ซึ่งอ่านเหมือน "เทสต์พัง" ไม่ใช่ "ข้อมูลจริงของผู้ใช้
    หายไปแล้ว" — และเรื่องนี้เกิดขึ้นจริงระหว่างรอบตรวจ 2026-08-07 (ไฟล์หายไปจาก
    เครื่องระหว่างที่มีเอเจนต์หลายตัวรันบน working tree เดียวกัน)
    (AUDIT_ROUND2_2026-08-07 — แนวแก้ข้อ 2)

    ``skip`` เมื่อไม่มีไฟล์ **ตั้งแต่ตอน import** = เครื่องนี้ยังไม่เคยตั้ง alert
    ซึ่งเป็นคนละเรื่องกับ "มีตอนเริ่ม แล้วหายระหว่างทาง" ที่ต้องดังเป็น AssertionError
    """
    if fingerprint is None:
        pytest.skip("ไม่มีไฟล์คลัง alert จริงในเครื่องนี้ตั้งแต่ตอนเริ่มรัน")
    assert path.exists(), (
        f"คลัง alert จริงหายไประหว่างรันชุดเทสต์ ({path}) — ไฟล์นี้ถูก gitignore ไว้ "
        "จึงไม่มีสำเนาใน git ให้กู้ ต้องหาตัวที่ลบให้เจอก่อนรันต่อ"
    )
    now = (path.stat().st_mtime_ns, path.stat().st_size)
    assert now == fingerprint, f"คลัง alert จริงถูกเขียนทับระหว่างรันชุดเทสต์ ({path})"


class TestRealStoreUntouched:
    def test_module_path_points_at_tmp(self, store):
        assert pa.ALERTS_PATH.resolve() != REAL_STORE.resolve()

    def test_real_store_is_byte_identical_after_the_suite(self, store):
        """ตาข่ายกันเทสต์ชุดนี้เผลอเขียนสมุด alert จริงของผู้ใช้."""
        _assert_store_untouched(REAL_STORE, _REAL_STORE_FINGERPRINT)


class TestTheNetItselfFailsReadably:
    """ตาข่ายด้านบนต้องรายงานเป็นข้อความไทยที่อ่านรู้เรื่อง ไม่ใช่ ``FileNotFoundError`` เปล่า ๆ."""

    def test_file_deleted_mid_run_says_so_in_thai(self, tmp_path):
        decoy = tmp_path / "price_alerts.json"
        decoy.write_text('{"alerts": []}\n', encoding="utf-8")
        fingerprint = (decoy.stat().st_mtime_ns, decoy.stat().st_size)
        decoy.unlink()  # จำลอง "ไฟล์หายระหว่างรัน" แบบที่เกิดขึ้นจริง

        with pytest.raises(AssertionError) as excinfo:
            _assert_store_untouched(decoy, fingerprint)

        assert "หายไประหว่างรันชุดเทสต์" in str(excinfo.value)

    def test_file_rewritten_mid_run_is_a_different_message(self, tmp_path):
        decoy = tmp_path / "price_alerts.json"
        decoy.write_text('{"alerts": []}\n', encoding="utf-8")
        fingerprint = (decoy.stat().st_mtime_ns, decoy.stat().st_size)
        decoy.write_text('{"alerts": [{"id": "x"}]}\n', encoding="utf-8")

        with pytest.raises(AssertionError) as excinfo:
            _assert_store_untouched(decoy, fingerprint)

        assert "ถูกเขียนทับ" in str(excinfo.value)

    def test_absent_from_the_start_is_a_skip_not_a_failure(self, tmp_path):
        with pytest.raises(pytest.skip.Exception):
            _assert_store_untouched(tmp_path / "never_existed.json", None)
