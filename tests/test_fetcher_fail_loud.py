# -*- coding: utf-8 -*-
"""ตาข่ายของบรรทัดที่ CLAUDE.md ระบุชื่อไว้เอง — ``data/fetcher.py`` ต้อง raise ไม่ใช่คืนเฟรมว่าง.

ที่มา (AUDIT_ROUND2_2026-08-07 · HIGH "บรรทัด raise PriceDataUnavailableError ที่ CLAUDE.md
ระบุชื่อไว้ ไม่มีเทสต์คุ้มกันเลย"): มิวแทนต์ที่เปลี่ยน

    raise PriceDataUnavailableError(...)        ->  return pd.DataFrame()

รอดชีวิตจากชุดเทสต์ทั้ง 1296 ตัว ทั้งที่ CLAUDE.md เขียนห้ามไว้ตรงตัวว่า
"it does not return an empty frame"  สาเหตุ: ทุกเทสต์ที่เอ่ยชื่อ ``fetch_adjusted_close_data``
ใช้ ``monkeypatch.setattr`` ทับตัวจริงทิ้งหมด ไม่มีใครเรียกของจริงเลยสักตัว

ไฟล์นี้จึงเรียก **ตัวจริง** โดย stub เฉพาะ ``yf.download`` (ขอบนอกสุดที่ยิงเน็ต) และ
``time.sleep`` — ไม่ยิง network ไม่แตะไฟล์ของผู้ใช้

ทำไมกฎนี้ถึงสำคัญพอจะมีเทสต์เฉพาะ: ``fetch_adjusted_close_data`` เป็นทางเข้าราคาของ
main.py, goal_service, pdf_export, ai_advisor, etf_service, market_analysis_service,
portfolio/backtest, technical/indicators และ dashboard  ถ้ามันเงียบเป็นเฟรมว่าง
downstream จะแปลงเป็นสัญญาณ/ราคา/กำไรปลอมพร้อมกันทั้งระบบโดยผู้ใช้ไม่รู้ตัว
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import pytest

from data import fetcher
from data.fetcher import PriceDataUnavailableError, fetch_adjusted_close_data


class _DownloadSpy:
    """แทน ``yf.download`` — นับจำนวนครั้งที่ถูกเรียก แล้วคืน/โยนตามที่กำหนด."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def no_sleep(monkeypatch):
    """หน่วงเวลาระหว่าง retry ต้องไม่ทำให้ชุดเทสต์ช้า — และเก็บจำนวนครั้งไว้ตรวจด้วย."""
    slept: list[float] = []
    monkeypatch.setattr(fetcher.time, "sleep", lambda sec: slept.append(sec))
    return slept


def _good_frame(ticker: str = "VOO") -> pd.DataFrame:
    """เฟรมแบบที่ yfinance คืนสำหรับ ticker เดียว (คอลัมน์ธรรมดา ไม่ใช่ MultiIndex)."""
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    return pd.DataFrame({"Adj Close": [100.0, 101.0, 102.0, 103.0, 104.0]}, index=idx)


class TestFetcherRaisesInsteadOfReturningEmptyFrame:
    """กฎข้อ 1 ของโปรเจกต์: ดึงไม่สำเร็จ = เสียงดัง ห้ามกลายเป็นเฟรมว่างเงียบ ๆ."""

    def test_raises_price_data_unavailable_when_every_attempt_throws(
        self, monkeypatch, no_sleep
    ):
        """มิวแทนต์ ``return pd.DataFrame()`` ตายตรงนี้ — ต้อง raise เท่านั้น."""
        spy = _DownloadSpy([RuntimeError("yahoo ล่ม")])
        monkeypatch.setattr(fetcher.yf, "download", spy)

        with pytest.raises(PriceDataUnavailableError):
            fetch_adjusted_close_data(["VOO"], years=1)

    def test_does_not_return_anything_at_all_on_failure(self, monkeypatch, no_sleep):
        """ดักมิวแทนต์ทุกแบบที่ 'คืนค่า' แทนที่จะ raise — ว่างหรือไม่ว่างก็ผิดเหมือนกัน.

        เทสต์ตัวบนใช้ ``pytest.raises`` ซึ่งอ่านเป็น 'ต้องโยน' อยู่แล้ว ตัวนี้เขียนซ้ำใน
        มุมกลับเพื่อให้ข้อความตอนแดงบอกตรง ๆ ว่าโค้ด "คืนค่าอะไรกลับมา" ซึ่งคือรูปร่างของ
        การถดถอยที่เคยเกิดจริง
        """
        spy = _DownloadSpy([RuntimeError("yahoo ล่ม")])
        monkeypatch.setattr(fetcher.yf, "download", spy)

        returned = "ยังไม่ได้เรียก"
        try:
            returned = fetch_adjusted_close_data(["VOO"], years=1)
        except PriceDataUnavailableError:
            returned = "raise ตามสัญญา"

        assert returned == "raise ตามสัญญา", (
            "fetch_adjusted_close_data คืนค่าแทนที่จะ raise PriceDataUnavailableError "
            f"(ได้: {type(returned).__name__} ว่าง={getattr(returned, 'empty', 'n/a')}) — "
            "CLAUDE.md ระบุไว้ตรงตัวว่า 'it does not return an empty frame'"
        )

    def test_retries_three_times_before_giving_up(self, monkeypatch, no_sleep):
        """สัญญาที่ CLAUDE.md เขียนไว้: 3 retries แล้วค่อย raise — ไม่ใช่ยอมแพ้ครั้งแรก."""
        spy = _DownloadSpy([RuntimeError("timeout")])
        monkeypatch.setattr(fetcher.yf, "download", spy)

        with pytest.raises(PriceDataUnavailableError):
            fetch_adjusted_close_data(["VOO"], years=1)

        assert spy.calls == 3, f"ต้องลองครบ 3 ครั้งก่อนยอมแพ้ (ลองจริง {spy.calls})"
        assert len(no_sleep) == spy.calls - 1, (
            "ต้องหน่วงเวลาระหว่างครั้งเท่านั้น ไม่ใช่หน่วงหลังครั้งสุดท้ายแล้วค่อย raise "
            f"(sleep {len(no_sleep)} ครั้ง จาก {spy.calls} ครั้งที่ลอง)"
        )

    def test_error_message_names_the_tickers_and_the_underlying_reason(
        self, monkeypatch, no_sleep
    ):
        """เสียงดังต้องอ่านรู้เรื่อง: บอกว่า ticker ไหน และพังเพราะอะไร."""
        spy = _DownloadSpy([RuntimeError("HTTP 429 rate limited")])
        monkeypatch.setattr(fetcher.yf, "download", spy)

        with pytest.raises(PriceDataUnavailableError) as excinfo:
            fetch_adjusted_close_data(["VOO", "SCHD"], years=1)

        message = str(excinfo.value)
        assert "VOO" in message and "SCHD" in message
        assert "HTTP 429 rate limited" in message, (
            "ข้อความต้องพ่วงสาเหตุจริงมาด้วย ไม่ใช่ 'ดึงข้อมูลไม่สำเร็จ' ลอย ๆ"
        )
        assert isinstance(excinfo.value.__cause__, RuntimeError), (
            "ต้อง ``raise ... from last_error`` เพื่อให้ traceback เห็นต้นเหตุ"
        )

    def test_empty_frame_from_yfinance_is_a_failure_not_a_result(
        self, monkeypatch, no_sleep
    ):
        """yfinance คืนเฟรมว่าง = ดึงไม่สำเร็จ ไม่ใช่ 'ไม่มีข้อมูลราคา'."""
        spy = _DownloadSpy([pd.DataFrame()])
        monkeypatch.setattr(fetcher.yf, "download", spy)

        with pytest.raises(PriceDataUnavailableError):
            fetch_adjusted_close_data(["VOO"], years=1)
        assert spy.calls == 3

    def test_missing_adj_close_column_is_a_failure(self, monkeypatch, no_sleep):
        """คอลัมน์หาย = สัญญาข้อมูลเปลี่ยน ต้องดัง ห้ามคืนเฟรมว่าง."""
        idx = pd.date_range("2024-01-01", periods=3, freq="D")
        spy = _DownloadSpy([pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)])
        monkeypatch.setattr(fetcher.yf, "download", spy)

        with pytest.raises(PriceDataUnavailableError):
            fetch_adjusted_close_data(["VOO"], years=1)

    def test_all_nan_prices_are_a_failure_not_zeroes(self, monkeypatch, no_sleep):
        """ราคาว่างทั้งคอลัมน์ต้อง raise — ห้ามหลุดออกไปเป็นเฟรมว่าง/ศูนย์ให้ downstream."""
        idx = pd.date_range("2024-01-01", periods=3, freq="D")
        spy = _DownloadSpy(
            [pd.DataFrame({"Adj Close": [float("nan")] * 3}, index=idx)]
        )
        monkeypatch.setattr(fetcher.yf, "download", spy)

        with pytest.raises(PriceDataUnavailableError):
            fetch_adjusted_close_data(["VOO"], years=1)

    def test_error_type_stays_a_runtimeerror_subclass(self):
        """ผู้เรียกหลายที่ดักด้วย ``RuntimeError`` — เปลี่ยนฐานคลาสคือ silent breakage."""
        assert issubclass(PriceDataUnavailableError, RuntimeError)


class TestFetcherStillSucceedsWhenDataArrives:
    """ตาข่ายคู่: fail-loud ต้องไม่กลายเป็น 'พังเสมอ' — เส้นทางสำเร็จต้องยังทำงาน."""

    def test_returns_prices_when_download_works(self, monkeypatch, no_sleep):
        spy = _DownloadSpy([_good_frame()])
        monkeypatch.setattr(fetcher.yf, "download", spy)

        frame = fetch_adjusted_close_data(["VOO"], years=1)

        assert spy.calls == 1
        assert list(frame.columns) == ["VOO"]
        assert frame["VOO"].tolist() == [100.0, 101.0, 102.0, 103.0, 104.0]

    def test_recovers_on_a_later_attempt_without_raising(self, monkeypatch, no_sleep):
        """ล้มเหลว 2 ครั้งแล้วสำเร็จครั้งที่ 3 = ต้องคืนข้อมูล ไม่ใช่ raise."""
        spy = _DownloadSpy(
            [RuntimeError("timeout"), RuntimeError("timeout"), _good_frame()]
        )
        monkeypatch.setattr(fetcher.yf, "download", spy)

        frame = fetch_adjusted_close_data(["VOO"], years=1)

        assert spy.calls == 3
        assert not frame.empty
