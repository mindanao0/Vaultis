# -*- coding: utf-8 -*-
"""ตาข่ายของรายงาน PDF รายเดือน (`utils/pdf_export.py`) — AUDIT_2026-08-06 ข้อ A4.

ครอบ 4 ข้อในไฟล์เดียว:

* **H5** `_safe_float(..., default=0.0)` แปลง NaN เป็น ``0.00`` ทุกช่อง → ผู้ใช้อ่าน
  รายงานย้อนหลังโดยไม่มีบริบทหน้าจอ แล้วเข้าใจว่า "ราคาดึงไม่ได้" คือ "มูลค่า 0 บาท"
* **H6** PDF ลงทะเบียนเฉพาะ Helvetica → อักษรไทยถูกวาดด้วย ZapfDingbats เป็น ■
* **M-PDF-1** ดึงราคาไม่สำเร็จถูกพิมพ์เป็น "No return data available." (= ไม่มีข้อมูลจริง)
* **M-PDF-2** `get_monthly_advice` โยน → PDF พิมพ์ "model suggests holding cash"
  ทั้งที่โมเดลไม่เคยประเมินอะไรเลย

AUDIT_ROUND2_2026-08-07 เพิ่มอีกสามข้อในไฟล์เดียวกัน:

* **คำเตือนสมุดบัญชี 2 ใน 3 ชุดหายไป** — PDF อ่านแค่ ``skipped_rows``
  ส่วน ``derived_fx_rows`` (อัตราถูกคำนวณย้อน) กับ ``inconsistent_rows``
  (ยอดเงินขัดกับ จำนวนหุ้น × ราคา × อัตรา) ไม่เคยถูกพิมพ์ ⇒ เอกสารที่เก็บไว้อ่าน
  ย้อนหลังเสนอยอดที่มีแถวน่าสงสัยปนอยู่ว่าเป็นตัวเลขสะอาด
* **ตัวเลขบาทไม่มีป้ายที่มาของอัตรา** — ``fx_is_live=False`` (ค่าสำรองจาก config)
  ให้ PDF ที่เหมือนกันทุกตัวอักษรกับอัตราสด
* **อีโมจิกลายเป็นกล่องสี่เหลี่ยม (tofu)** — Garuda/Helvetica ไม่มีกลิฟอีโมจิ

เทสต์ทั้งไฟล์ไม่แตะเน็ต ไม่แตะฐานข้อมูล ไม่เรียก LLM — ทุกทางออกถูก stub ที่ระดับโมดูล
"""

from __future__ import annotations

import base64
import fnmatch
import re
import zlib
from pathlib import Path

import pandas as pd
import pytest

import utils.pdf_export as pe
from data.fetcher import PriceDataUnavailableError

THAI_RANGE = range(0x0E00, 0x0E80)


# --------------------------------------------------------------------------- #
# ตัวช่วย
# --------------------------------------------------------------------------- #
def _holdings_df() -> pd.DataFrame:
    """GLDM ดึงราคาไม่ได้ (tracker คืน NaN อย่างถูกต้อง) · VOO ปกติ."""
    nan = float("nan")
    return pd.DataFrame(
        [
            {
                "Ticker": "GLDM",
                "Shares": 100.0,
                "Avg Cost (USD)": 50.0,
                "Current Price (USD)": nan,
                "P&L (THB)": nan,
                "Return (%)": nan,
                "Price OK": False,
            },
            {
                "Ticker": "VOO",
                "Shares": 10.0,
                "Avg Cost (USD)": 400.0,
                "Current Price (USD)": 500.0,
                "P&L (THB)": 30000.0,
                "Return (%)": 25.0,
                "Price OK": True,
            },
        ]
    )


def _prices_df() -> pd.DataFrame:
    """VOO มีราคาจริง · XLV เป็น NaN ล้วน (fetcher เก็บคอลัมน์ที่ล้มเหลวไว้จริง)."""
    idx = pd.bdate_range("2024-01-02", periods=300)
    return pd.DataFrame(
        {"VOO": [400.0 + i * 0.5 for i in range(300)], "XLV": [float("nan")] * 300},
        index=idx,
    )


def _advice() -> dict:
    """รูปร่างเดียวกับที่ ``get_monthly_advice`` คืนจริง (รวม ``etf_scores``)."""
    return {
        "advice_text": "กำไรเดือนนี้ 36800 บาท — ทยอยซื้อตามแผน",
        "allocation": {"VOO": {"amount_thb": 3000, "percent": 60, "group": "core"}},
        "unallocated_thb": 0.0,
        "no_data_tickers": [],
        "etf_scores": [
            {"ticker": "VOO", "data_ok": True, "total_pct": 72.0},
            {"ticker": "XLV", "data_ok": True, "total_pct": 55.0},
        ],
    }


_MISSING = object()


class _HoldingsStub:
    """แทน ``tracker.get_portfolio_summary`` — คืน **วัตถุเดิมทุกครั้ง** และนับการเรียก.

    คืนวัตถุเดิมเพื่อให้เทียบด้วย ``is`` ได้ว่า snapshot ที่ตาราง Holdings ใช้
    เป็นตัวเดียวกับที่ถูกส่งต่อไปให้ ``get_total_summary()`` (AUDIT_ROUND2_2026-08-07)
    """

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = 0

    def __call__(self) -> pd.DataFrame:
        self.calls += 1
        return self.frame


class _TotalSummaryStub:
    """แทน ``tracker.get_total_summary`` — **บังคับ** ให้ผู้เรียกส่ง snapshot เข้ามา.

    สตับตัวเดิมเป็น ``lambda:`` ที่ไม่รับอาร์กิวเมนต์เลย จึงตรึง**บั๊ก**ไว้แทนที่จะ
    ตรึงกฎ: ถ้าใครแก้ ``pdf_export`` ให้ส่ง snapshot ต่อ (ซึ่งเป็นสิ่งที่ถูก) เทสต์จะ
    ระเบิดเป็น ``TypeError`` และถ้าใครแก้กลับไปดึงราคาเองรอบสอง เทสต์กลับผ่านหมด
    (AUDIT_ROUND2_2026-08-07) — ตอนนี้กลับด้าน: ไม่ส่ง snapshot = ล้มพร้อมเหตุผลไทย
    """

    def __init__(self, payload: dict, expected: pd.DataFrame | None = None) -> None:
        self.payload = payload
        self.expected = expected
        self.calls = 0
        self.received: list[object] = []

    def __call__(self, holdings=_MISSING) -> dict:
        self.calls += 1
        if holdings is _MISSING:
            raise AssertionError(
                "get_total_summary() ถูกเรียกโดยไม่ส่ง snapshot ที่คำนวณไว้แล้ว — "
                "ของจริงจะไปดึงราคา+FX เองอีกรอบ ทำให้ตาราง Holdings กับยอดรวมบน "
                "หน้าเดียวกันมาจากคนละ snapshot (AUDIT_ROUND2_2026-08-07)"
            )
        self.received.append(holdings)
        if self.expected is not None:
            assert holdings is self.expected, (
                "ยอดรวมถูกคิดจาก DataFrame คนละตัวกับที่พิมพ์ในตาราง Holdings"
            )
        return self.payload


def _default_total_summary() -> dict:
    """ยอดรวมดีฟอลต์ของ ``_render``: ดึงราคาไม่ได้เลยสักกอง.

    ฐาน priced = 0 แต่เงินที่จ่ายไปจริง = 200,000
    (ตรงกับที่ ``tracker.get_total_summary`` คืนจริงหลังแก้ H9)
    """
    nan = float("nan")
    return {
        "invested_thb_all": 200000.0,
        "invested_thb_priced": 0.0,
        "total_invested_thb": 200000.0,
        "current_value_thb": nan,
        "total_pnl_thb": nan,
        "total_return_pct": nan,
        "missing_prices": ["GLDM"],
        "skipped_rows": [],
    }


def _record_drawn(monkeypatch: pytest.MonkeyPatch) -> tuple[list[list[list[str]]], list[str]]:
    """ดักตาราง/ย่อหน้าที่ถูกวาดลง PDF จริง — คืน ``(tables, paras)`` ที่ถูกเติมระหว่าง build."""
    tables: list[list[list[str]]] = []
    paras: list[str] = []
    real_build = pe._build_table
    real_para = pe.Paragraph

    def rec_build(data, *args, **kwargs):
        tables.append([[str(cell) for cell in row] for row in data])
        return real_build(data, *args, **kwargs)

    def rec_para(text, *args, **kwargs):
        paras.append(str(text))
        return real_para(text, *args, **kwargs)

    monkeypatch.setattr(pe, "_build_table", rec_build)
    monkeypatch.setattr(pe, "Paragraph", rec_para)
    return tables, paras


def _render(
    monkeypatch: pytest.MonkeyPatch,
    *,
    total_summary: dict | None = None,
    holdings: pd.DataFrame | None = None,
    prices=None,
    advice=None,
    tickers: list[str] | None = None,
    include_ai: bool = False,
    month: str = "2026-08",
    recorder: dict | None = None,
) -> tuple[list[list[list[str]]], list[str], bytes]:
    """สร้าง PDF พร้อมดักตาราง/ย่อหน้าที่ถูกวาดลงไปจริง.

    ``prices``/``advice`` ที่เป็น callable จะถูกใช้เป็นฟังก์ชันแทน (ให้โยนได้)
    ``recorder`` (dict ที่ผู้เรียกส่งมา) จะถูกเติมสตับที่นับการเรียก เพื่อให้เทสต์
    ตรวจได้ว่ารายงานหนึ่งฉบับดึง snapshot พอร์ตกี่ครั้ง (AUDIT_ROUND2_2026-08-07)
    """
    tables, paras = _record_drawn(monkeypatch)

    holdings_stub = _HoldingsStub(_holdings_df() if holdings is None else holdings)
    totals_stub = _TotalSummaryStub(
        total_summary if total_summary is not None else _default_total_summary(),
        expected=holdings_stub.frame,
    )
    if recorder is not None:
        recorder["holdings"] = holdings_stub
        recorder["totals"] = totals_stub
    monkeypatch.setattr(pe, "get_portfolio_summary", holdings_stub)
    monkeypatch.setattr(pe, "get_total_summary", totals_stub)
    monkeypatch.setattr(pe, "get_tickers", lambda: tickers or ["VOO", "XLV"])

    price_source = prices if prices is not None else _prices_df()
    if callable(price_source):
        monkeypatch.setattr(pe, "fetch_adjusted_close_data", price_source)
    else:
        monkeypatch.setattr(
            pe, "fetch_adjusted_close_data", lambda tickers, years=10: price_source
        )

    advice_source = advice if advice is not None else _advice()
    if callable(advice_source):
        monkeypatch.setattr(pe, "get_monthly_advice", advice_source)
    else:
        monkeypatch.setattr(pe, "get_monthly_advice", lambda **kwargs: advice_source)

    pdf = pe.generate_monthly_report(month=month, budget_thb=5000, include_ai=include_ai)
    return tables, paras, pdf


def _row(tables: list[list[list[str]]], first_cell: str) -> list[str]:
    for table in tables:
        for row in table:
            if row and row[0] == first_cell:
                return row
    raise AssertionError(f"ไม่พบแถวที่ขึ้นต้นด้วย {first_cell!r} ในตารางที่วาดลง PDF")


def _summary_rows(tables: list[list[list[str]]]) -> dict[str, str]:
    """แถวของตารางสรุปหน้า 1 — ``{ป้าย: ค่าที่พิมพ์}`` ตามที่ถูกวาดลงกระดาษจริง."""
    for table in tables:
        if table and table[0][:1] == ["Metric"]:
            return {row[0]: row[1] for row in table[1:] if len(row) >= 2}
    raise AssertionError(f"ไม่พบตารางสรุปหน้า 1 ในตารางที่วาดลง PDF: {tables}")


def _money(cell: str) -> float:
    return float(cell.replace(",", "").replace("%", ""))


def _squashed(pdf: bytes) -> str:
    """ข้อความบนกระดาษแบบตัดช่องว่างทิ้ง.

    reportlab หั่นย่อหน้าเป็นหลาย ``Tj`` ตามการขึ้นบรรทัด/อักขระพิเศษ (``<`` ถูกวาด
    แยกคำ) การเทียบสตริงตรง ๆ จึงพลาดทั้งที่ข้อความไปถึงกระดาษครบ — และ PDF ยัง
    escape ``(`` / ``)`` ด้วย ``\\`` ในสตรีมด้วย
    """
    return re.sub(r"\s+", "", _drawn_text(pdf)).replace("\\", "")


def _embedded_fonts(pdf: bytes) -> set[str]:
    return {m.decode() for m in re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-]+)", pdf)}


def _drawn_text(pdf: bytes) -> str:
    """ข้อความที่ **ถูกวาดลงหน้ากระดาษจริง** (คนละอย่างกับที่ส่งเข้า ``Paragraph``).

    reportlab แจงย่อหน้าเป็น mini-XML ก่อนวาด — สิ่งที่ส่งเข้าไปกับสิ่งที่ออกมา
    อาจไม่เท่ากัน การดักที่ ``Paragraph`` จึงมองไม่เห็นข้อความที่หายระหว่างทาง
    """
    parts: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        chunk = match.group(1)
        # reportlab เขียนสตรีมเป็น ASCII85 + Flate (หรือดิบ ถ้าปิด pageCompression)
        try:
            chunk = base64.a85decode(chunk.strip(), adobe=True)
        except ValueError:
            pass
        try:
            # decompressobj: ยอมให้มีไบต์ท้ายสตรีม (\r\n ก่อน endstream) โดยไม่ error
            chunk = zlib.decompressobj().decompress(chunk)
        except zlib.error:
            pass
        parts.extend(
            m.group(1).decode("latin-1")
            for m in re.finditer(rb"\(((?:[^()\\]|\\.)*)\)\s*Tj", chunk)
        )
    return " ".join(parts)


@pytest.fixture(autouse=True)
def _no_system_thai_font(monkeypatch: pytest.MonkeyPatch):
    """ดีฟอลต์: ทำเหมือนเครื่องไม่มีฟอนต์ไทย (ตรงกับ image ของโปรเจกต์).

    ถ้าไม่กด host ที่มีฟอนต์ไทยติดมาด้วย (เช่น Fedora) จะได้ผลคนละแบบกับใน Docker
    """
    monkeypatch.setattr(pe, "_thai_font_files", lambda: [], raising=False)
    getattr(pe, "_reset_thai_font_cache", lambda: None)()
    yield
    getattr(pe, "_reset_thai_font_cache", lambda: None)()


@pytest.fixture
def synthetic_thai_font(tmp_path: Path) -> Path:
    """สร้างฟอนต์ที่ครอบอักษรไทยจาก DejaVuSans (ไม่ต้องต่อเน็ต ไม่ต้องฝัง .ttf ใน repo)."""
    pytest.importorskip("fontTools")
    import matplotlib
    from fontTools.ttLib import TTFont as FTFont

    src = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
    font = FTFont(str(src))
    for table in font["cmap"].tables:
        if table.isUnicode():
            for codepoint in range(0x0E01, 0x0E5C):
                table.cmap[codepoint] = "a"
    # ต้องเปลี่ยนชื่อในตาราง name ด้วย — reportlab เขียน `/BaseFont` ใน PDF จากชื่อ
    # ภายในฟอนต์ (nameID 6 = PostScript name) ไม่ใช่ชื่อที่ registerFont ตั้งให้
    # ไม่เปลี่ยน = ได้ `AAAAAA+DejaVuSans` แล้วแยกไม่ออกว่าฝังฟอนต์ตัวไหนไป
    for record in font["name"].names:
        if record.nameID in (1, 3, 4, 6, 16, 18):
            record.string = "SynthThai"
    out = tmp_path / "SynthThai.ttf"
    font.save(str(out))
    return out


# --------------------------------------------------------------------------- #
# H5 — NaN ต้องเป็น N/A ไม่ใช่ 0.00
# --------------------------------------------------------------------------- #
def test_holdings_nan_ไม่กลายเป็นศูนย์(monkeypatch: pytest.MonkeyPatch):
    tables, _, _ = _render(monkeypatch)
    gldm = _row(tables, "GLDM")
    assert gldm[3:] == ["N/A", "N/A", "N/A"], (
        "ราคาปัจจุบัน/P&L/ผลตอบแทนที่ดึงไม่ได้ต้องเป็น N/A — 0.00 คือตัวเลขที่อ่านเป็นเงินได้"
    )
    # แถวที่มีข้อมูลจริงต้องไม่เพี้ยน
    assert _row(tables, "VOO")[:6] == ["VOO", "10.0000", "400.00", "500.00", "30,000.00", "25.00%"]


def test_สรุปหน้าแรก_nan_ไม่กลายเป็นศูนย์(monkeypatch: pytest.MonkeyPatch):
    tables, _, _ = _render(monkeypatch)
    rows = _summary_rows(tables)
    invested = {label: value for label, value in rows.items() if "Invested" in label}
    assert "200,000.00" in invested.values(), f"เงินลงทุนที่จ่ายไปจริงหายจากตาราง: {rows}"
    unknown = [
        value
        for label, value in rows.items()
        if "Current Value" in label or "Profit" in label or "Return" in label
    ]
    assert unknown == ["N/A", "N/A", "N/A"], f"ค่าที่ไม่รู้ถูกพิมพ์เป็นตัวเลข: {rows}"


def test_ศูนย์จริงยังเป็นศูนย์(monkeypatch: pytest.MonkeyPatch):
    """กันแก้เกิน — 0.0 ที่เป็นคำตอบจริงต้องไม่ถูกเปลี่ยนเป็น N/A."""
    tables, _, _ = _render(
        monkeypatch,
        total_summary={
            "invested_thb_all": 0.0,
            "invested_thb_priced": 0.0,
            "total_invested_thb": 0.0,
            "current_value_thb": 0.0,
            "total_pnl_thb": 0.0,
            "total_return_pct": 0.0,
            "missing_prices": [],
            "skipped_rows": [],
        },
    )
    rows = _summary_rows(tables)
    pnl = [value for label, value in rows.items() if "Profit" in label]
    ret = [value for label, value in rows.items() if "Return" in label]
    assert pnl == ["0.00"], rows
    assert ret == ["0.00%"], rows


# --------------------------------------------------------------------------- #
# H9 — เงินลงทุนมีสองฐาน ตารางหน้า 1 ต้องบวกลบกันลงตัว
# --------------------------------------------------------------------------- #
def _totals_two_bases() -> dict:
    """GLDM ดึงราคาไม่ได้ → ฐาน priced (120,000) เล็กกว่าเงินที่จ่ายจริง (300,000)."""
    return {
        "invested_thb_all": 300000.0,
        "invested_thb_priced": 120000.0,
        "total_invested_thb": 300000.0,
        "current_value_thb": 150000.0,
        "total_pnl_thb": 30000.0,
        "total_return_pct": 25.0,
        "missing_prices": ["GLDM"],
        "skipped_rows": [],
    }


def test_ตารางหน้าแรกบวกลบลงตัว(monkeypatch: pytest.MonkeyPatch):
    """มูลค่าปัจจุบัน − เงินลงทุน (ที่พิมพ์อยู่ในตารางเดียวกัน) ต้องเท่ากับกำไร/ขาดทุน.

    เดิมพิมพ์ ``Total Invested`` = ฐานที่รวมกองที่ไม่มีราคา คู่กับกำไรที่คิดจากฐาน
    เฉพาะกองที่มีราคา ผู้ใช้ที่อ่านรายงานย้อนหลังจึงประกอบเลขกลับเองไม่ได้ (H9)
    """
    tables, _, _ = _render(monkeypatch, total_summary=_totals_two_bases())
    rows = _summary_rows(tables)

    invested_rows = {label: value for label, value in rows.items() if "Invested" in label}
    assert len(invested_rows) == 2, f"ต้องพิมพ์เงินลงทุนทั้งสองฐาน ไม่ใช่ฐานเดียว: {rows}"
    assert sorted(_money(v) for v in invested_rows.values()) == [120000.0, 300000.0]

    current = _money(next(v for label, v in rows.items() if "Current Value" in label))
    pnl = _money(next(v for label, v in rows.items() if "Profit" in label))
    base_labels = [label for label in invested_rows if _money(invested_rows[label]) == current - pnl]
    assert base_labels, (
        f"ไม่มีแถวเงินลงทุนแถวไหนเป็นฐานของกำไร/ขาดทุนเลย — {current:,.2f} − {pnl:,.2f} "
        f"= {current - pnl:,.2f} ไม่ตรงกับช่องไหนในตาราง: {rows}"
    )
    # ป้ายต้องแยกสองฐานออกจากกันได้ด้วยตัวเอง (รายงานถูกอ่านย้อนหลังโดยไม่มีหน้าจอ)
    labels = " ".join(invested_rows).lower()
    assert "all" in labels and "priced" in labels, (
        f"ป้ายเงินลงทุนไม่ได้บอกว่าแต่ละแถวเป็นฐานไหน: {list(invested_rows)}"
    )


def test_สองฐานต่างกันต้องมีคำอธิบายบนกระดาษ(monkeypatch: pytest.MonkeyPatch):
    _, _, pdf = _render(monkeypatch, total_summary=_totals_two_bases())
    drawn = _squashed(pdf)
    assert "300,000.00" in drawn and "120,000.00" in drawn, (
        "ตัวเลขฐานใดฐานหนึ่งไม่ได้ถูกวาดลงกระดาษจริง"
    )
    assert "180,000.00" in drawn, (
        "ไม่ได้บอกว่าสองฐานต่างกันเท่าไร (= เงินในกองที่ดึงราคาไม่ได้ ไม่ใช่ขาดทุน)"
    )


def test_สองฐานเท่ากันไม่ต้องอธิบาย(monkeypatch: pytest.MonkeyPatch):
    """กันแก้เกิน — ราคาครบทุกกองแล้วต้องไม่มีคำเตือนเรื่องฐานมาปนให้สับสน."""
    totals = dict(
        _totals_two_bases(),
        invested_thb_all=120000.0,
        total_invested_thb=120000.0,
        missing_prices=[],
    )
    _, paras, _ = _render(monkeypatch, total_summary=totals)
    assert not [p for p in paras if "different bases" in p.lower()], paras


def test_ฐานที่หายไปต้องบอกว่าประกอบเลขกลับไม่ได้(monkeypatch: pytest.MonkeyPatch):
    """ฐาน priced ที่ "ไม่รู้" ต้องไม่ปล่อยให้ตารางดูเหมือนบวกลบลงตัว."""
    totals = dict(_totals_two_bases())
    totals.pop("invested_thb_priced")
    tables, paras, _ = _render(monkeypatch, total_summary=totals)
    rows = _summary_rows(tables)
    assert [v for label, v in rows.items() if "priced" in label.lower() and "Invested" in label] == [
        "N/A"
    ], f"ฐานที่ไม่รู้ถูกพิมพ์เป็นตัวเลข: {rows}"
    assert [p for p in paras if "cannot be reconciled" in p], (
        f"ตารางขาดฐานไปหนึ่งตัวแต่ไม่มีคำเตือนสักบรรทัด — ย่อหน้า: {paras}"
    )


def test_อ่านคีย์สองฐานชุดเดียวกับ_tracker_ตัวจริง(monkeypatch: pytest.MonkeyPatch):
    """ชื่อคีย์ต้องมาจากผู้ผลิตจริง ไม่ใช่ชื่อที่ fixture ในไฟล์นี้แต่งขึ้นเอง.

    ถ้า ``tracker.get_total_summary()`` เปลี่ยนชื่อคีย์เมื่อไร ช่องในรายงานจะกลายเป็น
    ``N/A`` เงียบ ๆ (ซึ่งอ่านได้ว่า "ไม่รู้ยอด") — เทสต์นี้ผูกสองฝั่งไว้ด้วยกัน
    """
    import portfolio.tracker as tracker

    # สมุดว่าง = ไม่แตะ CSV จริง ไม่แตะเน็ต และ 0 คือคำตอบจริง (ไม่ใช่ "ไม่รู้")
    monkeypatch.setattr(tracker, "get_portfolio_summary", lambda: pd.DataFrame())
    totals = tracker.get_total_summary()

    tables, _, _ = _render(monkeypatch, total_summary=totals, holdings=pd.DataFrame())
    rows = _summary_rows(tables)
    invested = {label: value for label, value in rows.items() if "Invested" in label}
    assert len(invested) == 2, f"ต้องพิมพ์เงินลงทุนทั้งสองฐาน: {rows}"
    assert set(invested.values()) == {"0.00"}, (
        f"อ่านคีย์ไม่ตรงกับ tracker — ช่องเงินลงทุนกลายเป็น N/A ทั้งที่สมุดว่างคือ 0 จริง: {rows}"
    )


def test_คอลัมน์ที่ไม่มีราคาเลยต้องเป็น_NA_และมีคำเตือนใต้ตาราง(monkeypatch: pytest.MonkeyPatch):
    tables, paras, _ = _render(monkeypatch)

    returns_rows = [row for table in tables for row in table if row and row[0] == "1M"]
    assert returns_rows, "ไม่พบตาราง Return Analysis"
    assert "0.00%" not in returns_rows[0][2:], "คอลัมน์ที่เป็น NaN ล้วนถูกพิมพ์เป็น 0.00%"
    assert returns_rows[0][2] == "N/A"

    xlv_risk = _row(tables, "XLV")
    assert xlv_risk[1:4] == ["N/A", "N/A", "N/A"], "Risk Metrics ของ ticker ที่ไม่มีราคาเป็น 0.0000"

    warned = [p for p in paras if "XLV" in p and "no price data" in p.lower()]
    assert warned, (
        "หน้า 2 ไม่มีคำเตือนสักบรรทัดว่า XLV ไม่มีราคาเลย — "
        f"ย่อหน้าที่วาดจริง: {paras}"
    )


def test_ticker_ที่หายไปจากตารางราคาถูกรายงาน(monkeypatch: pytest.MonkeyPatch):
    """ticker ที่ขอไปแล้วไม่มีคอลัมน์กลับมาเลย ก็คือ 'ดึงไม่ได้' เหมือนกัน ห้ามเงียบ."""
    prices = _prices_df()[["VOO"]]
    _, paras, _ = _render(monkeypatch, prices=prices, tickers=["VOO", "XLV", "GLDM"])
    joined = " ".join(paras)
    assert "XLV" in joined and "GLDM" in joined, (
        f"ticker ที่ไม่มีคอลัมน์ราคาเลยหายเงียบจากรายงาน — ย่อหน้า: {paras}"
    )


# --------------------------------------------------------------------------- #
# M-PDF-1 — ดึงราคาไม่สำเร็จ ≠ ไม่มีข้อมูล
# --------------------------------------------------------------------------- #
def test_ดึงราคาไม่สำเร็จต้องไม่ถูกพิมพ์เป็นไม่มีข้อมูล(monkeypatch: pytest.MonkeyPatch):
    def boom(tickers, years=10):
        raise PriceDataUnavailableError("ดึงข้อมูลราคา ['VOO'] ไม่สำเร็จหลังลอง 3 ครั้ง")

    _, paras, _ = _render(monkeypatch, prices=boom)
    assert "No return data available." not in paras, (
        "ความล้มเหลวของการดึงราคาถูกพิมพ์ด้วยข้อความเดียวกับ 'ไม่มีข้อมูลจริง'"
    )
    assert "No risk metrics data available." not in paras
    failed = [p for p in paras if "download failed" in p.lower()]
    assert failed, f"ไม่มีย่อหน้าไหนบอกว่าดึงราคาไม่สำเร็จ — ย่อหน้า: {paras}"
    assert any("PriceDataUnavailableError" in p for p in failed), (
        "ต้องระบุชนิดของความล้มเหลว (ชื่อคลาสเป็น ASCII อ่านได้เสมอแม้ไม่มีฟอนต์ไทย)"
    )


# --------------------------------------------------------------------------- #
# M-PDF-2 — โมเดลล้มเหลว ≠ โมเดลแนะนำให้ถือเงินสด
# --------------------------------------------------------------------------- #
def test_โมเดลล้มเหลวต้องไม่กลายเป็นคำแนะนำถือเงินสด(monkeypatch: pytest.MonkeyPatch):
    def boom(**kwargs):
        raise RuntimeError("สร้างคำแนะนำรายเดือนไม่สำเร็จ: อ่านสมุดบัญชีไม่ได้")

    _, paras, _ = _render(monkeypatch, advice=boom)
    assert not [p for p in paras if "holding cash" in p.lower()], (
        "ความล้มเหลวของ get_monthly_advice ถูกพิมพ์เป็นคำแนะนำการลงทุน"
    )
    failed = [p for p in paras if "could not be produced" in p.lower() or "failed" in p.lower()]
    assert failed, f"ไม่มีย่อหน้าไหนบอกว่าโมเดลล้มเหลว — ย่อหน้า: {paras}"
    assert any("RuntimeError" in p for p in paras)


def test_ไม่มีกองไหนผ่านเกณฑ์จริงยังพิมพ์ข้อความเดิม(monkeypatch: pytest.MonkeyPatch):
    """กันแก้เกิน — allocation ว่างทั้งที่ทุกกองมีข้อมูลครบ คือคำตอบจริงของโมเดล."""
    advice = dict(_advice(), allocation={})
    _, paras, _ = _render(monkeypatch, advice=advice)
    assert any("holding cash" in p.lower() for p in paras)


def test_allocation_ว่างเพราะไม่มีข้อมูลต้องไม่กลายเป็นคำแนะนำถือเงินสด(
    monkeypatch: pytest.MonkeyPatch,
):
    """ดึงราคาไม่ได้ → ``data_ok=False`` ทุกกอง → โมเดลไม่เคยตัดสินอะไรเลย.

    ``calculate_allocation`` คืน ``{}`` ทั้งกรณี "ไม่มีข้อมูลให้คิด" และกรณี
    "คิดแล้วไม่จัดสรร" — รายงานต้องอ่านสถานะที่มี (``no_data_tickers``/``data_ok``)
    แล้วแยกข้อความ ห้ามพิมพ์ว่าโมเดลแนะนำให้ถือเงินสดทั้งที่ไม่ได้แนะนำ
    """
    advice = {
        "advice_text": "AI is off",
        "allocation": {},
        "unallocated_thb": 5000.0,
        "no_data_tickers": ["VOO", "XLV"],
        "etf_scores": [
            {"ticker": "VOO", "data_ok": False},
            {"ticker": "XLV", "data_ok": False},
        ],
    }
    _, paras, pdf = _render(monkeypatch, advice=advice)
    drawn = _squashed(pdf)

    assert "holdingcash" not in drawn.lower(), (
        f"ยังพิมพ์ว่าโมเดลแนะนำให้ถือเงินสดทั้งที่ไม่มีข้อมูลให้ตัดสิน — บนกระดาษ: {drawn[-500:]}"
    )
    assert "NOTarecommendationtoholdcash" in drawn, (
        f"ไม่ได้ปฏิเสธการตีความว่าเป็นคำแนะนำ — บนกระดาษ: {drawn[-500:]}"
    )
    named = [p for p in paras if "VOO" in p and "XLV" in p]
    assert named, f"ไม่ได้บอกว่ากองไหนไม่มีข้อมูล — ย่อหน้า: {paras}"


def test_allocation_ว่างทั้งที่บางกองไม่มีข้อมูลต้องไม่สรุปแทนทั้งแผน(
    monkeypatch: pytest.MonkeyPatch,
):
    """คิดได้บางกอง อีกบางกองดึงราคาไม่ได้ → สรุปว่า "ถือเงินสด" แทนทั้งแผนไม่ได้.

    ครึ่งหนึ่งของจักรวาลไม่เคยถูกให้คะแนน คำว่า "ไม่มีกองไหนผ่านเกณฑ์" จึงพูดได้
    เฉพาะกับครึ่งที่คิดจริง — ต้องบอกทั้งสองฝั่งบนกระดาษ
    """
    advice = {
        "advice_text": "AI is off",
        "allocation": {},
        "unallocated_thb": 5000.0,
        "no_data_tickers": ["XLV"],
        "etf_scores": [
            {"ticker": "VOO", "data_ok": True, "total_pct": 72.0},
            {"ticker": "XLV", "data_ok": False, "total_pct": None},
        ],
    }
    _, paras, pdf = _render(monkeypatch, advice=advice)
    drawn = _squashed(pdf)

    assert "holdingcash" not in drawn.lower(), (
        f"สรุปว่าโมเดลแนะนำถือเงินสดทั้งที่ XLV ไม่เคยถูกให้คะแนน — บนกระดาษ: {drawn[-400:]}"
    )
    assert "NOTarecommendationtoholdcash" in drawn, f"ไม่ได้ปฏิเสธการตีความ: {drawn[-400:]}"
    both = [p for p in paras if "VOO" in p and "XLV" in p]
    assert both, f"ไม่ได้บอกว่ากองไหนถูกคิด กองไหนไม่มีข้อมูล — ย่อหน้า: {paras}"


def test_โมเดลไม่ได้คะแนนสักกองต้องไม่กลายเป็นคำแนะนำถือเงินสด(
    monkeypatch: pytest.MonkeyPatch,
):
    """``etf_scores`` ว่างเปล่า = โมเดลไม่ได้ประเมินอะไรเลย ไม่ใช่ "ไม่มีกองไหนผ่านเกณฑ์"."""
    advice = {
        "advice_text": "AI is off",
        "allocation": {},
        "unallocated_thb": 5000.0,
        "no_data_tickers": [],
        "etf_scores": [],
    }
    _, paras, _ = _render(monkeypatch, advice=advice)
    assert not [p for p in paras if "holding cash" in p.lower() and "NOT a recommendation" not in p], (
        f"allocation ว่างเพราะไม่มีกองให้คิดเลย ถูกพิมพ์เป็นคำแนะนำการลงทุน: {paras}"
    )


# --------------------------------------------------------------------------- #
# H6 — อักษรไทยต้องไม่ถูกวาดเป็นสี่เหลี่ยมดำ
# --------------------------------------------------------------------------- #
def test_ไม่มีฟอนต์ไทยต้องไม่วาดสี่เหลี่ยมดำ(monkeypatch: pytest.MonkeyPatch):
    _, paras, pdf = _render(monkeypatch)

    assert "ZapfDingbats" not in _embedded_fonts(pdf), (
        "reportlab สลับไป ZapfDingbats = วาด ■ หนึ่งตัวต่ออักษรไทยหนึ่งตัว"
    )
    thai_left = [p for p in paras if any(ord(c) in THAI_RANGE for c in p)]
    assert not thai_left, (
        f"ยังส่งอักษรไทยเข้า PDF ทั้งที่ไม่มีฟอนต์ไทย: {thai_left}"
    )
    assert any("Thai" in p and "font" in p.lower() for p in paras), (
        f"ตัดข้อความไทยทิ้งเงียบ ๆ — ต้องบอกผู้ใช้ว่าทำไมถึงหาย: {paras}"
    )


def test_มีฟอนต์ไทยแล้วต้องใช้จริง(
    monkeypatch: pytest.MonkeyPatch, synthetic_thai_font: Path
):
    monkeypatch.setattr(pe, "_thai_font_files", lambda: [str(synthetic_thai_font)])
    pe._reset_thai_font_cache()

    _, paras, pdf = _render(monkeypatch)

    fonts = _embedded_fonts(pdf)
    assert "ZapfDingbats" not in fonts
    assert any("SynthThai" in f for f in fonts), f"ไม่ได้ฝังฟอนต์ไทยที่ลงทะเบียนไว้: {fonts}"
    assert any("กำไร" in p for p in paras), "มีฟอนต์ไทยแล้วยังตัดข้อความไทยทิ้ง"


def test_ฟอนต์ที่ไม่มีอักษรไทยต้องไม่ถูกเลือก(monkeypatch: pytest.MonkeyPatch):
    """DejaVuSans ลงทะเบียนได้แต่ไม่มีอักษรไทย — เลือกไปก็ได้ ■ เหมือนเดิม."""
    import matplotlib

    dejavu = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
    monkeypatch.setattr(pe, "_thai_font_files", lambda: [str(dejavu)])
    pe._reset_thai_font_cache()
    assert pe._thai_font() is None


def test_image_ติดตั้งฟอนต์ไทยไว้ในที่ที่โค้ดไปหา():
    """ตรึงข้อต่อระหว่าง Dockerfile กับ ``_SYSTEM_FONT_GLOBS``.

    ทางหลักของ H6 คือ *พิมพ์ภาษาไทยได้จริง* — การแทนด้วยหมายเหตุอังกฤษเป็นตาข่าย
    ท้ายสุด ไม่ใช่คำตอบ ถ้าสองฝั่งนี้หลุดจากกันเมื่อไร รายงานในคอนเทนเนอร์จะกลับไป
    ไม่มีภาษาไทยเลยแบบเงียบ ๆ (เทสต์อื่นในไฟล์นี้จับไม่ได้เพราะมันสตับ path ทิ้ง)
    """
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    lines = [
        line for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    body = "\n".join(lines)

    families = re.findall(r"\bfonts-tlwg-([a-z]+)\b", body)
    assert families or "fonts-thai-tlwg" in body, (
        "Dockerfile ไม่ได้ติดตั้งฟอนต์ไทย — reportlab จะไม่ error แต่วาด ■ แทนอักษรไทย "
        "หรือ (หลังแก้ H6) ตัดข้อความไทยทิ้งทั้งหน้า"
    )

    installed = f"/usr/share/fonts/truetype/tlwg/{families[0].capitalize() if families else 'Garuda'}.ttf"
    assert any(fnmatch.fnmatch(installed, pattern) for pattern in pe._SYSTEM_FONT_GLOBS), (
        f"Dockerfile ลงฟอนต์ไว้ที่ {installed} แต่ _thai_font_files() ไม่ได้มองหาที่นั่น: "
        f"{pe._SYSTEM_FONT_GLOBS}"
    )


# --------------------------------------------------------------------------- #
# สาเหตุที่พิมพ์ลงรายงานต้องไปถึงกระดาษจริง (ตาข่ายของ M-PDF-1/M-PDF-2 เอง)
# --------------------------------------------------------------------------- #
def test_สาเหตุที่มีอักขระ_xml_ต้องไม่หายจากกระดาษ(monkeypatch: pytest.MonkeyPatch):
    """``<...>`` / ``&`` ในข้อความ error ต้องไม่ถูก reportlab กลืนหายเงียบ ๆ.

    ``Paragraph`` แจงข้อความเป็น mini-XML ข้อความจริงของ requests/yfinance/psycopg
    มีทั้ง ``<Response [429] ...>`` และ URL ที่มี ``&`` — ถ้าไม่ escape สาเหตุจะหาย
    จากกระดาษทั้งที่ตัวแปรมีค่าอยู่ = "ดึงไม่สำเร็จ" กลับไปแยกไม่ออกจาก "ไม่มีข้อมูล"
    ซึ่งคือสิ่งที่ M-PDF-1 ตั้งใจปิด
    """

    def boom(tickers, years=10):
        raise PriceDataUnavailableError(
            "ticker ['VOO'] <Response [429] Too Many Requests> "
            "url=/v8/finance/chart/VOO?period1=1&period2=2"
        )

    _, _, pdf = _render(monkeypatch, prices=boom)
    drawn = _drawn_text(pdf)
    assert "429" in drawn, f"รหัสความล้มเหลวหายจากกระดาษ — ข้อความที่วาดจริง: {drawn[:400]}"
    assert "period2" in drawn, f"สาเหตุถูกตัดกลางประโยค — ข้อความที่วาดจริง: {drawn[:400]}"


def test_สาเหตุที่มีอักขระ_xml_ต้องไม่ทำให้สร้างรายงานไม่ได้(monkeypatch: pytest.MonkeyPatch):
    """ข้อความ error ที่บังเอิญคล้ายแท็กต้องไม่ทำให้ผู้ใช้ไม่ได้รายงานทั้งฉบับ."""

    def boom(**kwargs):
        raise RuntimeError("expected <b 3 rows, got 0")

    _, paras, pdf = _render(monkeypatch, advice=boom)
    assert pdf, "สร้าง PDF ไม่สำเร็จเพราะข้อความ error มีอักขระ XML"
    assert not [p for p in paras if "holding cash" in p.lower()]


def test_ข้อมูลจากสมุดบัญชีที่มีอักขระ_xml_ต้องไปถึงกระดาษ(monkeypatch: pytest.MonkeyPatch):
    """ไม่ได้มีแค่ข้อความ error ที่มาจากภายนอก — ``skipped_rows`` มาจาก CSV ของผู้ใช้.

    ทุกย่อหน้าต้องผ่านตัว escape ตัวเดียวกัน ไม่งั้นแถวที่ถูกตัดทิ้งจะหายจากรายงาน
    (= กลับไปตัดข้อมูลทิ้งเงียบ ๆ) หรือทำให้สร้าง PDF ไม่ได้ทั้งฉบับ
    """
    totals = dict(
        _totals_two_bases(),
        skipped_rows=[{"ticker": "A&B<x", "date": "2026-07-01"}],
    )
    _, _, pdf = _render(monkeypatch, total_summary=totals)
    drawn = _squashed(pdf)
    assert "A&B<x" in drawn, f"ชื่อกองในแถวที่ถูกตัดหายจากกระดาษ — ที่วาดจริง: {drawn[-400:]}"
    assert "2026-07-01" in drawn


def test_ข้อความไทยจากความล้มเหลวก็ต้องไม่เป็นสี่เหลี่ยม(monkeypatch: pytest.MonkeyPatch):
    """ทางที่ deterministic ที่สุด: ข้อความ error ของ fetcher เป็นไทยทั้งประโยค."""

    def boom(tickers, years=10):
        raise PriceDataUnavailableError("ดึงข้อมูลราคาไม่สำเร็จหลังลอง 3 ครั้ง")

    _, paras, pdf = _render(monkeypatch, prices=boom)
    assert "ZapfDingbats" not in _embedded_fonts(pdf)
    assert not [p for p in paras if any(ord(c) in THAI_RANGE for c in p)]


# --------------------------------------------------------------------------- #
# AUDIT_ROUND2_2026-08-07 — คำเตือนสมุดบัญชีต้องมาครบทั้งสามชุด
# --------------------------------------------------------------------------- #
def _ledger_totals(**overrides) -> dict:
    """ยอดรวมที่มีคำเตือนสมุดบัญชีครบทุกคีย์ตามที่ ``tracker.get_total_summary()`` คืน."""
    totals = dict(
        _totals_two_bases(),
        missing_prices=[],
        invested_thb_all=120000.0,
        total_invested_thb=120000.0,
        skipped_rows=[],
        skipped_reason="",
        derived_fx_rows=[],
        derived_fx_reason="",
        inconsistent_rows=[],
        inconsistent_reason="",
        fx_rate_thb=34.0,
        fx_is_live=True,
    )
    totals.update(overrides)
    return totals


_DERIVED_ROW = {
    "tx_id": "d1",
    "date": "2025-01-05",
    "ticker": "SCHD",
    "tx_type": "buy",
    "recorded_fx": None,
    "used_fx": 33.9,
    "fee_assumed_zero": True,
    "reason": "ไม่ได้บันทึกอัตราแลกเปลี่ยน — ใช้อัตราที่คำนวณย้อนจากยอดเงินบาท 33.9000",
}
_INCONSISTENT_ROW = {
    "tx_id": "i1",
    "date": "2024-11-11",
    "ticker": "VOO",
    "tx_type": "buy",
    "amount_thb": 34000.0,
    "implied_amount_thb": 32000.0,
    "diff_pct": 5.7,
    "reason": "ยอดเงินที่บันทึกไว้ 34,000.00 บาท ไม่ตรงกับ จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน",
}


def test_แถวที่อัตราถูกคำนวณย้อนต้องขึ้นคำเตือนบนกระดาษ(monkeypatch: pytest.MonkeyPatch):
    """``derived_fx_rows`` = แถวที่ **ยังถูกนับอยู่** ในยอดรวม แต่อัตราไม่ใช่ค่าที่บันทึกไว้.

    เดิม PDF อ่านแค่ ``skipped_rows`` ชุดนี้จึงหายเงียบ ผู้ใช้ที่อ่านรายงานย้อนหลัง
    ไม่มีทางรู้ว่ายอดบาทที่เห็นคำนวณจากอัตราที่ระบบหาเอง
    """
    totals = _ledger_totals(
        derived_fx_rows=[_DERIVED_ROW],
        derived_fx_reason="อัตราแลกเปลี่ยน 1 แถวถูกคำนวณย้อนจากยอดเงินบาท",
    )
    _, paras, pdf = _render(monkeypatch, total_summary=totals)

    warned = [p for p in paras if "back-computed" in p.lower()]
    assert warned, f"แถวที่อัตราถูกคำนวณย้อนหายจากรายงานทั้งฉบับ — ย่อหน้า: {paras}"
    assert "SCHD" in warned[0] and "2025-01-05" in warned[0], (
        f"ไม่ได้บอกว่าต้องไปแก้แถวไหนในสมุด: {warned[0]}"
    )
    assert "included in the totals" in warned[0], (
        f"ต้องบอกว่าแถวนี้ยังถูกนับอยู่ (คนละความหมายกับ skipped): {warned[0]}"
    )
    assert "SCHD" in _squashed(pdf), "คำเตือนไม่ได้ถูกวาดลงกระดาษจริง"


def test_แถวที่ยอดเงินขัดกันเองต้องขึ้นคำเตือนบนกระดาษ(monkeypatch: pytest.MonkeyPatch):
    """``inconsistent_rows`` = ยอดบาทขัดกับ จำนวนหุ้น × ราคา × อัตรา ในแถวเดียวกัน."""
    totals = _ledger_totals(
        inconsistent_rows=[_INCONSISTENT_ROW],
        inconsistent_reason="ยอดเงินบาทของ 1 แถวไม่ตรงกับ จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน",
    )
    _, paras, pdf = _render(monkeypatch, total_summary=totals)

    warned = [p for p in paras if "contradicts" in p.lower()]
    assert warned, f"แถวที่ยอดเงินขัดกันเองหายจากรายงานทั้งฉบับ — ย่อหน้า: {paras}"
    assert "VOO" in warned[0] and "2024-11-11" in warned[0], (
        f"ไม่ได้บอกว่าต้องไปแก้แถวไหนในสมุด: {warned[0]}"
    )
    assert "still counted" in warned[0], (
        f"ต้องบอกว่าแถวนี้ยังถูกนับอยู่ในยอดรวม: {warned[0]}"
    )
    assert "2024-11-11" in _squashed(pdf), "คำเตือนไม่ได้ถูกวาดลงกระดาษจริง"


def test_คำเตือนสมุดบัญชีสามชุดต้องไม่ยุบรวมกัน(monkeypatch: pytest.MonkeyPatch):
    """สามชุดคือเตือนคนละความหมาย (tracker.py เขียน invariant นี้ไว้เอง)."""
    totals = _ledger_totals(
        skipped_rows=[{"ticker": "QQQM", "date": "2026-02-02"}],
        skipped_reason="ข้ามธุรกรรม 1 แถวเพราะข้อมูลไม่ครบ",
        derived_fx_rows=[_DERIVED_ROW],
        derived_fx_reason="อัตราแลกเปลี่ยน 1 แถวถูกคำนวณย้อนจากยอดเงินบาท",
        inconsistent_rows=[_INCONSISTENT_ROW],
        inconsistent_reason="ยอดเงินบาทของ 1 แถวไม่ตรงกับ จำนวนหุ้น × ราคา × อัตราแลกเปลี่ยน",
    )
    _, paras, _ = _render(monkeypatch, total_summary=totals)

    kinds = {
        "skipped": [p for p in paras if "skipped for incomplete data" in p],
        "derived": [p for p in paras if "back-computed" in p.lower()],
        "inconsistent": [p for p in paras if "contradicts" in p.lower()],
    }
    missing = [name for name, found in kinds.items() if not found]
    assert not missing, f"คำเตือนชุด {missing} หายจากรายงาน — ย่อหน้า: {paras}"
    texts = [found[0] for found in kinds.values()]
    assert len(set(texts)) == 3, f"สามชุดถูกยุบเป็นข้อความเดียวกัน: {texts}"


def test_สมุดสะอาดต้องไม่มีคำเตือนสมุดบัญชีเลย(monkeypatch: pytest.MonkeyPatch):
    """กันแก้เกิน — ไม่มีแถวน่าสงสัยแล้วต้องไม่มีคำเตือนมาปนให้สับสน."""
    _, paras, _ = _render(monkeypatch, total_summary=_ledger_totals())
    noisy = [
        p
        for p in paras
        if "back-computed" in p.lower() or "contradicts" in p.lower() or "skipped" in p.lower()
    ]
    assert not noisy, f"สมุดสะอาดแต่มีคำเตือนขึ้นมา: {noisy}"


# --------------------------------------------------------------------------- #
# AUDIT_ROUND2_2026-08-07 — ป้ายที่มาของอัตราแลกเปลี่ยน (B9)
# --------------------------------------------------------------------------- #
def test_อัตราสำรองต้องขึ้นคำเตือนบนกระดาษ(monkeypatch: pytest.MonkeyPatch):
    """``fx_is_live=False`` = ตัวเลขบาททั้งหน้าคิดจากค่าสำรองใน config."""
    totals = _ledger_totals(fx_is_live=False, fx_rate_thb=34.5)
    _, paras, pdf = _render(monkeypatch, total_summary=totals)

    warned = [p for p in paras if "fallback FX rate" in p]
    assert warned, f"PDF ไม่ได้บอกว่าตัวเลขบาทคิดจากอัตราสำรอง — ย่อหน้า: {paras}"
    assert "34.5000" in warned[0], f"ไม่ได้บอกว่าอัตราที่ใช้คือเท่าไร: {warned[0]}"
    assert "WARNING" in warned[0], f"ค่าสำรองต้องเป็นคำเตือน ไม่ใช่หมายเหตุเฉย ๆ: {warned[0]}"
    assert "fallbackFXrate" in _squashed(pdf), "คำเตือนไม่ได้ถูกวาดลงกระดาษจริง"


def test_อัตราสดกับอัตราสำรองต้องได้เอกสารคนละฉบับ(monkeypatch: pytest.MonkeyPatch):
    """หลักฐานตรงของ AUDIT: เดิม PDF สองรอบเหมือนกันทุกตัวอักษร.

    ต้องใช้ ``monkeypatch.context()`` คนละอันต่อรอบ ไม่งั้นตัวดัก ``Paragraph`` ของรอบแรก
    ถูกตัวดักของรอบสองครอบทับ แล้วย่อหน้าของรอบสองไหลเข้าลิสต์ของรอบแรกด้วย
    """
    with monkeypatch.context() as first:
        _, live_paras, _ = _render(first, total_summary=_ledger_totals(fx_is_live=True))
    with monkeypatch.context() as second:
        _, fallback_paras, _ = _render(second, total_summary=_ledger_totals(fx_is_live=False))

    assert live_paras != fallback_paras, (
        "อัตราสดกับอัตราสำรองให้ย่อหน้าชุดเดียวกันเป๊ะ — ผู้ใช้แยกไม่ออกว่าเลขบาทมาจากไหน"
    )
    assert not [p for p in live_paras if "fallback FX rate" in p], (
        f"อัตราสดถูกพิมพ์ว่าเป็นค่าสำรอง: {live_paras}"
    )
    assert [p for p in live_paras if "live FX rate" in p], (
        f"อัตราสดก็ต้องมีป้ายที่มากำกับ: {live_paras}"
    )


def test_ไม่ทราบที่มาของอัตราไม่เท่ากับค่าสำรอง(monkeypatch: pytest.MonkeyPatch):
    """``None`` = ไม่ทราบที่มา — คนละข้อความกับ ``False`` ที่รู้ว่าเป็นค่าสำรอง."""
    totals = _ledger_totals()
    totals.pop("fx_is_live")
    _, paras, _ = _render(monkeypatch, total_summary=totals)

    unknown = [p for p in paras if "no recorded source" in p]
    assert unknown, f"ที่มาที่ไม่ทราบถูกปล่อยผ่านโดยไม่มีป้ายเลย — ย่อหน้า: {paras}"
    assert not [p for p in paras if "fallback FX rate" in p or "live FX rate" in p], (
        f"'ไม่ทราบที่มา' ถูกยุบรวมกับ 'ค่าสำรอง'/'ค่าสด': {paras}"
    )


# --------------------------------------------------------------------------- #
# AUDIT_ROUND2_2026-08-07 — อีโมจิต้องไม่กลายเป็นกล่องสี่เหลี่ยม (tofu)
# --------------------------------------------------------------------------- #
def _has_emoji(text: str) -> bool:
    return any(pe._is_undrawable(ch) for ch in text)


def test_อีโมจิต้องไม่ถูกส่งลงกระดาษ(monkeypatch: pytest.MonkeyPatch):
    """Garuda/Helvetica ไม่มีกลิฟอีโมจิ — reportlab วาดกล่องสี่เหลี่ยมว่างแทนโดยไม่ error."""
    advice = dict(_advice(), advice_text="\U0001f512 AI commentary is off to control cost")
    _, paras, pdf = _render(monkeypatch, advice=advice)

    left = [p for p in paras if _has_emoji(p)]
    assert not left, f"ยังส่งอีโมจิเข้า PDF ทั้งที่ฟอนต์วาดไม่ได้: {left}"
    assert any("AI commentary is off" in p for p in paras), (
        f"ถอดอีโมจิแล้วเนื้อความหายไปด้วย — ย่อหน้า: {paras}"
    )
    assert "AIcommentaryisoff" in _squashed(pdf)


def test_ข้อความ_AI_ปิดอยู่ของจริงไม่พาอีโมจิลงกระดาษ(
    monkeypatch: pytest.MonkeyPatch, synthetic_thai_font: Path
):
    """ผูกกับต้นทางจริง ``analysis.llm.AI_DISABLED_MESSAGE`` (ขึ้นต้นด้วย 🔒).

    ต้องมีฟอนต์ไทยด้วย ไม่งั้น ``_pdf_text`` แทนทั้งประโยคด้วยหมายเหตุอังกฤษ
    แล้วเทสต์จะผ่านด้วยเหตุผลที่ไม่เกี่ยวกับอีโมจิเลย
    """
    from analysis.llm import AI_DISABLED_MESSAGE

    assert _has_emoji(AI_DISABLED_MESSAGE), (
        "ต้นทางไม่มีอีโมจิแล้ว — เทสต์นี้ไม่ได้ทดสอบอะไรอีกต่อไป ให้ทบทวนใหม่"
    )
    monkeypatch.setattr(pe, "_thai_font_files", lambda: [str(synthetic_thai_font)])
    pe._reset_thai_font_cache()

    advice = dict(_advice(), advice_text=AI_DISABLED_MESSAGE)
    _, paras, _ = _render(monkeypatch, advice=advice)

    left = [p for p in paras if _has_emoji(p)]
    assert not left, f"🔒 จาก AI_DISABLED_MESSAGE ยังหลุดลงกระดาษ: {left}"
    assert any("บทวิเคราะห์ AI ปิดอยู่" in p for p in paras), (
        f"เนื้อความไทยหายไปพร้อมอีโมจิ — ย่อหน้า: {paras}"
    )


def test_ถอดอีโมจิต้องไม่กินเครื่องหมายวรรคตอนของข้อความจริง():
    """กันแก้เกิน — – — … • เป็นอักขระข้อความจริงที่ Garuda วาดได้ ห้ามถูกกวาดทิ้ง."""
    keep = "กำไร 1,000 บาท – เพิ่มขึ้น 5% … ดูรายละเอียด • ข้อ 1 — สรุป"
    assert pe._drawable(keep) == keep
    assert pe._drawable("\U0001f512 ปิดอยู่") == "ปิดอยู่"
    assert pe._drawable("a\U0001f512b") == "a b", "ถอดแล้วสองคำต้องไม่ติดกันเป็นคำเดียว"


# --------------------------------------------------------------------------- #
# ราคา + FX ชุดเดียวต่อรายงานหนึ่งฉบับ (AUDIT_ROUND2_2026-08-07)
# --------------------------------------------------------------------------- #
# หน้า 1 ของ PDF พิมพ์ **ตาราง Holdings รายกอง** กับ **บล็อกยอดรวม + คำเตือน
# missing_prices** ไว้บนกระดาษแผ่นเดียวกัน เดิม ``generate_monthly_report()`` เรียก
# ``get_portfolio_summary()`` (ยิงราคา+FX รอบที่ 1) แล้วเรียก ``get_total_summary()``
# แบบไม่ส่งอาร์กิวเมนต์ ซึ่งไปเรียก ``get_portfolio_summary()`` เองอีกรอบ (รอบที่ 2)
# ⇒ ถ้า yfinance ติด rate limit คั่นกลาง เอกสารฉบับเดียวที่ถูกเก็บไว้อ่านย้อนหลัง
# จะมีสองคำตอบที่ขัดกันเอง เช่นยอดรวม 153,000.00 บาท วางอยู่เหนือแถว VOO ที่พิมพ์ว่า
# N/A หรือกลับกัน  เทสต์ชุดนี้ตรึงว่า "หนึ่งรายงาน = หนึ่ง snapshot" จริง ๆ
HEADER = "tx_id,date,ticker,shares,price_usd,fx_rate_thb,amount_thb,fee_thb,note,tx_type\n"
# 10 VOO @ 400 USD อัตรา 34.00 → จ่ายจริง 136,000 บาท (ยอดสอดคล้อง ไม่มีแถวถูกตัด)
ONE_BUY = "t1,2026-01-05,VOO,10,400,34.0,136000,0,,buy\n"


class _CountingPrices:
    """ผู้ให้ราคาที่นับจำนวนครั้งที่ถูกเรียก และเปลี่ยนคำตอบได้ตามรอบ.

    ``responses`` คือคำตอบของการเรียกครั้งที่ 1, 2, ... (ครั้งถัด ๆ ไปใช้ตัวสุดท้าย)
    ใช้จำลอง rate limit ที่มาคั่นระหว่างการดึงราคาสองรอบในเอกสารฉบับเดียว
    """

    def __init__(self, *responses: dict[str, float]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, tickers: list[str]) -> dict[str, float]:
        self.calls += 1
        return dict(self.responses[min(self.calls, len(self.responses)) - 1])


class _CountingFx:
    """อัตราแลกเปลี่ยนคงที่ พร้อมที่มา — นับจำนวนครั้งที่ถูกเรียกต่อรายงานหนึ่งฉบับ."""

    def __init__(self, rate: float = 34.0, is_live: bool = True) -> None:
        self.quote = (rate, is_live)
        self.calls = 0

    def __call__(self) -> tuple[float, bool]:
        self.calls += 1
        return self.quote


@pytest.fixture()
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """สมุดบัญชีสังเคราะห์ใน tmp — ห้ามแตะสมุดจริงของผู้ใช้ (ไฟล์นี้ถูก gitignore)."""
    from portfolio import tracker

    data_dir = tmp_path / "ledger"
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / "transactions.csv"
    monkeypatch.setattr(tracker, "DATA_DIR", data_dir)
    monkeypatch.setattr(tracker, "TRANSACTIONS_FILE", csv_path)
    csv_path.write_text(HEADER + ONE_BUY, encoding="utf-8")
    return csv_path


def _render_with_real_tracker(
    monkeypatch: pytest.MonkeyPatch,
    prices: _CountingPrices,
    fx: _CountingFx | None = None,
) -> tuple[list[list[list[str]]], list[str], _CountingFx]:
    """สร้าง PDF โดย **ไม่** สตับ tracker — วัดจำนวนครั้งที่รายงานยิงราคา/FX จริง.

    สตับเฉพาะทางออกอื่นที่ไม่เกี่ยวกับ snapshot พอร์ต (ราคาย้อนหลังของหน้า 2 กับ
    ``get_monthly_advice`` ของหน้า 3) เพราะทั้งคู่ยิงเน็ต/เสียเงิน และเป็นคนละเส้นทาง
    กับสิ่งที่เทสต์ชุดนี้วัด
    """
    from portfolio import tracker

    fx = fx or _CountingFx()
    monkeypatch.setattr(tracker, "_get_latest_prices", prices)
    monkeypatch.setattr(tracker, "_get_fx_quote", fx)

    tables, paras = _record_drawn(monkeypatch)
    monkeypatch.setattr(pe, "get_tickers", lambda: ["VOO"])
    monkeypatch.setattr(pe, "fetch_adjusted_close_data", lambda tickers, years=10: _prices_df())
    monkeypatch.setattr(pe, "get_monthly_advice", lambda **kwargs: _advice())

    pe.generate_monthly_report(month="2026-08", budget_thb=5000, include_ai=False)
    return tables, paras, fx


def _missing_price_warnings(paras: list[str]) -> list[str]:
    return [p for p in paras if "current price unavailable" in p]


def test_ยอดรวมต้องคิดจาก_snapshot_เดียวกับตาราง_holdings(monkeypatch: pytest.MonkeyPatch):
    """``get_total_summary()`` ต้องได้รับ DataFrame ตัวเดียวกับที่พิมพ์ในตาราง Holdings."""
    recorder: dict = {}
    _render(monkeypatch, recorder=recorder)

    assert recorder["holdings"].calls == 1, (
        f"รายงานหนึ่งฉบับดึง snapshot พอร์ต {recorder['holdings'].calls} ครั้ง"
    )
    assert recorder["totals"].calls == 1
    assert len(recorder["totals"].received) == 1
    assert recorder["totals"].received[0] is recorder["holdings"].frame, (
        "ยอดรวมไม่ได้คิดจาก snapshot ตัวเดียวกับที่พิมพ์ในตาราง Holdings"
    )


def test_รายงานหนึ่งฉบับยิงราคาและอัตราแลกเปลี่ยนครั้งเดียว(
    ledger: Path, monkeypatch: pytest.MonkeyPatch
):
    """นับของจริงผ่าน ``tracker._get_latest_prices`` — มากกว่า 1 = มีสอง snapshot บนหน้าเดียว."""
    prices = _CountingPrices({"VOO": 450.0})

    _, _, fx = _render_with_real_tracker(monkeypatch, prices)

    assert prices.calls == 1, (
        f"รายงานยิงราคา {prices.calls} ครั้งต่อเอกสารหนึ่งฉบับ — ตาราง Holdings กับ "
        "ยอดรวมบนหน้าเดียวกันจึงมาจากคนละ snapshot ได้"
    )
    assert fx.calls == 1, (
        f"รายงานยิงอัตราแลกเปลี่ยน {fx.calls} ครั้ง — ตัวเลขบาทกับป้ายอัตราที่พิมพ์ "
        "อาจเป็นคนละอัตรา"
    )


def test_ราคาหายหลังรอบแรกต้องไม่ทำให้หน้าหนึ่งขัดกันเอง(
    ledger: Path, monkeypatch: pytest.MonkeyPatch
):
    """รอบแรกได้ราคา รอบสองโดน rate limit — ห้ามได้ยอดรวมสมบูรณ์คู่กับคำเตือน "ไม่มีราคา"."""
    prices = _CountingPrices({"VOO": 450.0}, {})

    tables, paras, _ = _render_with_real_tracker(monkeypatch, prices)

    summary = _summary_rows(tables)
    price_cell = _row(tables, "VOO")[3]           # Current Price (USD)
    value_cell = summary["Current Value (priced holdings)"]
    warnings = _missing_price_warnings(paras)

    priced_in_table = price_cell != pe.NA
    priced_in_totals = value_cell != pe.NA
    assert priced_in_table == priced_in_totals, (
        "ตาราง Holdings กับยอดรวมบนหน้าเดียวกันเล่าคนละเรื่อง: "
        f"Current Price (USD)={price_cell} แต่ Current Value={value_cell}"
    )
    assert bool(warnings) is not priced_in_totals, (
        f"พิมพ์คำเตือนว่าดึงราคาไม่ได้ ({warnings}) คู่กับยอดรวม {value_cell} "
        "ที่คิดจากราคาตัวนั้นเอง"
    )
    # snapshot เดียว = ของรอบแรก (ราคาที่ดึงได้จริง)
    assert _money(price_cell) == pytest.approx(450.0)
    assert _money(value_cell) == pytest.approx(153000.0)
    assert warnings == []


def test_ราคาหายตั้งแต่รอบแรกต้องไม่มียอดรวมโผล่จากรอบหลัง(
    ledger: Path, monkeypatch: pytest.MonkeyPatch
):
    """ตรงข้ามกัน: รอบแรกไม่ได้ราคา — ห้ามมีตัวเลขมูลค่าจากการดึงรอบหลังมาวางบนแถว N/A."""
    prices = _CountingPrices({}, {"VOO": 450.0})

    tables, paras, _ = _render_with_real_tracker(monkeypatch, prices)

    summary = _summary_rows(tables)
    assert _row(tables, "VOO")[3] == pe.NA, "ไม่รู้ราคา ห้ามพิมพ์ราคาในตาราง"
    assert summary["Current Value (priced holdings)"] == pe.NA, (
        "ไม่รู้ราคา ห้ามมียอดรวมโผล่มาจากการดึงรอบหลัง"
    )
    assert summary["Profit / Loss (priced only)"] == pe.NA
    # เงินที่จ่ายไปแล้วยังรู้เสมอ — คนละเรื่องกับ "ดึงราคาไม่ได้"
    assert _money(summary["Invested - all holdings"]) == pytest.approx(136000.0)
    warnings = _missing_price_warnings(paras)
    assert warnings and "VOO" in warnings[0], (
        f"ดึงราคาไม่ได้ต้องมีคำเตือนบนกระดาษ ไม่ใช่แค่ช่อง N/A เงียบ ๆ — ย่อหน้า: {paras}"
    )


def test_ledger_fixture_ไม่แตะสมุดจริง(ledger: Path):
    """กันพลาด: fixture ต้องชี้ไปที่สมุดชั่วคราวเสมอ."""
    from portfolio import tracker

    assert str(tracker.TRANSACTIONS_FILE) == str(ledger)
    assert "portfolio/data/transactions.csv" not in str(tracker.TRANSACTIONS_FILE)
