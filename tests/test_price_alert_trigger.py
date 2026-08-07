# -*- coding: utf-8 -*-
"""ตารางความจริงของเงื่อนไข trigger ใน ``check_alerts()`` (AUDIT_2026-08-06 ข้อ 0-D).

ทำไมต้องมีไฟล์นี้: mutation testing รอบ R9 สลับ ``>=`` เป็น ``<=`` ใน ``check_alerts()``
แล้วชุดเทสต์ 568 ตัว **ผ่านหมด** — call-probe ยืนยันว่า ``check_alerts()`` ไม่เคยถูกเรียก
สักครั้งตลอดชุดเทสต์ ⇒ alert "สูงกว่า X" กลายเป็น "ต่ำกว่า X" ได้โดยไม่มีอะไรจับ
ไฟล์นี้คือตาข่ายก่อนเข้าไปแก้ A1/D1 (คลัง alert + การนับ checked)

ความปลอดภัย: ทุกเคส
- ชี้ ``ALERTS_PATH`` ไปที่ ``tmp_path`` — ไม่แตะ ``alerts/data/price_alerts.json`` ของจริง
- stub ``load_config`` ให้ webhook ว่าง **และ** stub ``send_discord_webhook`` เป็นตัวนับ
  (คอนเทนเนอร์เทสต์โหลด ``.env`` จริงที่มี ``DISCORD_WEBHOOK_URL`` อยู่ — ห้ามยิงจริง)
- stub ``get_price_snapshots`` — ไม่ยิง yfinance
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

import alerts.price_alert as pa

TARGET = 100.0


def _write_alerts(path: Path, alerts: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"alerts": alerts}, ensure_ascii=False), encoding="utf-8")


def _read_alerts(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["alerts"]


def _alert(ticker: str, alert_type: str, price: float = TARGET, alert_id: str = "a1") -> dict:
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


@pytest.fixture
def alert_env(tmp_path, monkeypatch):
    """สภาพแวดล้อมปลอดภัย: ไฟล์ชั่วคราว + ไม่มี webhook + ไม่มี network."""
    store = tmp_path / "price_alerts.json"
    monkeypatch.setattr(pa, "ALERTS_PATH", store)

    sent: list[dict] = []

    def _fake_webhook(**kwargs):
        sent.append(kwargs)
        return {"success": True}

    monkeypatch.setattr(pa, "send_discord_webhook", _fake_webhook)
    monkeypatch.setattr(
        pa,
        "load_config",
        lambda: {"notifications": {"discord_webhook_url": ""}},
    )

    def set_prices(prices: dict[str, float]) -> None:
        snapshots = {
            ticker: {"latest_price": float(price), "previous_close": float(price)}
            for ticker, price in prices.items()
        }
        monkeypatch.setattr(pa, "get_price_snapshots", lambda tickers: snapshots)

    set_prices({})
    return {"store": store, "sent": sent, "set_prices": set_prices}


class TestTriggerTruthTable:
    """6 ช่อง: (above|below) × (ราคา > เป้า | = เป้า | < เป้า).

    เส้นแบ่งที่ราคา "เท่ากับ" เป้าหมายคือจุดที่ mutation ``>=``/``<=`` รอดมาได้
    """

    @pytest.mark.parametrize(
        "alert_type,price,should_trigger",
        [
            ("above", 101.0, True),    # สูงกว่าเป้า → เตือน
            ("above", 100.0, True),    # เท่ากับเป้า → เตือน (>= ไม่ใช่ >)
            ("above", 99.0, False),    # ต่ำกว่าเป้า → ห้ามเตือน
            ("below", 99.0, True),     # ต่ำกว่าเป้า → เตือน
            ("below", 100.0, True),    # เท่ากับเป้า → เตือน (<= ไม่ใช่ <)
            ("below", 101.0, False),   # สูงกว่าเป้า → ห้ามเตือน
        ],
    )
    def test_truth_table(self, alert_env, alert_type, price, should_trigger):
        _write_alerts(alert_env["store"], [_alert("VOO", alert_type)])
        alert_env["set_prices"]({"VOO": price})

        result = pa.check_alerts()

        triggered_ids = [item["id"] for item in result["triggered"]]
        assert triggered_ids == (["a1"] if should_trigger else [])

        stored = _read_alerts(alert_env["store"])[0]
        assert bool(stored["triggered"]) is should_trigger
        if should_trigger:
            assert stored["triggered_price"] == pytest.approx(price)
            assert stored["triggered_at"] is not None
            assert result["triggered"][0]["alert_type"] == alert_type
            assert result["triggered"][0]["target_price"] == pytest.approx(TARGET)
            assert result["triggered"][0]["current_price"] == pytest.approx(price)
        else:
            assert stored["triggered_price"] is None
            assert stored["triggered_at"] is None

    def test_above_and_below_never_agree_on_the_same_price(self, alert_env):
        """กันการสลับเครื่องหมาย: ราคาเดียวกันต้องปลุก above หรือ below อย่างละหนึ่ง."""
        _write_alerts(
            alert_env["store"],
            [_alert("VOO", "above", alert_id="hi"), _alert("VOO", "below", alert_id="lo")],
        )
        alert_env["set_prices"]({"VOO": 101.0})

        result = pa.check_alerts()
        assert [item["id"] for item in result["triggered"]] == ["hi"]

    def test_already_triggered_alert_is_not_rechecked(self, alert_env):
        done = _alert("VOO", "above")
        done["triggered"] = True
        _write_alerts(alert_env["store"], [done])
        alert_env["set_prices"]({"VOO": 101.0})

        result = pa.check_alerts()
        assert result["triggered"] == []


class TestUnavailablePrice:
    """ดึงราคาไม่ได้ ≠ ไม่ถึงเงื่อนไข — ห้ามกลายเป็น "ตรวจแล้วไม่มีอะไร"."""

    def test_missing_price_never_triggers_and_stays_pending(self, alert_env):
        """ต้องไม่ trigger จากราคาที่ไม่มี และ alert ต้องยังค้างรอรอบถัดไป."""
        _write_alerts(alert_env["store"], [_alert("AAPL", "above", price=1.0)])
        alert_env["set_prices"]({})  # ดึงราคาไม่ได้เลยสักตัว

        result = pa.check_alerts()

        assert result["triggered"] == []
        stored = _read_alerts(alert_env["store"])[0]
        assert bool(stored["triggered"]) is False
        assert stored["triggered_price"] is None

    def test_missing_price_does_not_block_the_ticker_that_has_one(self, alert_env):
        _write_alerts(
            alert_env["store"],
            [_alert("VOO", "above", alert_id="voo"), _alert("AAPL", "above", alert_id="aapl")],
        )
        alert_env["set_prices"]({"VOO": 101.0})

        result = pa.check_alerts()
        assert [item["id"] for item in result["triggered"]] == ["voo"]

    # xfail ถูกถอดออกเมื่อ 2026-08-07 พร้อมการแก้ AUDIT_2026-08-06 ข้อ D1.1
    # (`checked` นับเฉพาะ alert ที่มีราคาจริง + เพิ่มช่อง `unchecked`) — เทสต์นี้เป็นตาข่ายจริงแล้ว
    def test_unchecked_alert_is_reported_not_counted_as_checked(self, alert_env):
        _write_alerts(
            alert_env["store"],
            [_alert("VOO", "above", alert_id="voo"), _alert("AAPL", "above", alert_id="aapl")],
        )
        alert_env["set_prices"]({"VOO": 99.0})  # VOO มีราคา (ยังไม่ถึงเป้า), AAPL ไม่มี

        result = pa.check_alerts()

        assert result["checked"] == 1, "นับเฉพาะ alert ที่มีราคาจริงให้ตรวจ"
        assert [row["ticker"] for row in result.get("unchecked", [])] == ["AAPL"]


class TestNoRealSideEffects:
    """กันเทสต์ชุดนี้เองยิง Discord จริง (คอนเทนเนอร์มี DISCORD_WEBHOOK_URL ของจริงใน .env)."""

    def test_no_webhook_is_sent_when_url_is_empty(self, alert_env):
        _write_alerts(alert_env["store"], [_alert("VOO", "above")])
        alert_env["set_prices"]({"VOO": 101.0})

        result = pa.check_alerts()

        assert alert_env["sent"] == []
        assert result["daily_discord_result"]["skipped"] is True

    def test_real_alert_file_is_untouched(self, alert_env):
        """ALERTS_PATH ที่ถูก monkeypatch ต้องไม่ใช่ไฟล์จริงของผู้ใช้."""
        real = _ROOT / "alerts" / "data" / "price_alerts.json"
        assert pa.ALERTS_PATH.resolve() != real.resolve()
