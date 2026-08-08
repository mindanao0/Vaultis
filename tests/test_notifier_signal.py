# -*- coding: utf-8 -*-
"""ตาข่ายพฤติกรรมของ ``alerts/notifier.py`` — การ์ด Discord ที่ผู้ใช้อ่านจริงบนมือถือ.

docstring หัวไฟล์ของ ``alerts/notifier.py`` อ้างถึงไฟล์นี้ว่าเป็น "ตัวตรึงพฤติกรรม"
(``tests/test_notifier_signal.py`` ตรึงไว้) แต่ไฟล์นี้ไม่เคยถูกสร้างจริง — นี่คือไฟล์นั้น

บั๊กสองข้อจาก AUDIT_ROUND2_2026-08-07 (P2) ที่ตาข่ายนี้ต้องกันไม่ให้กลับมา:

1. **การ์ดเนื้อความว่าง** — คอมมิต ``fa5b139`` ("Fix encoding - change Thai text to
   English") ลบสตริงไทยออกจากไฟล์ทั้งไฟล์โดยไม่ได้ใส่ภาษาอังกฤษกลับเข้าไป เหลือแต่โครง
   f-string เปล่า ⇒ ผู้ใช้ได้การ์ดหน้าตาแบบ ``"MA200:  MA200 \\nSignal: "``
   (โดนทั้ง technical alert, สรุปรายสัปดาห์, DCA reminder และข้อความทดสอบ)
   ไม่มีเทสต์ตัวไหนจับได้เลย **เพราะไม่มีใครตรวจ "เนื้อความ"** — เทสต์เดิมตรวจแค่
   ``success`` กับ ``status_code`` ซึ่งเขียวสนิทตอนส่งการ์ดเปล่า
   ⇒ :func:`assert_message_has_real_content` ตรวจทุกบรรทัดของทุกตัวสร้างข้อความ
2. **ไฟล์นี้เคยตัดสินสัญญาณเอง** ด้วย ``if rsi < 30 → สีเขียว`` โดยไม่แตะ
   ``technical/signal_rules.py`` ⇒ RSI 22 ที่ราคา**ต่ำกว่า** MA200 (oversold ในขาลง =
   มีดที่กำลังตก) ได้การ์ดสีเขียวชวนซื้อ เหมือนกันทุกไบต์กับ RSI 22 ที่ราคาเหนือ MA200
   ⇒ :class:`TestSignalComesFromCentralRules` ตรึงว่าสองเคสนี้ต้องได้คนละสี และสีฝั่งขาลง
   ต้องไม่อยู่ใน ``GREEN_SIGNAL_COLORS`` (ตรึงเป็น**คุณสมบัติ** ไม่ใช่เลขฮาร์ดโค้ด)

และกฎ "สามสถานะห้ามยุบรวมกัน" ของ CLAUDE.md:
``ตรวจไม่ได้`` ≠ ``ตรวจแล้วไม่มีอะไรต้องเตือน`` ≠ ``ส่งแล้ว``
(:class:`TestThreeStatusesNeverCollapse`)

**ไม่มีการยิงเน็ตในไฟล์นี้** — fixture ``_no_real_network_no_real_config`` แทน
``notifier.requests`` ด้วยของปลอมที่ระเบิดทันที และแทน ``notifier.load_config``
ด้วยตัวที่ระเบิดเช่นกัน (เครื่องนี้มี webhook/คีย์จริงอยู่ใน ``.env`` และ ``config.json``
จริงถูก mount ที่ ``/app`` ตอนรันเทสต์)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

import alerts.notifier as notifier
from technical import signal_rules

NAN = float("nan")

# ตัวอย่างจริงของการ์ดที่ผู้ใช้ได้รับหลังคอมมิต fa5b139 — ใช้ทดสอบ "ตัวตรวจ" เองด้วย
FA5B139_BROKEN_CARD = "MA200:  MA200 \nSignal: "


# --------------------------------------------------------------------------- ของปลอมแทน network
class _FakeResponse:
    def __init__(self, status_code: int = 204) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


class _RecordingRequests:
    """ยืนแทนโมดูล ``requests`` **เฉพาะใน namespace ของ notifier** — เก็บ payload ไว้ตรวจ.

    ใช้ ``monkeypatch.setattr(notifier, "requests", ...)`` แทนการ patch ``requests.post``
    ทั้งโปรเซส เพื่อไม่ให้รั่วไปกระทบโมดูลอื่นในรอบเดียวกัน
    """

    def __init__(self, status_code: int = 204) -> None:
        self.calls: list[dict[str, Any]] = []
        self._status_code = status_code

    def post(self, url: str, json: Any = None, timeout: Any = None, **_kw: Any) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse(self._status_code)

    def get(self, *_a: Any, **_kw: Any) -> None:
        raise AssertionError("เทสต์นี้ห้ามยิง HTTP GET จริง")


class _ExplodingRequests:
    def post(self, *_a: Any, **_kw: Any) -> None:
        raise AssertionError("เทสต์นี้ห้ามส่ง Discord จริง")

    def get(self, *_a: Any, **_kw: Any) -> None:
        raise AssertionError("เทสต์นี้ห้ามยิง HTTP GET จริง")


@pytest.fixture(autouse=True)
def _no_real_network_no_real_config(monkeypatch):
    """ค่าเริ่มต้นของทุกเคส: ยิงเน็ตไม่ได้ และอ่าน ``config.json`` จริงไม่ได้."""

    def _boom_config() -> dict:
        raise AssertionError("เทสต์นี้ห้ามอ่าน config.json จริง — ให้ stub notifier.load_config")

    monkeypatch.setattr(notifier, "requests", _ExplodingRequests())
    monkeypatch.setattr(notifier, "load_config", _boom_config)


@pytest.fixture()
def recorder(monkeypatch) -> _RecordingRequests:
    """ให้ ``notifier`` "ส่ง" ได้โดยไม่ออกไปไหน แล้วคืนตัวเก็บ payload."""
    rec = _RecordingRequests()
    monkeypatch.setattr(notifier, "requests", rec)
    return rec


# --------------------------------------------------------------------------- ตัวตรวจ "เนื้อความจริง"
_THAI_CHAR = re.compile("[฀-๿]")  # บล็อก Thai ทั้งบล็อกใน Unicode
_EMPTY_BRACKET = re.compile(r"[(\[]\s*[)\]]")
_SEPARATOR_CHARS = set("─—-=_ ")


def _is_blank_or_separator(line: str) -> bool:
    stripped = line.strip()
    return not stripped or set(stripped) <= _SEPARATOR_CHARS


def assert_message_has_real_content(
    text: str,
    *,
    headers: tuple[str, ...] = (),
    lines_without_thai: tuple[str, ...] = (),
) -> None:
    """ฟ้องถ้าข้อความมี "ช่องว่างที่ค่าหายไป" แบบที่ ``fa5b139`` ทิ้งไว้.

    ตรวจทีละบรรทัด (ข้ามบรรทัดว่างและเส้นคั่น):

    - **ห้ามลงท้ายด้วย ``:``** เพราะนั่นคือ label ที่ค่าหายไป (``"Signal: "``)
      ยกเว้นบรรทัดที่อยู่ใน ``headers`` ซึ่งเป็นหัวข้อของบล็อกถัดไปจริง ๆ —
      และหัวข้อนั้นต้องมีบรรทัดถัดไปที่ไม่ว่างเสมอ ไม่งั้นก็คือหัวข้อลอย
    - **ห้ามมีช่องว่างติดกันสองตัว** เพราะสตริงไทยที่ถูกลบออกจากกลาง f-string
      จะยุบเหลือช่องว่างคู่ (``"MA200:  MA200"``)
    - **ห้ามมีวงเล็บ/วงเล็บเหลี่ยมเปล่า** (``()``/``[]``) — ช่องค่าที่หายไปอีกแบบ
    - **ต้องมีอักขระไทยอย่างน้อยหนึ่งตัว** ยกเว้นบรรทัดที่ระบุไว้ใน
      ``lines_without_thai`` (ต้องระบุเป็นข้อความเป๊ะ ๆ = ตรึงบรรทัดนั้นไปในตัว)

    ``headers`` / ``lines_without_thai`` เป็น whitelist แบบ **ข้อความเต็มบรรทัด**
    โดยตั้งใจ: ถ้าใครลบภาษาไทยออกจากบรรทัดที่ได้รับยกเว้น บรรทัดนั้นก็จะไม่ตรงกับ
    whitelist อีกต่อไป แล้วกฎที่เหลือจะจับได้อยู่ดี
    """
    assert isinstance(text, str), f"ข้อความต้องเป็น str ไม่ใช่ {type(text)!r}"
    assert text.strip(), "ข้อความว่างทั้งก้อน — ผู้ใช้ได้การ์ดเปล่า"

    lines = text.split("\n")
    for index, line in enumerate(lines):
        if _is_blank_or_separator(line):
            continue
        where = f"บรรทัดที่ {index + 1}: {line!r}"

        if line.rstrip().endswith(":"):
            assert line in headers, (
                f"{where} — label ลงท้ายด้วย ':' แล้วไม่มีค่าตามมา "
                "(รอยเดิมของ fa5b139: 'Signal: ')"
            )
            following = [ln for ln in lines[index + 1:] if ln.strip()]
            assert following, f"{where} — เป็นหัวข้อที่ไม่มีเนื้อหาตามมาเลย"

        assert "  " not in line, (
            f"{where} — มีช่องว่างติดกันสองตัว = ค่าที่ควรอยู่ตรงนั้นหายไป "
            "(รอยเดิมของ fa5b139: 'MA200:  MA200')"
        )
        assert not _EMPTY_BRACKET.search(line), f"{where} — มีวงเล็บเปล่า = ค่าข้างในหายไป"

        if line in lines_without_thai:
            continue
        assert _THAI_CHAR.search(line), (
            f"{where} — ไม่มีอักขระไทยเลย ข้อความนี้คือสิ่งที่ผู้ใช้ไทยอ่านจริงบนมือถือ "
            "(rule: ภาษาไทยตลอด — fa5b139 ลบทิ้งทั้งไฟล์มาแล้ว)"
        )


class TestTheContentCheckerItself:
    """ตัวตรวจต้องจับการ์ดที่พังจริงได้ ไม่งั้นตาข่ายทั้งไฟล์นี้ไร้ความหมาย."""

    def test_rejects_the_actual_fa5b139_card(self):
        with pytest.raises(AssertionError):
            assert_message_has_real_content(FA5B139_BROKEN_CARD)

    @pytest.mark.parametrize(
        "broken",
        [
            "Signal: ",                       # label ที่ค่าหายไป
            "MA200:  MA200 ",                 # ช่องว่างคู่จากสตริงที่ถูกลบ
            "RSI: 45.0 ()",                   # วงเล็บเปล่า
            "Portfolio value: 100.00",        # ไม่มีภาษาไทยเลย
            "   ",                            # ว่างทั้งก้อน
        ],
    )
    def test_rejects_each_broken_shape(self, broken):
        with pytest.raises(AssertionError):
            assert_message_has_real_content(broken)

    def test_accepts_a_healthy_card(self):
        assert_message_has_real_content(
            "มูลค่าพอร์ตปัจจุบัน: $1,000.00\nสถานะพอร์ต: ✅ ยังไม่ต้อง Rebalance"
        )


# --------------------------------------------------------------------------- ชุดข้อมูลทดสอบ
#: oversold ทั้งคู่ ต่างกันแค่ "อยู่เหนือหรือใต้ MA200" — คู่นี้คือหัวใจของบั๊กข้อ 2
OVERSOLD_UPTREND = dict(symbol="VOO", rsi=22.0, price=110.0, ma200=100.0, previous_price=109.0, ma50=105.0)
OVERSOLD_DOWNTREND = dict(symbol="VOO", rsi=22.0, price=90.0, ma200=100.0, previous_price=91.0, ma50=95.0)

#: ครบข้อมูล แต่ไม่มีอะไรต้องเตือน (RSI โซนกลาง + ไม่มีการตัด MA200)
NOTHING_TO_REPORT = dict(symbol="VOO", rsi=50.0, price=110.0, ma200=100.0, previous_price=109.0, ma50=105.0)

#: (rsi, price, ma200, ma50, previous_price) — กริดที่ครอบทุกสัญญาณกลางที่ส่งจริง
SIGNAL_GRID = [
    (22.0, 110.0, 100.0, 105.0, 109.0),   # oversold ในขาขึ้น  → accumulate
    (22.0, 90.0, 100.0, 95.0, 91.0),      # oversold ในขาลง    → downtrend_watch
    (78.0, 120.0, 100.0, 110.0, 119.0),   # overbought ในขาขึ้น → overbought_caution
    (78.0, 90.0, 100.0, 95.0, 91.0),      # overbought ในขาลง   → overbought_caution
    (50.0, 101.0, 100.0, 99.0, 99.0),     # golden cross        → neutral
    (50.0, 99.0, 100.0, 101.0, 101.0),    # death cross         → downtrend
]


def _build(**over: Any) -> dict[str, Any]:
    kwargs = dict(symbol="VOO", rsi=50.0, price=110.0, ma200=100.0, previous_price=109.0, ma50=105.0)
    kwargs.update(over)
    return notifier.build_technical_alert_payload(**kwargs)


def _description(built: dict[str, Any]) -> str:
    assert built["payload"] is not None, "เคสนี้ต้องมี payload ให้ตรวจ"
    return built["payload"]["embeds"][0]["description"]


def _status_triple(result: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (result.get("success"), result.get("skipped"), result.get("data_ok"))


# --------------------------------------------------------------------------- บั๊กข้อ 2
class TestSignalComesFromCentralRules:
    """สีและป้ายต้องมาจาก ``technical/signal_rules.py`` ที่เดียว — ห้ามตัดสินเองในไฟล์นี้."""

    def test_oversold_in_downtrend_and_uptrend_are_not_the_same_card(self):
        """หัวใจของบั๊ก: เดิมสองเคสนี้ให้ payload เท่ากันทุกไบต์ (สีเขียวทั้งคู่)."""
        up = _build(**OVERSOLD_UPTREND)
        down = _build(**OVERSOLD_DOWNTREND)

        assert up["send"] is True and down["send"] is True
        assert up["color"] != down["color"], (
            "RSI 22 เหนือ MA200 กับ RSI 22 ใต้ MA200 ได้สีเดียวกัน = "
            "มีดที่กำลังตกถูกระบายเป็นจังหวะสะสม (AUDIT_ROUND2_2026-08-07)"
        )
        assert up["payload"] != down["payload"]

    def test_downtrend_oversold_is_never_painted_green(self):
        """สีคือสิ่งแรกที่ผู้ใช้เห็นก่อนอ่านตัวหนังสือ — ฝั่งขาลงห้ามอยู่ในเซ็ตสีเขียว."""
        down = _build(**OVERSOLD_DOWNTREND)
        assert down["signal"] == signal_rules.DOWNTREND_WATCH
        assert down["color"] not in notifier.GREEN_SIGNAL_COLORS

    def test_uptrend_oversold_is_the_green_one(self):
        up = _build(**OVERSOLD_UPTREND)
        assert up["signal"] == signal_rules.ACCUMULATE
        assert up["color"] in notifier.GREEN_SIGNAL_COLORS

    @pytest.mark.parametrize(
        "central",
        [signal_rules.DOWNTREND, signal_rules.DOWNTREND_WATCH, signal_rules.NO_DATA],
    )
    def test_no_down_or_unknown_signal_carries_a_green_color(self, central):
        """ตรึงเป็นคุณสมบัติของตาราง ไม่ใช่เลขฮาร์ดโค้ด — เพิ่มสัญญาณใหม่ก็ยังโดนตรวจ."""
        assert notifier.SIGNAL_COLORS[central] not in notifier.GREEN_SIGNAL_COLORS

    def test_every_central_signal_has_a_color_and_an_emoji(self):
        """สัญญาณที่ไม่มีในตาราง = ``SIGNAL_COLORS.get()`` คืน ``None`` แล้วการ์ดไร้สี."""
        centrals = {
            signal_rules.NO_DATA,
            signal_rules.ACCUMULATE,
            signal_rules.BULLISH,
            signal_rules.OVERBOUGHT_CAUTION,
            signal_rules.DOWNTREND_WATCH,
            signal_rules.DOWNTREND,
            signal_rules.NEUTRAL,
        }
        assert centrals <= set(notifier.SIGNAL_COLORS)
        assert centrals <= set(notifier.SIGNAL_EMOJI)

    @pytest.mark.parametrize("rsi,price,ma200,ma50,prev", SIGNAL_GRID)
    def test_signal_and_color_track_dca_signal(self, rsi, price, ma200, ma50, prev):
        """ป้อนข้อมูลชุดเดียวกันเข้า ``dca_signal()`` ต้องได้ผลเท่ากันเสมอ."""
        built = _build(rsi=rsi, price=price, ma200=ma200, ma50=ma50, previous_price=prev)
        expected = signal_rules.dca_signal(price, ma50, ma200, rsi)
        assert built["signal"] == expected
        assert built["color"] == notifier.SIGNAL_COLORS[expected]

    @pytest.mark.parametrize("rsi,price,ma200,ma50,prev", SIGNAL_GRID)
    def test_label_tracks_overall_signal(self, rsi, price, ma200, ma50, prev):
        built = _build(rsi=rsi, price=price, ma200=ma200, ma50=ma50, previous_price=prev)
        golden = bool(prev <= ma200 < price)
        death = bool(prev >= ma200 > price)
        assert built["label"] == signal_rules.overall_signal(
            built["signal"], golden_cross=golden, death_cross=death, rsi=rsi
        )

    def test_downtrend_card_does_not_read_as_buy(self):
        """ข้อความบนการ์ดขาลงต้องไม่ชวนสะสม — สีอย่างเดียวไม่พอ คนอ่านตัวหนังสือด้วย."""
        text = _description(_build(**OVERSOLD_DOWNTREND))
        assert notifier.ACTION_TEXT_TH["hold"] in text
        assert notifier.ACTION_TEXT_TH["buy"] not in text
        assert signal_rules.thai_description(signal_rules.DOWNTREND_WATCH) in text
        assert "ราคาอยู่ต่ำกว่า MA200" in text

    def test_uptrend_card_says_accumulate(self):
        text = _description(_build(**OVERSOLD_UPTREND))
        assert notifier.ACTION_TEXT_TH["buy"] in text
        assert signal_rules.thai_description(signal_rules.ACCUMULATE) in text
        assert "ราคาอยู่เหนือ MA200" in text

    def test_rsi_line_zone_comes_from_rsi_zone(self):
        """บรรทัด RSI ต้องเดินตาม ``rsi_zone()`` และอ้างเส้นแบ่งจากค่าคงที่กลาง."""
        up = _description(_build(**OVERSOLD_UPTREND))
        assert "Oversold Zone" in up
        assert f"{signal_rules.RSI_OVERSOLD:.0f}" in up
        hot = _description(_build(rsi=78.0, price=120.0, ma200=100.0, ma50=110.0, previous_price=119.0))
        assert "Overbought Zone" in hot
        assert f"{signal_rules.RSI_OVERBOUGHT:.0f}" in hot


# --------------------------------------------------------------------------- สามสถานะ
class TestThreeStatusesNeverCollapse:
    """``ตรวจไม่ได้`` ≠ ``ตรวจแล้วไม่มีอะไรต้องเตือน`` ≠ ``ส่งแล้ว`` (CLAUDE.md)."""

    MISSING = [
        pytest.param({"rsi": None}, id="rsi=None"),
        pytest.param({"rsi": NAN}, id="rsi=NaN"),
        pytest.param({"price": None}, id="price=None"),
        pytest.param({"price": NAN}, id="price=NaN"),
        pytest.param({"ma200": None}, id="ma200=None"),
        pytest.param({"ma200": NAN}, id="ma200=NaN"),
    ]

    @pytest.mark.parametrize("missing", MISSING)
    def test_missing_input_is_no_data_and_never_sends(self, missing):
        built = _build(**missing)
        assert built["signal"] == signal_rules.NO_DATA
        assert built["send"] is False
        assert built["payload"] is None
        assert built["color"] == notifier.SIGNAL_COLORS[signal_rules.NO_DATA]
        assert built["color"] not in notifier.GREEN_SIGNAL_COLORS

    @pytest.mark.parametrize("missing", MISSING)
    def test_no_data_reason_denies_being_normal(self, missing):
        """"ตรวจไม่ได้" ต้องพูดออกมาเป็นภาษาไทยว่าไม่ได้แปลว่า RSI ปกติ."""
        reason = _build(**missing)["reason"]
        assert "ข้อมูลไม่พร้อม" in reason
        assert "ไม่ได้แปลว่า" in reason

    @pytest.mark.parametrize("missing", MISSING)
    def test_send_reports_no_data_as_failure(self, missing, recorder):
        """เดิมกลืนเป็น ``success=True`` ⇒ scheduler พิมพ์ว่า "ตรวจแล้วปกติ"."""
        result = notifier.send_technical_alert(
            webhook_url="https://discord.test/hook",
            symbol="VOO",
            **{**dict(rsi=50.0, price=110.0, ma200=100.0, previous_price=109.0), **missing},
        )
        assert result["success"] is False
        assert result["data_ok"] is False
        assert result["skipped"] is True
        assert "ข้อมูลไม่พร้อม" in str(result["error"])
        assert recorder.calls == [], "ข้อมูลไม่ครบแล้วยังยิง Discord = ส่งสัญญาณจากข้อมูลที่ไม่มี"

    def test_nothing_to_report_is_a_successful_check(self, recorder):
        """ครบข้อมูล + ไม่มีสัญญาณ = ตรวจแล้วจริง ต้องไม่ถูกนับเป็นความล้มเหลว."""
        built = _build(**NOTHING_TO_REPORT)
        assert built["send"] is False
        assert built["signal"] != signal_rules.NO_DATA

        result = notifier.send_technical_alert(webhook_url="https://discord.test/hook", **NOTHING_TO_REPORT)
        assert result["success"] is True
        assert result["skipped"] is True
        assert result["data_ok"] is True
        assert "ไม่มีสัญญาณเทคนิคที่ต้องแจ้งเตือน" in result["reason"]
        assert recorder.calls == []

    def test_the_three_outcomes_are_distinguishable(self, recorder):
        """หัวใจของกฎ: ผู้เรียกต้องแยกสามกรณีนี้ออกจากกันได้จากค่าที่คืนอย่างเดียว."""
        blind = notifier.send_technical_alert(
            webhook_url="https://discord.test/hook",
            symbol="VOO", rsi=None, price=110.0, ma200=100.0, previous_price=109.0,
        )
        quiet = notifier.send_technical_alert(webhook_url="https://discord.test/hook", **NOTHING_TO_REPORT)
        sent = notifier.send_technical_alert(webhook_url="https://discord.test/hook", **OVERSOLD_DOWNTREND)

        triples = [_status_triple(blind), _status_triple(quiet), _status_triple(sent)]
        assert len(set(triples)) == 3, f"สามสถานะยุบรวมกัน: {triples}"
        assert len(recorder.calls) == 1, "ต้องยิง Discord เฉพาะเคสที่มีสัญญาณจริงเท่านั้น"

    def test_sent_result_carries_signal_and_label(self, recorder):
        result = notifier.send_technical_alert(webhook_url="https://discord.test/hook", **OVERSOLD_DOWNTREND)
        assert result["success"] is True
        assert result["signal"] == signal_rules.DOWNTREND_WATCH
        assert result["label"] == "hold"
        assert recorder.calls[0]["json"]["embeds"][0]["color"] not in notifier.GREEN_SIGNAL_COLORS

    def test_missing_webhook_is_a_failure_not_a_silent_skip(self, recorder):
        result = notifier.send_technical_alert(webhook_url="", **OVERSOLD_DOWNTREND)
        assert result["success"] is False
        assert "webhook_url" in str(result["error"])
        assert recorder.calls == []


# --------------------------------------------------------------------------- previous_price
class TestUnusablePreviousPrice:
    """"ตรวจการตัด MA200 ไม่ได้" ห้ามเงียบ ๆ แปลว่า "ไม่มีการตัด"."""

    UNUSABLE = [pytest.param(None, id="prev=None"), pytest.param(NAN, id="prev=NaN")]

    @pytest.mark.parametrize("prev", UNUSABLE)
    def test_says_the_cross_could_not_be_checked(self, prev):
        text = _description(_build(**{**OVERSOLD_UPTREND, "previous_price": prev}))
        assert "ตรวจการตัด MA200 ไม่ได้" in text
        assert "ไม่ใช่" in text, "ต้องมีประโยคปฏิเสธชัด ๆ ว่านี่ไม่ใช่ 'ไม่มีการตัด'"

    @pytest.mark.parametrize("prev", UNUSABLE)
    def test_never_claims_a_cross_it_could_not_see(self, prev):
        text = _description(_build(**{**OVERSOLD_UPTREND, "previous_price": prev}))
        assert "Golden Signal" not in text
        assert "Death Signal" not in text

    @pytest.mark.parametrize("prev", UNUSABLE)
    def test_cannot_be_upgraded_to_strong_buy(self, prev):
        """``strong_buy`` ต้องมี golden cross จริง — ตรวจไม่ได้ ≠ ตรวจแล้วเจอ."""
        built = _build(**{**OVERSOLD_UPTREND, "previous_price": prev})
        assert built["label"] != "strong_buy"

    def test_usable_previous_price_shows_no_warning(self):
        """เคสปกติต้องไม่มีคำเตือน ไม่งั้นคำเตือนกลายเป็นเสียงรบกวนที่ไม่มีใครอ่าน."""
        text = _description(_build(**OVERSOLD_UPTREND))
        assert "ตรวจการตัด MA200 ไม่ได้" not in text

    def test_golden_cross_is_reported_when_it_really_happens(self):
        text = _description(_build(rsi=50.0, price=101.0, ma200=100.0, ma50=99.0, previous_price=99.0))
        assert "Golden Signal" in text
        assert "ราคาตัดขึ้นเหนือ MA200" in text

    def test_death_cross_is_reported_when_it_really_happens(self):
        built = _build(rsi=50.0, price=99.0, ma200=100.0, ma50=101.0, previous_price=101.0)
        assert "Death Signal" in _description(built)
        assert built["label"] == "sell"


# --------------------------------------------------------------------------- บั๊กข้อ 1 (fa5b139)
class TestEveryOutgoingMessageHasRealContent:
    """ทุกตัวสร้างข้อความต้องมีเนื้อความจริง — นี่คือตาข่ายที่ ``fa5b139`` ลอดไปได้."""

    #: บรรทัด RSI โซนกลางเป็นบรรทัดตัวเลข/ชื่อโซนล้วน (ไม่มีวงเล็บภาษาไทยเหมือนโซนอื่น)
    #: ระบุเป็นข้อความเป๊ะ ๆ เพื่อให้ยังตรึงเนื้อความของบรรทัดนี้ไว้ด้วย
    NEUTRAL_RSI_LINE = (
        f"RSI: 50.0 ⚪ Neutral Zone "
        f"({signal_rules.RSI_OVERSOLD:.0f}–{signal_rules.RSI_OVERBOUGHT:.0f})"
    )

    @pytest.mark.parametrize("rsi,price,ma200,ma50,prev", SIGNAL_GRID)
    def test_technical_alert_description(self, rsi, price, ma200, ma50, prev):
        built = _build(rsi=rsi, price=price, ma200=ma200, ma50=ma50, previous_price=prev)
        assert_message_has_real_content(
            _description(built), lines_without_thai=(self.NEUTRAL_RSI_LINE,)
        )

    @pytest.mark.parametrize("prev", [None, NAN])
    def test_technical_alert_description_when_cross_is_unknown(self, prev):
        assert_message_has_real_content(_description(_build(**{**OVERSOLD_UPTREND, "previous_price": prev})))

    def test_technical_alert_title_names_the_symbol(self):
        title = _build(**{**OVERSOLD_DOWNTREND, "symbol": "GLDM"})["payload"]["embeds"][0]["title"]
        assert "GLDM" in title
        assert title.strip("📊 —")

    @pytest.mark.parametrize("rebalance", [True, False])
    @pytest.mark.parametrize("value,capital", [(1100.0, 1000.0), (900.0, 1000.0), (0.0, 0.0)])
    def test_weekly_summary_description(self, value, capital, rebalance):
        _title, description, _positive = notifier.build_weekly_summary_message(value, capital, rebalance)
        assert_message_has_real_content(description)

    @pytest.mark.parametrize(
        "budget,fx,advice",
        [
            (5000.0, 33.5, "- ซื้อ VOO 2,000 บาท"),
            (5000.0, 33.5, ""),
            (None, 33.5, "- ซื้อ VOO 2,000 บาท"),
            (NAN, NAN, ""),
        ],
    )
    def test_dca_reminder_message(self, budget, fx, advice):
        text = notifier.build_dca_reminder_message(
            dca_date_text="09/08/2026", dca_budget_thb=budget, fx_rate_thb=fx, ai_advice=advice
        )
        assert_message_has_real_content(
            text, headers=("📊 แผนแบ่งเงินเดือนนี้ (คำนวณจากโมเดล):",)
        )

    def test_test_alert_fields_have_values(self, recorder):
        """ข้อความทดสอบก็โดน ``fa5b139`` ด้วย — ผู้ใช้กดทดสอบแล้วได้การ์ดเปล่า."""
        result = notifier.test_alert(webhook_url="https://discord.test/hook")
        assert result["success"] is True

        embed = recorder.calls[0]["json"]["embeds"][0]
        assert embed["title"].strip("🚀 ")
        fields = {field["name"]: field["value"] for field in embed["fields"]}
        assert set(fields) == {"Status", "Time", "Message"}
        for name, value in fields.items():
            assert str(value).strip(), f"ฟิลด์ {name} ไม่มีค่า = การ์ดทดสอบเปล่า"
        assert _THAI_CHAR.search(fields["Status"]), "ข้อความสถานะต้องเป็นภาษาไทย"
        assert _THAI_CHAR.search(fields["Message"]), "ข้อความยืนยันต้องเป็นภาษาไทย"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", fields["Time"])


# --------------------------------------------------------------------------- สรุปรายสัปดาห์
class TestWeeklySummary:
    """"ยังไม่มีเงินลงทุนสะสม" ≠ "ลงทุนแล้วเสมอตัว" — 0.00% เป็นตัวเลขที่ระบบแต่งขึ้น."""

    @pytest.mark.parametrize("capital", [0.0, -100.0])
    def test_no_invested_capital_is_na_not_zero_percent(self, capital):
        _title, description, _positive = notifier.build_weekly_summary_message(0.0, capital, False)
        assert "n/a" in description
        assert "ยังไม่มีเงินลงทุนสะสม" in description
        assert "0.00%" not in description
        assert "%" not in description.split("ผลตอบแทนรวม:")[1].split("\n")[0]

    def test_real_capital_gets_a_real_percentage(self):
        _title, description, positive = notifier.build_weekly_summary_message(1100.0, 1000.0, False)
        assert "10.00%" in description
        assert "n/a" not in description
        assert positive is True

    def test_loss_is_reported_as_negative_not_hidden(self):
        _title, description, positive = notifier.build_weekly_summary_message(900.0, 1000.0, False)
        assert "-10.00%" in description
        assert positive is False

    @pytest.mark.parametrize("value,capital", [(None, 1000.0), (NAN, 1000.0), (1100.0, None), (1100.0, NAN)])
    def test_unusable_numbers_raise_instead_of_printing_zero(self, value, capital):
        """ค่าที่คำนวณไม่ได้ต้องดังออกมา ห้ามกลายเป็น $0.00 บนการ์ด."""
        with pytest.raises(RuntimeError) as exc:
            notifier.build_weekly_summary_message(value, capital, False)
        assert "ข้อผิดพลาด" in str(exc.value)

    @pytest.mark.parametrize("rebalance,needle", [(True, "ถึงเกณฑ์ต้อง Rebalance"), (False, "ยังไม่ต้อง Rebalance")])
    def test_rebalance_status_is_spelled_out(self, rebalance, needle):
        _title, description, _positive = notifier.build_weekly_summary_message(1100.0, 1000.0, rebalance)
        assert needle in description

    def test_all_four_labels_survive(self):
        """ตรึงหัวข้อไทยทั้งสี่บรรทัด — ``fa5b139`` ลบทิ้งทั้งหมดมาแล้ว."""
        _title, description, _positive = notifier.build_weekly_summary_message(1100.0, 1000.0, False)
        for label in ("มูลค่าพอร์ตปัจจุบัน:", "เงินลงทุนสะสม:", "ผลตอบแทนรวม:", "สถานะพอร์ต:"):
            assert label in description
        assert description.count("\n") == 3


# --------------------------------------------------------------------------- DCA reminder
class TestDcaReminder:
    """งบ/FX ที่อ่านไม่ได้ต้องติดป้ายเตือน ไม่ใช่พิมพ์ 0 ให้ผู้ใช้เอาไปโอนเงินจริง."""

    def test_normal_message_keeps_every_thai_section(self):
        text = notifier.build_dca_reminder_message(
            dca_date_text="09/08/2026", dca_budget_thb=5000.0, fx_rate_thb=33.5,
            ai_advice="- ซื้อ VOO 2,000 บาท",
        )
        for needle in (
            "DCA Reminder", "พรุ่งนี้", "09/08/2026",
            "งบ DCA เดือนนี้", "5,000 บาท",
            "FX Rate วันนี้", "33.50 THB/USD",
            "แผนแบ่งเงินเดือนนี้", "- ซื้อ VOO 2,000 บาท",
            "อย่าลืมเปิด Dime",
        ):
            assert needle in text, f"หายไปจากข้อความ DCA: {needle!r}"

    @pytest.mark.parametrize("budget", [None, NAN])
    def test_unreadable_budget_is_flagged_not_zero(self, budget):
        text = notifier.build_dca_reminder_message(dca_budget_thb=budget, fx_rate_thb=33.5)
        assert "อ่านงบ DCA ไม่ได้" in text
        assert "⚠️" in text
        assert "0 บาท" not in text

    @pytest.mark.parametrize("fx", [None, NAN])
    def test_unreadable_fx_is_flagged_not_zero(self, fx):
        text = notifier.build_dca_reminder_message(dca_budget_thb=5000.0, fx_rate_thb=fx)
        assert "ดึงอัตราแลกเปลี่ยนไม่ได้" in text
        assert "0.00 THB/USD" not in text

    @pytest.mark.parametrize("advice", ["", "   ", None])
    def test_missing_plan_says_so_instead_of_leaving_a_hole(self, advice):
        text = notifier.build_dca_reminder_message(ai_advice=advice)
        assert "ยังไม่มีแผนจัดสรร" in text

    def test_send_uses_the_built_message_verbatim(self, recorder):
        result = notifier.send_dca_reminder(
            webhook_url="https://discord.test/hook", dca_date_text="09/08/2026",
            dca_budget_thb=5000.0, fx_rate_thb=33.5, ai_advice="- ซื้อ VOO 2,000 บาท",
        )
        assert result["success"] is True
        description = recorder.calls[0]["json"]["embeds"][0]["description"]
        assert description == notifier.build_dca_reminder_message(
            dca_date_text="09/08/2026", dca_budget_thb=5000.0, fx_rate_thb=33.5,
            ai_advice="- ซื้อ VOO 2,000 บาท",
        )
        assert_message_has_real_content(
            description, headers=("📊 แผนแบ่งเงินเดือนนี้ (คำนวณจากโมเดล):",)
        )

    def test_explicit_webhook_never_touches_config(self, recorder):
        """fixture ทำให้ ``load_config`` ระเบิด — เคสนี้ผ่านได้ก็ต่อเมื่อไม่ได้เรียกมัน."""
        assert notifier.send_dca_reminder(webhook_url="https://discord.test/hook")["success"] is True

    def test_falls_back_to_config_webhook(self, monkeypatch, recorder):
        monkeypatch.setattr(
            notifier, "load_config",
            lambda: {"notifications": {"discord_webhook_url": "https://discord.test/from-config"}},
        )
        assert notifier.send_dca_reminder(webhook_url="")["success"] is True
        assert recorder.calls[0]["url"] == "https://discord.test/from-config"

    def test_no_webhook_anywhere_is_a_failure(self, monkeypatch, recorder):
        monkeypatch.setattr(notifier, "load_config", lambda: {"notifications": {"discord_webhook_url": ""}})
        result = notifier.send_dca_reminder(webhook_url="")
        assert result["success"] is False
        assert recorder.calls == []


# --------------------------------------------------------------------------- send_discord_webhook
class TestSendDiscordWebhookRejectsEmptyCards:
    """การ์ดเนื้อความว่างต้องดังออกมา ไม่ใช่ปล่อยผ่านเงียบ ๆ (Discord ก็ตอบ 400 อยู่ดี)."""

    @pytest.mark.parametrize("description", ["", "   ", "\n\n", None])
    def test_empty_description_is_rejected_before_sending(self, description, recorder):
        result = notifier.send_discord_webhook(
            webhook_url="https://discord.test/hook", title="หัวข้อ", description=description
        )
        assert result["success"] is False
        assert "ห้ามว่าง" in str(result["error"])
        assert recorder.calls == [], "การ์ดเปล่าต้องไม่ถูกส่งออกไปเลย"

    def test_empty_webhook_is_rejected(self, recorder):
        result = notifier.send_discord_webhook(webhook_url="", title="หัวข้อ", description="เนื้อความ")
        assert result["success"] is False
        assert recorder.calls == []

    def test_real_description_goes_out_unchanged(self, recorder):
        result = notifier.send_discord_webhook(
            webhook_url="https://discord.test/hook",
            title="สรุปพอร์ต",
            description="มูลค่าพอร์ตปัจจุบัน: $1,000.00",
            is_positive=False,
            embed_color=notifier.SIGNAL_COLORS[signal_rules.DOWNTREND],
        )
        assert result["success"] is True
        embed = recorder.calls[0]["json"]["embeds"][0]
        assert embed["description"] == "มูลค่าพอร์ตปัจจุบัน: $1,000.00"
        assert embed["color"] == notifier.SIGNAL_COLORS[signal_rules.DOWNTREND]
        assert "สรุปพอร์ต" in embed["title"]


class TestNeutralZoneDoesNotClaimTheCrossWasChecked:
    """RSI โซนกลาง + ราคาก่อนหน้าใช้ไม่ได้ = "ตรวจการตัดไม่ได้" ห้ามตอบว่า "ไม่มีการตัด".

    ``golden_cross``/``death_cross`` เป็น ``False`` ได้จากสองสาเหตุที่ต่างกันสิ้นเชิง —
    "ตรวจแล้วไม่มีการตัด" กับ "ตรวจไม่ได้เพราะไม่มีราคาก่อนหน้าที่ใช้ได้" — เดิมเส้นทาง
    โซนกลางยุบทั้งสองเป็นผลลัพธ์เดียว แล้วคืน ``data_ok=True`` พร้อมเหตุผลที่ **ยืนยัน**
    ว่า "ไม่มีการตัด MA200" ซึ่งเป็นคำกล่าวเท็จในเคสหลัง ⇒ ผู้เรียก (``main.py`` อ่าน
    ``data_ok``) นับรอบนั้นเป็น "ตรวจแล้วปกติ เงียบได้" ทั้งที่ golden/death cross อาจ
    เกิดขึ้นจริงและไม่มีใครตรวจ (AUDIT_ROUND2_2026-08-07 — กฎสามสถานะห้ามยุบรวม)
    """

    @pytest.mark.parametrize("bad_prev", [None, float("nan")])
    def test_ตรวจการตัดไม่ได้ต้องไม่ถูกนับเป็นตรวจแล้วปกติ(self, bad_prev) -> None:
        built = _build(rsi=50.0, previous_price=bad_prev)

        assert built["send"] is False
        assert built["signal"] != signal_rules.NO_DATA, "RSI/ราคา/MA200 ครบ — ไม่ใช่ NO_DATA"
        assert built["data_ok"] is False, "ตรวจการตัดไม่ได้ ต้องไม่ประกาศว่าข้อมูลพร้อม"
        assert built["cross_checked"] is False
        assert "ไม่ได้" in built["reason"]
        assert "ไม่มีการตัด MA200)" not in built["reason"], (
            "ห้ามยืนยันว่า 'ไม่มีการตัด' ทั้งที่ตรวจไม่ได้ — นั่นคือการกุข้อมูล"
        )

    def test_ตรวจแล้วไม่มีการตัดจริงยังต้องเป็นปกติ(self) -> None:
        """เคสข้าง ๆ ที่ต้องไม่ถูกลากไปด้วย: ข้อมูลครบ ตรวจแล้วไม่มีอะไร = ปกติ."""
        built = _build(rsi=50.0, previous_price=109.0)

        assert built["send"] is False
        assert built["data_ok"] is True
        assert built["cross_checked"] is True
        assert "ไม่มีการตัด MA200" in built["reason"]

    @pytest.mark.parametrize("bad_prev", [None, float("nan")])
    def test_สถานะที่ส่งต่อให้ผู้เรียกแยกจากทั้งสองเคสข้าง_ๆ(self, recorder, bad_prev) -> None:
        """สามสถานะต้องแยกกันได้จริงจากค่าที่ ``send_technical_alert`` คืน."""
        unchecked = notifier.send_technical_alert(
            webhook_url="https://discord.test/hook",
            symbol="VOO", rsi=50.0, price=110.0, ma200=100.0, previous_price=bad_prev,
        )
        normal = notifier.send_technical_alert(
            webhook_url="https://discord.test/hook",
            symbol="VOO", rsi=50.0, price=110.0, ma200=100.0, previous_price=109.0,
        )
        no_data = notifier.send_technical_alert(
            webhook_url="https://discord.test/hook",
            symbol="VOO", rsi=None, price=110.0, ma200=100.0, previous_price=109.0,
        )

        # ตรวจการตัดไม่ได้ = ส่งไม่ได้ก็จริง แต่ไม่ใช่ "ส่งไม่สำเร็จ" (เน็ต/webhook ล่ม)
        assert _status_triple(unchecked) == (True, True, False)
        assert _status_triple(normal) == (True, True, True)
        assert _status_triple(no_data) == (False, True, False)
        assert len({_status_triple(unchecked), _status_triple(normal), _status_triple(no_data)}) == 3

        assert recorder.calls == [], "ไม่มีเคสไหนควรยิง Discord จริง"
