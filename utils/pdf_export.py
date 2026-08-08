# -*- coding: utf-8 -*-
"""PDF export utilities for monthly portfolio report.

กฎของไฟล์นี้ (AUDIT_2026-08-06 ข้อ A4):

1. **ตัวเลขที่ไม่มี ต้องเป็น ``N/A`` ห้ามเป็น ``0.00``** — รายงานนี้ถูกเก็บไว้อ่านย้อนหลัง
   โดยไม่มีบริบทหน้าจอ ``0.00`` อ่านเป็นเงินได้ทันที (H5) ใช้ :func:`_fmt` ทุกช่องเชิงเงิน
2. **"ดึงไม่สำเร็จ" ≠ "ไม่มีข้อมูล"** — ความล้มเหลวของการดึงราคา/ของโมเดล ต้องถูกพิมพ์
   ออกมาเป็นคำเตือนพร้อมสาเหตุ ห้ามใช้ข้อความเดียวกับกรณี "ไม่มีข้อมูลจริง" (M-PDF-1/2)
3. **ห้ามวาดอักษรที่รู้อยู่แล้วว่าอ่านไม่ออก** — ถ้าไม่มีฟอนต์ไทย reportlab จะสลับไป
   ZapfDingbats แล้ววาด ■ หนึ่งตัวต่ออักษรไทยหนึ่งตัว โดยไม่ error (H6) ทางหลักคือ
   *พิมพ์ไทยได้จริง*: Dockerfile ลง ``fonts-tlwg-garuda`` ไว้ที่
   ``/usr/share/fonts/truetype/tlwg/`` ซึ่ง :data:`_SYSTEM_FONT_GLOBS` มองหาเป็นอันดับแรก
   การแทนด้วยข้อความอังกฤษเป็น**ตาข่ายท้ายสุด**สำหรับเครื่องที่ไม่มีฟอนต์ ไม่ใช่คำตอบ
   ข้อเดียวกันครอบ **อีโมจิ** ด้วย: Garuda มีแต่กลิฟไทย/ละติน 🔒 ที่นำหน้า
   ``analysis.llm.AI_DISABLED_MESSAGE`` จึงกลายเป็นกล่องสี่เหลี่ยม (tofu) บนกระดาษ
   :func:`_drawable` ถอดมันออกก่อนวาดทุกครั้ง (AUDIT_ROUND2_2026-08-07)
4. **คำเตือนจากสมุดบัญชีต้องมาครบทั้งสามชุด** — ``portfolio/tracker.py`` แยก
   ``skipped_rows`` (ถูกตัดออกจากยอด) / ``derived_fx_rows`` (อัตราถูกคำนวณย้อน) /
   ``inconsistent_rows`` (ยอดเงินขัดกับตัวเลขอื่นในแถวเดียวกัน) ไว้คนละความหมาย
   PDF เคยพิมพ์แค่ชุดแรก อีกสองชุดหายเงียบ ⇒ เอกสารที่เก็บไว้อ่านย้อนหลังเสนอ
   ยอดเงินที่มีแถวน่าสงสัยปนอยู่ว่าเป็นตัวเลขสะอาด (AUDIT_ROUND2_2026-08-07)
5. **ตัวเลขบาททุกช่องต้องมีป้ายที่มาของอัตราแลกเปลี่ยน** — ``fx_is_live=False``
   แปลว่าคิดจากค่าสำรองใน ``config.json`` ไม่ใช่ราคาสด ต้องเตือนแบบเดียวกับ
   ``missing_prices`` (B9) เดิม PDF ออกมาเหมือนกันทุกตัวอักษรทั้งสองกรณี
6. **ราคา+FX ชุดเดียวต่อรายงานหนึ่งฉบับ** — ตาราง Holdings กับบล็อกยอดรวม/
   ``missing_prices`` อยู่บนกระดาษแผ่นเดียวกัน ถ้ามาจากคนละ snapshot เอกสารจะมี
   สองคำตอบที่ขัดกันเองเมื่อ yfinance ติด rate limit คั่นกลาง ⇒ เรียก
   ``get_portfolio_summary()`` ครั้งเดียวแล้ว **ส่ง DataFrame ตัวนั้นต่อ** ให้
   ``get_total_summary(holdings)`` เสมอ (AUDIT_ROUND2_2026-08-07)
"""

from __future__ import annotations

import glob
import logging
import math
import os
import re
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from analysis.ai_advisor import get_monthly_advice
from analysis.risk import calculate_risk_metrics
from analysis.returns import RETURNS_HISTORY_YEARS, calculate_period_returns
from data.fetcher import fetch_adjusted_close_data
from portfolio.tracker import get_portfolio_summary, get_total_summary
from utils.config import get_tickers

logger = logging.getLogger(__name__)


def _last_n_years(prices: "pd.DataFrame", years: int) -> "pd.DataFrame":
    """หั่นเฟรมราคาเหลือ ``years`` ปีล่าสุด (นับจากวันที่จริง ไม่ใช่จำนวนแถว).

    ใช้เมื่อเฟรมถูกดึงยาวกว่าที่ผู้อ่านต้องการ — ตาราง Returns ต้องการแท่งเพิ่มเพื่อให้
    หน้าต่าง 10Y คำนวณได้ (FIX_PLAN ข้อ 2.8) แต่ตาราง Risk ต้องคิดจากช่วงเดิม
    ไม่งั้น MaxDD/Volatility ในกระดาษจะเปลี่ยนเงียบ ๆ เพราะเหตุผลที่ไม่เกี่ยวกับมันเลย

    ดัชนีที่ไม่ใช่วันที่ (หรือเฟรมว่าง) คืนของเดิม — ไม่เดาแล้วหั่นผิด
    """
    if prices.empty or not isinstance(prices.index, pd.DatetimeIndex):
        return prices
    cutoff = prices.index[-1] - pd.DateOffset(years=years)
    return prices.loc[prices.index >= cutoff]

NA = "N/A"

# --------------------------------------------------------------------------- #
# ฟอนต์ไทย (H6)
# --------------------------------------------------------------------------- #
THAI_FONT_ENV = "VAULTIS_PDF_THAI_FONT"

# พยัญชนะ + สระลอย + วรรณยุกต์ — ฟอนต์ที่ขาดตัวใดตัวหนึ่งวาดภาษาไทยไม่ครบ
_THAI_PROBE_CHARS = ("ก", "ำ", "่")
_THAI_BLOCK = range(0x0E00, 0x0E80)

_NO_THAI_FONT_NOTE = (
    "[Thai text omitted: this build has no Thai font registered, so the text would print as "
    "black boxes instead of words. Install a Thai TTF (Debian/Ubuntu: apt-get install "
    "fonts-tlwg-garuda) or point VAULTIS_PDF_THAI_FONT at a .ttf file, then generate the "
    "report again. The project's Docker image installs it already - rebuild it if this note "
    "shows up there.]"
)
_NO_THAI_FONT_NOTE_SHORT = "[Thai text omitted - no Thai font in this build]"

# ที่ที่ไปตามหาฟอนต์ไทยของระบบ — **ต้องครอบตำแหน่งที่ Dockerfile ติดตั้งไว้จริง**
# (image ลง fonts-tlwg-garuda ⇒ /usr/share/fonts/truetype/tlwg/Garuda.ttf)
# ถ้าสองฝั่งนี้หลุดจากกันเมื่อไร รายงานในคอนเทนเนอร์จะไม่มีภาษาไทยอีกเลยโดยไม่มีใครรู้
# — tests/test_pdf_export.py ตรึงความสัมพันธ์นี้ไว้
_SYSTEM_FONT_GLOBS: tuple[str, ...] = (
    "/usr/share/fonts/truetype/tlwg/Garuda.ttf",          # Debian/Ubuntu: fonts-tlwg-garuda
    "/usr/share/fonts/truetype/tlwg/*.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansThai*.ttf",
    "/usr/share/fonts/thai-scalable/*.ttf",              # Fedora: thai-scalable-fonts
    "/usr/share/fonts/google-noto*/NotoSansThai*.ttf",    # Fedora: google-noto-*-fonts
    "/usr/share/fonts/**/*Thai*.ttf",
)

# ผลการค้นหาฟอนต์ถูกแคชไว้ (การอ่าน/แจง TTF ไม่ควรทำซ้ำทุกครั้งที่สร้างรายงาน)
_thai_font_cache: tuple[str | None, str | None] | None = None


def _thai_font_files() -> list[str]:
    """รายชื่อไฟล์ฟอนต์ที่ *อาจ* รองรับภาษาไทย เรียงตามลำดับความชอบ.

    ตัวแปรแวดล้อมมาก่อนเสมอ — ผู้ใช้ที่วางฟอนต์ไว้เองต้องชนะฟอนต์ของระบบ
    """
    paths: list[str] = []

    env_path = os.getenv(THAI_FONT_ENV, "").strip()
    if env_path:
        paths.append(env_path)

    repo_fonts = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    paths.extend(sorted(str(p) for p in repo_fonts.glob("*.ttf")))

    for pattern in _SYSTEM_FONT_GLOBS:
        paths.extend(sorted(glob.glob(pattern, recursive=True)))

    return list(dict.fromkeys(paths))


def _font_name_for(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", Path(path).stem) or "VaultisThai"


def _register_if_thai(path: str) -> str | None:
    """ลงทะเบียนฟอนต์ถ้ามันวาดอักษรไทยได้จริง — คืนชื่อที่ลงทะเบียน หรือ ``None``.

    ต้องตรวจ ``charToGlyph`` เอง เพราะ reportlab ลงทะเบียนฟอนต์ที่ไม่มีอักษรไทย
    ได้สำเร็จโดยไม่ error แล้วค่อยไปวาด ■ ตอน build (= อาการเดิมทุกอย่าง)
    """
    if not os.path.isfile(path):
        return None
    name = _font_name_for(path)
    try:
        font = TTFont(name, path)
    except Exception as exc:  # ไฟล์เสีย / เป็น .ttc / เป็น variable font ที่แจงไม่ได้
        logger.warning("PDF report: อ่านฟอนต์ %s ไม่ได้ (%s: %s)", path, type(exc).__name__, exc)
        return None

    char_map = getattr(font.face, "charToGlyph", None) or {}
    if not all(ord(ch) in char_map for ch in _THAI_PROBE_CHARS):
        return None

    pdfmetrics.registerFont(font)
    return name


def _bold_candidates(path: str) -> list[str]:
    p = Path(path)
    stems = [
        p.stem.replace("-Regular", "-Bold"),
        p.stem.replace("Regular", "Bold"),
        f"{p.stem}-Bold",
        f"{p.stem}Bold",
    ]
    return [str(p.with_name(f"{stem}{p.suffix}")) for stem in dict.fromkeys(stems) if stem != p.stem]


def _discover_thai_fonts() -> tuple[str | None, str | None]:
    for path in _thai_font_files():
        regular = _register_if_thai(path)
        if regular is None:
            continue
        bold = next((b for b in (_register_if_thai(c) for c in _bold_candidates(path)) if b), None)
        pdfmetrics.registerFontFamily(
            regular,
            normal=regular,
            bold=bold or regular,
            italic=regular,
            boldItalic=bold or regular,
        )
        logger.info("PDF report: ใช้ฟอนต์ไทย %s (%s)", regular, path)
        return regular, bold
    logger.warning(
        "PDF report: ไม่พบฟอนต์ไทยในเครื่อง — ข้อความไทยในรายงานจะถูกแทนด้วยหมายเหตุภาษาอังกฤษ "
        "(ตั้ง %s หรือติดตั้ง fonts-thai-tlwg เพื่อให้พิมพ์ภาษาไทยได้)",
        THAI_FONT_ENV,
    )
    return None, None


def _reset_thai_font_cache() -> None:
    """ล้างผลการค้นหาฟอนต์ (ใช้ในชุดเทสต์)."""
    global _thai_font_cache
    _thai_font_cache = None


def _thai_fonts() -> tuple[str | None, str | None]:
    global _thai_font_cache
    if _thai_font_cache is None:
        _thai_font_cache = _discover_thai_fonts()
    return _thai_font_cache


def _thai_font() -> str | None:
    """ชื่อฟอนต์ไทยที่ลงทะเบียนแล้ว หรือ ``None`` ถ้าเครื่องนี้ไม่มีฟอนต์ไทย."""
    return _thai_fonts()[0]


# --------------------------------------------------------------------------- #
# อักขระที่ฟอนต์ของรายงานวาดไม่ได้ (AUDIT_ROUND2_2026-08-07)
# --------------------------------------------------------------------------- #
# ฟอนต์ที่รายงานนี้ใช้ได้จริงมีสองตัวเท่านั้น: Garuda (ไทย+ละติน) กับ Helvetica
# ทั้งคู่ **ไม่มีกลิฟอีโมจิเลย** — reportlab ไม่ error แต่วาดกล่องสี่เหลี่ยมว่าง (tofu)
# ออกมาแทน ซึ่งบนเอกสารที่ผู้ใช้เก็บไว้/ส่งต่อ อ่านได้ว่า "ฟอนต์ไทยพัง" ทั้งที่ไทยปกติดี
# (ต้นทางคือ 🔒 ที่นำหน้า ``analysis.llm.AI_DISABLED_MESSAGE`` — ห้ามไปลบที่ต้นทาง
# เพราะหน้าเว็บแสดงได้ปกติ ต้องถอดตรงจุดที่วาดลงกระดาษเท่านั้น)
#
# ถอดเฉพาะย่านที่เป็น "สัญลักษณ์ประดับ" ล้วน ๆ ห้ามกวาดกว้างกว่านี้: – — … • ‘ ’ “ ”
# (U+2010–U+205F) เป็นอักขระข้อความจริงที่ Garuda วาดได้และมีความหมายในประโยค
_UNDRAWABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x2600, 0x27BF),    # Misc Symbols + Dingbats — ⚠ ✅ ➡
    (0x2B00, 0x2BFF),    # Misc Symbols and Arrows — ⬆ ⭐
    (0xFE00, 0xFE0F),    # Variation Selectors — ตัวคุมการแสดงผลของ emoji (VS15/VS16)
    (0x1F000, 0x1FAFF),  # Emoji / pictographs ทั้งย่าน — 🔒 📊 📈
)


def _is_undrawable(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _UNDRAWABLE_RANGES)


def _drawable(text: str) -> str:
    """ข้อความที่ไม่มีอักขระซึ่งรู้อยู่แล้วว่าจะออกมาเป็นกล่องสี่เหลี่ยม (กฎข้อ 3).

    ตัดทิ้งได้โดยไม่ผิดกฎ "ห้ามตัดข้อมูลทิ้งเงียบ" เพราะอีโมจิเป็น**เครื่องประดับ**
    ไม่ใช่ข้อมูล — ข้อความที่อยู่ถัดไปคือเนื้อความจริงและยังอยู่ครบทุกตัวอักษร
    (ต่างจากอักษรไทยที่ไม่มีฟอนต์ ซึ่ง :func:`_pdf_text` **ต้อง** ทิ้งหมายเหตุบอกไว้
    เพราะเนื้อความหายไปจริง)

    แทนด้วยช่องว่างก่อนแล้วยุบช่องว่างซ้ำ เพื่อไม่ให้คำสองคำติดกันเป็นคำเดียว
    (ยุบเฉพาะเว้นวรรค/แท็บ — ขึ้นบรรทัดใหม่เป็นการจัดหน้าของข้อความ AI ต้องคงไว้)
    """
    if not any(_is_undrawable(ch) for ch in text):
        return text
    replaced = "".join(" " if _is_undrawable(ch) else ch for ch in text)
    return re.sub(r"[ \t]{2,}", " ", replaced).strip()


def _markup(value: object) -> str:
    """ข้อความที่ปลอดภัยสำหรับ :class:`Paragraph` — reportlab แจงย่อหน้าเป็น mini-XML.

    คำเตือนของ M-PDF-1/M-PDF-2 พิมพ์ ``str(exc)`` ลงย่อหน้า และข้อความจริงของ
    requests/yfinance/psycopg มีทั้ง ``<Response [429] ...>`` และ URL ที่มี ``&``
    ถ้าไม่ escape ``<...>`` จะถูกกลืนหายเงียบ ๆ (สาเหตุหายจากกระดาษทั้งที่ตัวแปรมีค่า
    = "ดึงไม่สำเร็จ" กลับไปแยกไม่ออกจาก "ไม่มีข้อมูล") หรือแย่กว่านั้น ``doc.build()``
    โยน ``ValueError: paraparser: syntax error`` แล้วผู้ใช้ไม่ได้รายงานทั้งฉบับ

    ไฟล์นี้ไม่ได้ใช้มาร์กอัปในย่อหน้าไหนเลย — escape ทั้งหมดได้อย่างปลอดภัย

    ทุกย่อหน้าผ่านที่นี่ที่เดียว จึงเป็นจุดเดียวที่รับประกันได้ว่าไม่มีอีโมจิหลุดลง
    กระดาษ (:func:`_drawable`) — ช่องในตารางไม่ผ่านฟังก์ชันนี้ จึงต้องกรองซ้ำใน
    :func:`_pdf_text` การกรองสองรอบไม่มีผลข้างเคียงเพราะรอบสองไม่เจออะไรให้ถอดแล้ว
    """
    return _xml_escape(_drawable(str(value)))


def _has_thai(text: str) -> bool:
    return any(ord(ch) in _THAI_BLOCK for ch in text)


def _pdf_text(value: object, *, short: bool = False) -> str:
    """ข้อความที่วาดลง PDF ได้จริง — ไม่มีฟอนต์ไทย = ห้ามวาดอักษรไทย (H6).

    อีโมจิถูกถอดออกเสมอ ไม่ว่าจะมีฟอนต์ไทยหรือไม่ (กฎข้อ 3) — ฟอนต์ทั้งสองตัวที่
    รายงานนี้ใช้ไม่มีกลิฟอีโมจิ ดู :func:`_drawable`
    """
    text = _drawable("" if value is None else str(value))
    if not _has_thai(text) or _thai_font() is not None:
        return text
    return _NO_THAI_FONT_NOTE_SHORT if short else _NO_THAI_FONT_NOTE


# --------------------------------------------------------------------------- #
# ตัวเลข (H5)
# --------------------------------------------------------------------------- #
def _to_float(value: object) -> float | None:
    """แปลงเป็น float ที่ใช้ได้จริง — NaN / inf / None / สตริงเปล่า คืน ``None``."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fmt(value: object, spec: str = ",.2f", *, suffix: str = "", na: str = NA) -> str:
    """จัดรูปตัวเลขสำหรับ PDF — ค่าที่ไม่มีคืน ``N/A`` **ห้ามคืน 0** (H5).

    ``0.0`` ที่เป็นคำตอบจริงยังพิมพ์เป็น ``0.00`` ตามปกติ
    """
    number = _to_float(value)
    if number is None:
        return na
    return f"{format(number, spec)}{suffix}"


_MAX_LEDGER_ROWS_IN_NOTE = 5


def _ledger_row_labels(rows: list[dict]) -> str:
    """ป้ายชี้แถวสมุดบัญชี ``TICKER YYYY-MM-DD`` — ผู้ใช้ต้องรู้ว่าต้องไปแก้แถวไหน.

    คำเตือนที่บอกแค่ "มี 3 แถวน่าสงสัย" ใช้ทำอะไรไม่ได้เลยกับเอกสารที่อ่านย้อนหลัง
    ค่าที่หายไปพิมพ์เป็น ``?`` (ไม่ทราบ) ห้ามเดาแทน · ตัดที่ 5 แถวแล้วบอกจำนวนที่เหลือ
    เพื่อไม่ให้หน้าเดียวถูกกลืนด้วยคำเตือน — จำนวนเต็มยังอยู่ในประโยคที่เรียกใช้เสมอ
    """
    labels = ", ".join(
        f"{row.get('ticker') or '?'} {row.get('date') or '?'}"
        for row in rows[:_MAX_LEDGER_ROWS_IN_NOTE]
    )
    remaining = len(rows) - _MAX_LEDGER_ROWS_IN_NOTE
    if remaining > 0:
        labels += f", +{remaining} more"
    return labels


def _allocation_status(advice: dict) -> tuple[list[str], list[str], bool]:
    """แยกว่า "โมเดลคิดแล้วไม่จัดสรร" ต่างจาก "โมเดลไม่เคยได้ข้อมูลมาคิด" (M-PDF-2).

    ``financial_model.calculate_allocation()`` คืน ``{}`` เฉย ๆ โดยไม่บอกเหตุผล —
    ทั้งกรณีที่ทุกกองดึงราคาไม่ได้ (``data_ok=False``) และกรณีที่คิดแล้วไม่มีน้ำหนักเหลือ
    รายงานจึงต้องอ่านสถานะที่ ``get_monthly_advice()`` ส่งมาด้วยแทน:

    * ``etf_scores[].data_ok`` / ``total_pct`` — โมเดลได้คะแนนของกองนั้นจริงหรือไม่
    * ``no_data_tickers`` — รายชื่อที่ผู้ผลิตคะแนนบอกเองว่าไม่มีข้อมูล

    คืน ``(กองที่มีคะแนนจริง, กองที่ไม่มีข้อมูล, รู้สถานะหรือไม่)`` — ``False`` ตัวท้าย
    แปลว่า payload ไม่ได้ส่งสถานะมาเลย ซึ่ง**ไม่ใช่**ใบอนุญาตให้เดาว่าโมเดลแนะนำถือเงินสด

    ที่นี่ไม่ตัดสินด้วยเกณฑ์คะแนนใด ๆ (นิยามอยู่ที่ ``financial_model`` ที่เดียว)
    แค่ถามว่า "มีคะแนนหรือไม่มี" เท่านั้น
    """
    rows = advice.get("etf_scores")
    unusable: list[str] = [str(t) for t in (advice.get("no_data_tickers") or [])]
    if not isinstance(rows, list):
        # ไม่มีสถานะให้อ่าน — คืนรายชื่อที่รู้ (ถ้ามี) พร้อมธง "ไม่รู้"
        return [], unusable, False

    scored: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip()
        if not ticker:
            continue
        if row.get("data_ok", True) and _to_float(row.get("total_pct")) is not None:
            scored.append(ticker)
        elif ticker not in unusable:
            unusable.append(ticker)
    return scored, unusable, True


def _build_table(
    table_data: list[list[object]],
    col_widths: list[float] | None = None,
    *,
    font_name: str = "Helvetica",
    bold_font: str = "Helvetica-Bold",
) -> Table:
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _apply_thai_fonts(styles) -> tuple[str, str]:
    """ชี้ทุกสไตล์ไปที่ฟอนต์ไทยถ้ามี — คืน (ฟอนต์ปกติ, ฟอนต์หนา) สำหรับตาราง."""
    regular, bold = _thai_fonts()
    if regular is None:
        return "Helvetica", "Helvetica-Bold"
    bold_name = bold or regular
    for style in styles.byName.values():
        current = str(getattr(style, "fontName", "") or "")
        style.fontName = bold_name if "Bold" in current else regular
    return regular, bold_name


def generate_monthly_report(month: str, budget_thb: float, include_ai: bool = False) -> bytes:
    """สร้างรายงาน PDF รายเดือน.

    ``include_ai=False`` (ดีฟอลต์): ใส่เฉพาะตัวเลขจากโมเดล — ไม่เรียก AI ไม่มีค่าใช้จ่าย
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    base_font, bold_font = _apply_thai_fonts(styles)
    elements: list[object] = []

    def table(data: list[list[object]], col_widths: list[float] | None = None) -> Table:
        return _build_table(data, col_widths, font_name=base_font, bold_font=bold_font)

    def body(text: str) -> Paragraph:
        return Paragraph(_markup(text), styles["BodyText"])

    title_text = _markup(f"Vaultis Monthly Report - {_pdf_text(month, short=True)}")

    # Common data
    #
    # **ราคา + FX ชุดเดียวต่อรายงานหนึ่งฉบับ** (AUDIT_ROUND2_2026-08-07)
    # เดิมบรรทัดนี้เรียก ``get_total_summary()`` แบบไม่ส่งอะไรเข้าไป มันจึงไป
    # ``get_portfolio_summary()`` เองอีกรอบ = ยิงราคา+อัตราแลกเปลี่ยน **สอง** ชุด
    # ต่อเอกสารหนึ่งฉบับ  หน้า 1 พิมพ์ตาราง Holdings (ชุดที่ 1) ไว้ข้าง ๆ บล็อกยอดรวม
    # และคำเตือน ``missing_prices`` (ชุดที่ 2) — ถ้า yfinance ติด rate limit คั่นกลาง
    # (repo นี้มีประวัติเรื่องนี้จนต้องใส่แคช) กระดาษแผ่นเดียวจะมีสองคำตอบที่ขัดกันเอง:
    # ยอดรวม USD/THB ที่ดูสมบูรณ์ คู่กับ "current price unavailable for VOO" ของกอง
    # เดียวกัน  รายงานฉบับนี้ถูกเก็บไว้อ่านย้อนหลังโดยไม่มีหน้าจอกำกับ ⇒ ต้องเล่าเรื่อง
    # เดียวทั้งหน้า  ``tracker.get_total_summary()`` รับ snapshot ที่คำนวณแล้วได้
    # (พารามิเตอร์ ``holdings``) — ห้ามเรียกแบบไม่ส่งอาร์กิวเมนต์อีก
    holdings_df = get_portfolio_summary()
    total_summary = get_total_summary(holdings_df)
    tickers = list(get_tickers())

    # ดึงราคาไม่สำเร็จ ≠ ไม่มีข้อมูล — ต้องเก็บสาเหตุไว้พิมพ์ ห้ามกลืน (M-PDF-1)
    price_error: BaseException | None = None
    try:
        # ยาวพอสำหรับหน้าต่าง 10Y ของตาราง Returns (FIX_PLAN ข้อ 2.8) — เดิมขอ 10 ปี
        # (~2,511 แท่ง) ให้หน้าต่างที่ต้องการ 2,521 แท่ง ⇒ แถว 10Y เป็น N/A ในกระดาษ
        # ทุกฉบับ ทั้งที่ API ตอบได้ · ตาราง Risk ยังคิดจากช่วง 10 ปีเท่าเดิม (ดูด้านล่าง)
        prices = fetch_adjusted_close_data(tickers=tickers, years=RETURNS_HISTORY_YEARS)
    except Exception as exc:
        price_error = exc
        prices = pd.DataFrame()
        logger.warning(
            "PDF report: ดึงราคาไม่สำเร็จ (%s: %s) — หน้า Performance จะว่าง",
            type(exc).__name__,
            exc,
        )
    returns_df = calculate_period_returns(prices) if not prices.empty else pd.DataFrame()
    # ความเสี่ยงคิดจาก **10 ปีล่าสุด** เท่าเดิม — การขยายช่วงเพราะตาราง Returns ต้องการ
    # แท่งเพิ่ม ต้องไม่ทำให้ MaxDD/Volatility ในกระดาษเปลี่ยนเงียบ ๆ โดยไม่มีใครขอ
    risk_df = (
        calculate_risk_metrics(_last_n_years(prices, 10)) if not prices.empty else pd.DataFrame()
    )

    # ticker ที่ "ไม่มีราคาเลย" — ทั้งที่หายไปทั้งคอลัมน์ และที่มีคอลัมน์แต่ NaN ล้วน
    # (คนละชุดกับ missing_prices ของสมุดบัญชี ซึ่งเป็นราคา ณ ปัจจุบัน)
    no_price_tickers: list[str] = []
    if price_error is None and not prices.empty:
        no_price_tickers = list(
            dict.fromkeys(
                [t for t in tickers if t not in prices.columns]
                + [str(c) for c in prices.columns if int(prices[c].notna().sum()) == 0]
            )
        )
    no_price_note = (
        "WARNING: no price data at all for "
        f"{', '.join(no_price_tickers)} — every figure for them above is {NA}, not zero."
    )
    price_failure_note = (
        f"WARNING: price download failed ({type(price_error).__name__}: "
        f"{_pdf_text(price_error, short=True)}) — this section could not be produced. "
        "Re-generate the report later."
    )

    missing_prices = list(total_summary.get("missing_prices") or [])
    # สมุดบัญชีมีคำเตือน **สามชุดที่ห้ามยุบรวมกัน** (tracker.py เขียน invariant นี้ไว้เอง)
    # และรายงานฉบับนี้ถูกเก็บไว้อ่านย้อนหลังโดยไม่มีหน้าจอกำกับ ⇒ ต้องพิมพ์ให้ครบทั้งสาม
    # (AUDIT_ROUND2_2026-08-07 — เดิมพิมพ์แค่ชุดแรก อีกสองชุดหายเงียบ)
    skipped_rows = list(total_summary.get("skipped_rows") or [])      # ถูกตัดออกจากยอด
    derived_fx_rows = list(total_summary.get("derived_fx_rows") or [])  # อัตราถูกคำนวณย้อน
    inconsistent_rows = list(total_summary.get("inconsistent_rows") or [])  # ยอดเงินขัดกันเอง

    # Page 1: Portfolio summary
    elements.append(Paragraph(title_text, styles["Title"]))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph("Page 1 - Portfolio Summary", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * cm))

    # เงินลงทุนมีสองฐาน และตารางเดียวกันนี้เคยพิมพ์ฐานหนึ่งคู่กับกำไรที่คิดจากอีกฐาน
    # (H9) → ผู้ใช้ประกอบเลขกลับเองไม่ได้  ชื่อคีย์ตรงกับ ``tracker.get_total_summary()``
    # และ ``portfolio_service.get_summary()``: ``invested_thb_all`` = จ่ายไปจริงทั้งหมด ·
    # ``invested_thb_priced`` = เฉพาะกองที่ดึงราคาปัจจุบันได้ ซึ่งเป็น **ฐานเดียว** ที่
    # ``total_pnl_thb`` / ``total_return_pct`` คิดมาจาก  (``total_invested_thb`` คือชื่อเดิม
    # ของฐาน all — อ่านเป็นทางถอยเฉพาะเมื่อคีย์ใหม่ไม่มี ห้ามเดาเป็น 0)
    invested_all = total_summary.get("invested_thb_all", total_summary.get("total_invested_thb"))
    invested_priced = total_summary.get("invested_thb_priced")

    summary_table_data = [
        ["Metric", "Value (THB)"],
        ["Invested - all holdings", _fmt(invested_all)],
        ["Invested - priced holdings only", _fmt(invested_priced)],
        ["Current Value (priced holdings)", _fmt(total_summary.get("current_value_thb"))],
        ["Profit / Loss (priced only)", _fmt(total_summary.get("total_pnl_thb"))],
        [
            "Total Return (%) (priced only)",
            _fmt(total_summary.get("total_return_pct"), suffix="%"),
        ],
    ]
    elements.append(table(summary_table_data, [8 * cm, 8 * cm]))

    invested_all_f = _to_float(invested_all)
    invested_priced_f = _to_float(invested_priced)
    if (
        invested_all_f is not None
        and invested_priced_f is not None
        and round(invested_all_f - invested_priced_f, 2) != 0
    ):
        elements.append(Spacer(1, 0.2 * cm))
        elements.append(
            body(
                "NOTE: the two Invested rows are different bases and differ by "
                f"{invested_all_f - invested_priced_f:,.2f} THB. That gap is money sitting in "
                "holdings whose current price could not be fetched - it is NOT a loss. Current "
                "Value, Profit / Loss and Total Return cover the priced holdings only, so they "
                "reconcile against the priced-only base."
            )
        )
    elif invested_all_f is None or invested_priced_f is None:
        # ฐานใดฐานหนึ่ง "ไม่รู้" (N/A ในตาราง) — ต้องบอกว่าประกอบเลขกลับไม่ได้
        # ห้ามปล่อยให้ตารางดูเหมือนบวกลบลงตัวทั้งที่ตัวหารหายไปหนึ่งตัว
        elements.append(Spacer(1, 0.2 * cm))
        elements.append(
            body(
                "NOTE: one of the two invested bases is unknown (N/A above), so Profit / Loss "
                "and Total Return in this table cannot be reconciled against a base."
            )
        )
    if missing_prices:
        elements.append(Spacer(1, 0.2 * cm))
        elements.append(
            body(
                f"WARNING: current price unavailable for {', '.join(map(str, missing_prices))} — "
                f"value and P&L above exclude these holdings ({NA} in the table below)."
            )
        )
    # ที่มาของอัตราแลกเปลี่ยนที่แปลงทุกช่องในตารางนี้เป็นบาท (B9/C1.5) — ค่าสำรองจาก
    # config ทำให้ตัวเลขบาททั้งหน้าคลาดเคลื่อนได้เป็นเปอร์เซ็นต์ และเอกสารนี้ถูกอ่าน
    # ย้อนหลังโดยไม่มีคำเตือนบนหน้าจอมากำกับ ⇒ ป้ายที่มาต้องอยู่บนกระดาษเสมอ
    # "ไม่ทราบที่มา" (None) ≠ "ค่าสำรอง" (False) ≠ "ค่าสด" (True) — สามข้อความ
    fx_is_live = total_summary.get("fx_is_live")
    fx_rate_txt = _fmt(total_summary.get("fx_rate_thb"), ",.4f")
    elements.append(Spacer(1, 0.2 * cm))
    if fx_is_live is None:
        elements.append(
            body(
                "NOTE: the FX rate behind every THB figure on this page has no recorded "
                f"source (rate on record: {fx_rate_txt}) — this report cannot tell whether it "
                "was a live quote or the config fallback."
            )
        )
    elif not bool(fx_is_live):
        elements.append(
            body(
                f"WARNING: THB figures on this page use the fallback FX rate from config "
                f"({fx_rate_txt} THB/USD), not a live quote — they can be off by a percent or "
                "more. USD figures are unaffected."
            )
        )
    else:
        elements.append(
            body(f"NOTE: THB figures on this page use a live FX rate of {fx_rate_txt} THB/USD.")
        )

    if skipped_rows:
        elements.append(Spacer(1, 0.2 * cm))
        elements.append(
            body(
                f"WARNING: {len(skipped_rows)} ledger row(s) skipped for incomplete data "
                f"({_pdf_text(_ledger_row_labels(skipped_rows), short=True)}) — totals above "
                "exclude them."
            )
        )
    if derived_fx_rows:
        # คนละความหมายกับ skipped: แถวเหล่านี้ **ยังถูกนับ** อยู่ในทุกช่องข้างบน
        elements.append(Spacer(1, 0.2 * cm))
        elements.append(
            body(
                f"WARNING: the FX rate of {len(derived_fx_rows)} ledger row(s) "
                f"({_pdf_text(_ledger_row_labels(derived_fx_rows), short=True)}) was "
                "back-computed from the THB amount because the recorded rate was missing or "
                "unusable — those rows ARE included in the totals above. Details: "
                f"{_pdf_text(total_summary.get('derived_fx_reason') or '', short=True)}"
            )
        )
    if inconsistent_rows:
        elements.append(Spacer(1, 0.2 * cm))
        elements.append(
            body(
                f"WARNING: {len(inconsistent_rows)} ledger row(s) "
                f"({_pdf_text(_ledger_row_labels(inconsistent_rows), short=True)}) record a THB "
                "amount that contradicts shares x price x FX rate + fee — they ARE still counted "
                "in the totals above. Check those rows in the ledger. Details: "
                f"{_pdf_text(total_summary.get('inconsistent_reason') or '', short=True)}"
            )
        )
    elements.append(Spacer(1, 0.4 * cm))

    elements.append(Paragraph("Holdings", styles["Heading3"]))
    if holdings_df.empty:
        elements.append(body("No portfolio transactions found."))
    else:
        holdings_cols = ["Ticker", "Shares", "Avg Cost (USD)", "Current Price (USD)", "P&L (THB)", "Return (%)"]
        # หัวตารางต้องบอกสกุลของ % ให้ตรงกับช่องข้าง ๆ — ``Return (%)`` ในกระดาษนี้เป็น
        # **ฐานเงินบาท** ตัวเดียวกับ ``P&L (THB)`` และกับ %รวมด้านบน (FIX_PLAN ข้อ 3.3)
        # เดิมคอลัมน์นี้เป็นฐานดอลลาร์ วางคู่กับ P&L บาทโดยไม่มีอะไรบอก
        holdings_header = holdings_cols[:-1] + ["Return (THB %)"]
        holdings_table_data: list[list[object]] = [holdings_header]
        for _, row in holdings_df[holdings_cols].iterrows():
            holdings_table_data.append(
                [
                    _pdf_text(row["Ticker"], short=True),
                    _fmt(row["Shares"], ",.4f"),
                    _fmt(row["Avg Cost (USD)"]),
                    _fmt(row["Current Price (USD)"]),
                    _fmt(row["P&L (THB)"]),
                    _fmt(row["Return (%)"], suffix="%"),
                ]
            )
        elements.append(
            table(holdings_table_data, [2.2 * cm, 2.5 * cm, 2.8 * cm, 2.8 * cm, 2.8 * cm, 2.3 * cm])
        )

    # Page 2: Performance
    elements.append(PageBreak())
    elements.append(Paragraph(title_text, styles["Title"]))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph("Page 2 - Performance", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * cm))

    elements.append(Paragraph("Return Analysis (1M / 3M / 6M / 1Y)", styles["Heading3"]))
    if price_error is not None:
        elements.append(body(price_failure_note))
    elif returns_df.empty:
        elements.append(body("No return data available."))
    else:
        return_periods = [period for period in ["1M", "3M", "6M", "1Y"] if period in returns_df.index]
        return_table_data: list[list[object]] = [["Period"] + list(returns_df.columns)]
        for period in return_periods:
            row = [period]
            for ticker in returns_df.columns:
                row.append(_fmt(returns_df.loc[period, ticker], suffix="%"))
            return_table_data.append(row)
        elements.append(table(return_table_data))
        if no_price_tickers:
            elements.append(Spacer(1, 0.2 * cm))
            elements.append(body(no_price_note))

    elements.append(Spacer(1, 0.4 * cm))
    elements.append(Paragraph("Risk Metrics (Volatility / Sharpe / Drawdown)", styles["Heading3"]))
    if price_error is not None:
        elements.append(body(price_failure_note))
    elif risk_df.empty:
        elements.append(body("No risk metrics data available."))
    else:
        risk_table_data: list[list[object]] = [["Ticker", "Volatility", "Sharpe", "Drawdown"]]
        for ticker in risk_df.index:
            risk_table_data.append(
                [
                    _pdf_text(ticker, short=True),
                    _fmt(risk_df.loc[ticker, "Volatility"], ",.4f"),
                    _fmt(risk_df.loc[ticker, "Sharpe Ratio"], ",.4f"),
                    _fmt(risk_df.loc[ticker, "Max Drawdown"], ",.4f"),
                ]
            )
        elements.append(table(risk_table_data, [3 * cm, 4 * cm, 4 * cm, 4 * cm]))
        if no_price_tickers:
            elements.append(Spacer(1, 0.2 * cm))
            elements.append(body(no_price_note))

    # Page 3: AI Advisor summary
    elements.append(PageBreak())
    elements.append(Paragraph(title_text, styles["Title"]))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph("Page 3 - AI Advisor Summary", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * cm))

    advice: dict = {}
    advice_error: BaseException | None = None
    advice_text = ""
    try:
        advice = dict(
            get_monthly_advice(budget_thb=budget_thb, send_discord=False, user_initiated=include_ai)
            or {}
        )
        advice_text = str(advice.get("advice_text", "")).strip()
    except Exception as exc:
        # โมเดลไม่เคยประเมินอะไรเลย — ห้ามให้ตารางว่างกลายเป็น "โมเดลแนะนำให้ถือเงินสด" (M-PDF-2)
        advice_error = exc
        logger.warning(
            "PDF report: get_monthly_advice ล้มเหลว (%s: %s) — หน้า 3 จะไม่มีแผนจัดสรร",
            type(exc).__name__,
            exc,
        )

    # ตาราง allocation มาจากโมเดลโดยตรง — ไม่ regex แกะจากข้อความ AI อีกต่อไป (AUDIT.md C3)
    elements.append(Paragraph("Recommended Allocation (model-computed)", styles["Heading3"]))
    allocation = advice.get("allocation") or {}
    scored_tickers, unusable_tickers, status_known = _allocation_status(advice)
    scored_label = ", ".join(scored_tickers)
    unusable_label = ", ".join(unusable_tickers)
    if advice_error is not None:
        elements.append(
            body(
                f"WARNING: the monthly model run failed ({type(advice_error).__name__}: "
                f"{_pdf_text(advice_error, short=True)}) — no allocation could be produced. "
                "This is NOT a recommendation to hold cash. Re-generate the report later."
            )
        )
    elif allocation:
        allocation_table: list[list[object]] = [["Ticker", "Amount (THB)", "Percent", "Group"]]
        for ticker, item in allocation.items():
            allocation_table.append(
                [
                    _pdf_text(ticker, short=True),
                    _fmt(item.get("amount_thb"), ",.0f"),
                    _fmt(item.get("percent"), ".0f", suffix="%"),
                    _pdf_text(item.get("group", ""), short=True),
                ]
            )
        elements.append(table(allocation_table, [3 * cm, 4 * cm, 3 * cm, 4 * cm]))
        if "unallocated_thb" in advice:
            unallocated = _to_float(advice.get("unallocated_thb"))
            if unallocated is None:
                elements.append(
                    body(f"Unallocated: {NA} (the model did not report a usable number).")
                )
            elif unallocated > 0:
                elements.append(body(f"Unallocated: {unallocated:,.0f} THB"))
    elif scored_tickers and not unusable_tickers:
        # ทุกกองมีข้อมูลครบ โมเดลคิดจนจบแล้วไม่จัดสรร — อันนี้เท่านั้นที่เป็นคำตอบของโมเดล
        elements.append(
            body(
                "No ETF met the allocation threshold this month (model suggests holding cash). "
                f"All {len(scored_tickers)} ETF(s) were scored with usable data: {scored_label}."
            )
        )
    elif scored_tickers:
        # คิดได้บางกอง อีกบางกองไม่มีข้อมูล → สรุปแทนทั้งแผนไม่ได้
        elements.append(
            body(
                f"WARNING: no allocation was produced. The model scored {len(scored_tickers)} "
                f"ETF(s) ({scored_label}) and allocated nothing to them, but "
                f"{len(unusable_tickers)} ETF(s) ({unusable_label}) had no usable data and were "
                "never scored - the plan was computed from part of the universe only. This is "
                "NOT a recommendation to hold cash. Re-generate the report later."
            )
        )
    elif not status_known:
        elements.append(
            body(
                "WARNING: the allocation is empty and this report cannot tell why - the monthly "
                "model run reported no per-ETF status (etf_scores). This is NOT a recommendation "
                "to hold cash. Re-generate the report later."
            )
        )
    elif unusable_tickers:
        elements.append(
            body(
                "WARNING: no allocation could be produced - the model got no usable data for any "
                f"ETF ({unusable_label}), so it never scored anything and never decided anything. "
                "This is NOT a recommendation to hold cash. Re-generate the report later."
            )
        )
    else:
        elements.append(
            body(
                "WARNING: the model evaluated no ETF at all this month (empty score list), so no "
                "allocation could be produced. This is NOT a recommendation to hold cash. "
                "Re-generate the report later."
            )
        )

    no_data = advice.get("no_data_tickers") or []
    if no_data:
        elements.append(
            body(f"NO DATA (excluded from scoring): {', '.join(map(str, no_data))}")
        )

    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph("AI Commentary", styles["Heading3"]))
    if advice_error is not None:
        commentary = (
            f"AI analysis unavailable ({type(advice_error).__name__}: "
            f"{_pdf_text(advice_error, short=True)})"
        )
    else:
        commentary = _pdf_text(advice_text[:2500]) or "No AI analysis content."
    elements.append(body(commentary))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
