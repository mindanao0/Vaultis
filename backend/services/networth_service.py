"""Net Worth service: snapshot persistence and current-value calculation."""

from __future__ import annotations

import calendar
import json
import logging
from datetime import UTC, date, datetime
from typing import Any, NamedTuple

from sqlalchemy.orm import Session

from utils import fx

from ..models.networth_models import (
    Asset,
    EtfStatus,
    Liability,
    NetWorthResponse,
    SnapshotAgeStatus,
    SnapshotRequest,
)
from ..models.orm import NetWorthSnapshot
from ..services.portfolio_service import get_holdings

logger = logging.getLogger(__name__)

# snapshot ที่เก่ากว่านี้ = เงินสด/หนี้สินในคำตอบอาจไม่ใช่ของจริงแล้ว ต้องเตือน
STALE_SNAPSHOT_DAYS = 90


class _LiveEtf(NamedTuple):
    """ผลการตีมูลค่า ETF จากราคาสด **พร้อมสิ่งที่ตีไม่ได้**.

    ``fx_rate``/``fx_is_live`` เป็น ``None`` ได้สองความหมาย ซึ่งแยกกันด้วย ``fx_error``:
    ไม่ได้ใช้ FX เลย (``fx_error is None``) กับ ต้องใช้แล้วใช้ไม่ได้ (มีข้อความ) — G3
    """

    assets: list[Asset]
    missing_prices: list[str]
    skipped_rows: list[dict[str, Any]]
    skipped_reason: str
    fx_rate: float | None
    fx_is_live: bool | None
    holdings_count: int
    # เหตุผลที่ไม่มีอัตราแลกเปลี่ยนให้ใช้ (ข้อความไทยจาก ``fx.FxRateUnavailable``)
    fx_error: str | None
    # ticker ที่ **มีราคาแล้ว** แต่แปลงเป็นบาทไม่ได้เพราะไม่มีอัตรา — คนละกองกับ
    # ``missing_prices`` ซึ่งแปลว่าดึงราคาไม่ได้ (กฎข้อ 2 ห้ามยุบสองเหตุเป็นเหตุเดียว)
    unconverted: list[str]


def _etf_assets_live() -> _LiveEtf:
    """ETF holdings ที่มีราคาจริง → Asset (THB) + รายงานตัวที่ตีมูลค่าไม่ได้.

    ใช้ FX สดจากแหล่งกลาง — เดิมใช้ ``default_fx_rate`` 33.5 คงที่จาก config
    ทำให้มูลค่า Net Worth ต่างจากหน้า Portfolio (AUDIT.md M5) และเก็บ ``is_live``
    ไว้ส่งต่อด้วย เพราะค่าสำรองทำให้ตัวเลขบาททั้งก้อนคลาดเคลื่อน (L-NW-2)

    ถือครองที่ดึงราคาไม่ได้ **ถูกข้ามและถูกรายงาน** — ข้ามเฉย ๆ ให้ผลเหมือนนับเป็น 0
    ทุกประการ เพราะมันหายจากตัวตั้งของยอดรวม (AUDIT_2026-08-06 H11)

    **ขออัตราแลกเปลี่ยนเฉพาะเมื่อมีมูลค่า USD ที่ต้องแปลงจริง ๆ** (G3) เดิมเรียก
    ``fx.get_usdthb()`` แบบไม่มีเงื่อนไขก่อนจะรู้ด้วยซ้ำว่ามี holding หรือไม่ ⇒ หลัง B9
    ทำให้ค่าสำรองที่ตั้งผิดโยน :class:`fx.FxRateUnavailable` สมุดว่าง (ซึ่งไม่มีอะไร
    ต้องแปลงเป็นบาทเลย) ก็พาทั้ง ``/api/networth/current`` เป็น HTTP 500 ตามไปด้วย
    เงินสด/สินทรัพย์อื่น/หนี้สินที่เป็นตัวเลขบาทล้วนจึงหายไปทั้งก้อน — fail-closed
    ต้องปิดเฉพาะส่วนที่เชื่อถือไม่ได้ ไม่ใช่ปิดทั้งคำตอบ
    """
    try:
        report = get_holdings()
    except fx.FxRateUnavailable as exc:
        # ``tracker`` แปลงมูลค่าเป็นบาทตั้งแต่ต้นทาง สมุดที่มีธุรกรรมจริงจึงล้มตรงนี้
        # ก่อนถึงบรรทัดขอ FX ข้างล่าง — ผลลัพธ์ต้องเหมือนกัน คือ ETF หายจากยอด
        # (พร้อมเหตุผล) ไม่ใช่ทั้งคำตอบหาย
        # ทางนี้ไม่ได้อ่านสมุดสำเร็จ จึงยังไม่รู้จำนวน holding เลย — ``fx_error`` เป็นตัว
        # ตัดสินสถานะแทน ``holdings_count`` ทั้งหมด (ดู :func:`_resolve_etf`)
        logger.warning("ตีมูลค่า ETF ในสมุดเป็นเงินบาทไม่ได้ (ไม่มีอัตราแลกเปลี่ยน): %s", exc)
        return _LiveEtf(
            assets=[],
            missing_prices=[],
            skipped_rows=[],
            skipped_reason="",
            fx_rate=None,
            fx_is_live=None,
            holdings_count=0,
            fx_error=str(exc),
            unconverted=[],
        )

    holdings = list(report.get("holdings") or [])
    skipped_rows = list(report.get("skipped_rows") or [])
    skipped_reason = str(report.get("skipped_reason") or "")

    priced: list[tuple[str, float]] = []
    missing: list[str] = []
    for h in holdings:
        ticker = str(h.get("ticker") or "?")
        value_usd = h.get("current_value_usd")
        if not h.get("price_ok") or value_usd is None:
            missing.append(ticker)
            continue
        priced.append((ticker, float(value_usd)))

    if not priced:
        # ไม่มีดอลลาร์สักก้อนให้แปลง ⇒ อัตราแลกเปลี่ยนไม่เกี่ยวกับคำตอบนี้เลย
        return _LiveEtf(
            assets=[],
            missing_prices=missing,
            skipped_rows=skipped_rows,
            skipped_reason=skipped_reason,
            fx_rate=None,
            fx_is_live=None,
            holdings_count=len(holdings),
            fx_error=None,
            unconverted=[],
        )

    try:
        rate, fx_is_live = fx.get_usdthb()
    except fx.FxRateUnavailable as exc:
        logger.warning("แปลงมูลค่า ETF เป็นเงินบาทไม่ได้: %s", exc)
        return _LiveEtf(
            assets=[],
            missing_prices=missing,
            skipped_rows=skipped_rows,
            skipped_reason=skipped_reason,
            fx_rate=None,
            fx_is_live=None,
            holdings_count=len(holdings),
            fx_error=str(exc),
            unconverted=[t for t, _ in priced],
        )

    assets: list[Asset] = []
    for ticker, value_usd in priced:
        value_thb = round(value_usd * rate, 2)
        if value_thb <= 0:
            # ไม่เหลือหน่วยลงทุนแล้ว — 0 ที่เป็นจริง ไม่ใช่ข้อมูลที่หายไป
            # (และ ``Asset.value_thb`` บังคับ > 0 อยู่แล้ว)
            continue
        assets.append(Asset(name=ticker, type="etf", value_thb=value_thb))

    return _LiveEtf(
        assets=assets,
        missing_prices=missing,
        skipped_rows=skipped_rows,
        skipped_reason=skipped_reason,
        fx_rate=rate,
        fx_is_live=fx_is_live,
        holdings_count=len(holdings),
        fx_error=None,
        unconverted=[],
    )


def _latest_snapshot(db: Session) -> NetWorthSnapshot | None:
    return (
        db.query(NetWorthSnapshot)
        .order_by(NetWorthSnapshot.snapshot_date.desc(), NetWorthSnapshot.id.desc())
        .first()
    )


def _months_back(today: date, months: int) -> date:
    """ย้อนหลังเป็น "เดือนปฏิทิน" จริง ๆ ไม่ใช่ ``months × 30`` วัน.

    เดิม ``months=120`` ได้ cutoff ที่ขาดไป 52 วัน — snapshot ที่ผู้ใช้บันทึกไว้จริง
    ในช่วงนั้นหายจากประวัติเงียบ ๆ (AUDIT_2026-08-06 L-NW-1)
    """
    total = today.year * 12 + (today.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _split_snapshot_assets(row: NetWorthSnapshot) -> tuple[list[Asset], list[Asset], list[Liability]]:
    saved = json.loads(row.assets_json)
    non_etf = [Asset(**a) for a in saved if a.get("type") != "etf"]
    etf = [Asset(**a) for a in saved if a.get("type") == "etf"]
    liabilities = [Liability(**l) for l in json.loads(row.liabilities_json)]
    return non_etf, etf, liabilities


class _SnapshotAge(NamedTuple):
    """อายุของ snapshot **พร้อมกรณีที่บอกอายุไม่ได้**.

    ``stale is None`` = ตอบไม่ได้ว่าเก่าหรือใหม่ (ไม่มี snapshot / วันที่อ่านไม่ออก /
    วันที่อยู่ในอนาคต) — เดิมทั้งสามกรณีคืน ``False`` ซึ่งผู้บริโภคอ่านว่า "ยังใหม่"
    (AUDIT_2026-08-06 K2 ข้อ 4) และวันที่อนาคตยังให้ ``age_days`` ติดลบ ซึ่งไม่ใช่อายุ
    """

    days: int | None
    stale: bool | None
    status: SnapshotAgeStatus


def _snapshot_age(snapshot_date: str | None, today: date) -> _SnapshotAge:
    if snapshot_date is None or not str(snapshot_date).strip():
        return _SnapshotAge(None, None, "no_snapshot")
    try:
        parsed = date.fromisoformat(str(snapshot_date))
    except ValueError:
        logger.warning("snapshot_date ผิดรูปแบบ: %r — บอกอายุข้อมูลไม่ได้", snapshot_date)
        return _SnapshotAge(None, None, "unreadable_date")

    days = (today - parsed).days
    if days < 0:
        # แถวเก่าที่ลงวันที่อนาคตไว้ก่อนมี validation ที่ชั้น schema — อายุติดลบ
        # ไม่ใช่ข้อมูล ต้องเป็น "ไม่รู้" ไม่ใช่ "ยังใหม่มาก"
        logger.warning("snapshot_date อยู่ในอนาคต: %r — บอกอายุข้อมูลไม่ได้", snapshot_date)
        return _SnapshotAge(None, None, "future_date")

    stale = days > STALE_SNAPSHOT_DAYS
    return _SnapshotAge(days, stale, "stale" if stale else "fresh")


def _resolve_etf(
    live: _LiveEtf, snapshot_etf: list[Asset]
) -> tuple[list[Asset], EtfStatus]:
    """ตัดสินว่ามูลค่า ETF ในคำตอบนี้มาจากไหน — ราคาสด หรือ snapshot หรือไม่มีเลย.

    สองรูที่เคยอยู่ตรงนี้ (AUDIT_2026-08-06 K2 ข้อ 1-2):

    - สมุดไม่มี ETF ⇒ ตอบ ``no_holdings`` ทันทีโดยไม่ดู snapshot เลย ⇒ ETF ที่ผู้ใช้
      **กรอกมูลค่าเอง** หายจากยอดรวมเงียบ ๆ (เท่ากับนับเป็น 0)
    - ``holdings=[]`` เพราะ tracker ตัดทุกแถวทิ้ง ให้ค่าเท่ากับสมุดที่ไม่มี ETF จริง ๆ
      ทั้งที่ความจริงคือ **อ่านสมุดไม่ได้** จึงยังบอกไม่ได้ว่ามี ETF หรือไม่

    ``fx_error`` ถูกตัดสินก่อนทุกอย่าง เพราะไม่มีอัตราแลกเปลี่ยน = ตีมูลค่าสดไม่ได้
    สักบาท ไม่ว่าราคาจะครบแค่ไหน — และต้องไม่ถูกรายงานว่า ``live`` (G3)
    """
    if live.fx_error:
        # มูลค่าใน snapshot เป็นเงินบาทที่ผู้ใช้กรอกเอง จึงยังใช้ได้ทั้งที่ FX ล่ม
        if snapshot_etf:
            return snapshot_etf, "from_snapshot"
        return [], "fx_unavailable"

    # ตัดทุกแถวทิ้งจนไม่เหลือ holding = อ่านสมุดไม่ได้ ≠ สมุดไม่มี ETF
    ledger_unreadable = not live.holdings_count and bool(live.skipped_rows)

    if live.holdings_count and not live.missing_prices:
        return live.assets, "live"
    if live.holdings_count and live.assets:
        return live.assets, "partial"

    # ไม่มีมูลค่าสดสักบาท — ETF ที่บันทึกไว้ใน snapshot คือข้อมูลเดียวที่ยังเหลือ
    if snapshot_etf:
        return snapshot_etf, "from_snapshot"
    if live.missing_prices:
        return [], "unavailable"
    if ledger_unreadable:
        return [], "ledger_unreadable"
    return [], "no_holdings"


def _uncounted_snapshot_etf(
    counted: list[Asset], snapshot_etf: list[Asset], missing_prices: list[str]
) -> list[str]:
    """ETF ใน snapshot ที่ไม่ได้เข้ายอดรวมและไม่ได้ถูกรายงานที่อื่น.

    ผู้ใช้กรอก "กองทุน SSF" ไว้ใน snapshot แต่ในสมุดไม่มี ⇒ พอยอด ETF มาจากราคาสด
    ของสมุด ตัวนั้นจะหายไปเฉย ๆ — ต้องประกาศ ไม่ใช่ทิ้งเงียบ
    (ตัวที่อยู่ใน ``missing_prices`` ถูกรายงานไปแล้ว จึงไม่นับซ้ำ)
    """
    known = {a.name.strip().casefold() for a in counted}
    known |= {str(t).strip().casefold() for t in missing_prices}
    return [a.name for a in snapshot_etf if a.name.strip().casefold() not in known]


def get_current(db: Session) -> NetWorthResponse:
    """Live net worth: ETF values from yfinance + non-ETF assets from latest snapshot.

    ทุกสิ่งที่ "ไม่ได้อยู่ในยอดรวม" ต้องออกไปกับคำตอบด้วยเสมอ (``missing_prices`` /
    ``skipped_rows`` / ``etf_status`` / อายุของ snapshot / ที่มาของอัตราแลกเปลี่ยน)

    **ความล้มเหลวของอัตราแลกเปลี่ยนกินแค่ส่วนที่ใช้อัตรานั้น** (G3) เงินสด สินทรัพย์
    นอก ETF และหนี้สินเป็นตัวเลขบาทที่ผู้ใช้บันทึกเอง ไม่พึ่ง FX เลย จึงต้องออกไปกับ
    คำตอบตามปกติพร้อม ``fx_error`` + ``warnings`` ภาษาไทย ไม่ใช่หายไปทั้งก้อนกับ 500
    """
    today = date.today()
    live = _etf_assets_live()

    non_etf_assets: list[Asset] = []
    snapshot_etf: list[Asset] = []
    liabilities: list[Liability] = []
    as_of: str | None = None
    latest = _latest_snapshot(db)
    if latest:
        as_of = str(latest.snapshot_date)
        non_etf_assets, snapshot_etf, liabilities = _split_snapshot_assets(latest)

    etf_assets, etf_status = _resolve_etf(live, snapshot_etf)

    warnings: list[str] = []
    tickers = ", ".join(live.missing_prices)
    if live.fx_error:
        # "แปลงเป็นบาทไม่ได้" ≠ "ดึงราคาไม่ได้" ≠ "ไม่มี ETF" — ต้องแยกกันถึงหน้าจอ (G3)
        unconverted = ", ".join(live.unconverted) if live.unconverted else "ในสมุด"
        if etf_status == "from_snapshot":
            warnings.append(
                f"แปลงมูลค่า ETF ({unconverted}) เป็นเงินบาทไม่ได้ เพราะไม่มีอัตราแลกเปลี่ยน "
                f"ที่ใช้ได้ — ใช้มูลค่า ETF จาก snapshot วันที่ {as_of} แทน (ไม่ใช่ราคาสด)"
            )
        else:
            warnings.append(
                f"แปลงมูลค่า ETF ({unconverted}) เป็นเงินบาทไม่ได้ เพราะไม่มีอัตราแลกเปลี่ยน "
                "ที่ใช้ได้ และไม่มี snapshot ให้ใช้แทน — ยอดนี้ยังไม่รวม ETF "
                "(ไม่ได้แปลว่าไม่มี ETF) ส่วนเงินสด/สินทรัพย์อื่น/หนี้สินเป็นตัวเลขบาทอยู่แล้ว "
                "จึงยังใช้ได้ตามปกติ"
            )
        warnings.append(live.fx_error)
        if live.missing_prices:
            warnings.append(
                f"ดึงราคาไม่ได้: {tickers} — ยอดรวมยังไม่รวมมูลค่าส่วนนี้ (ไม่ได้นับเป็น 0)"
            )
    elif etf_status == "from_snapshot":
        if live.missing_prices:
            warnings.append(
                f"ดึงราคา ETF ไม่ได้เลย ({tickers}) — ใช้มูลค่าจาก snapshot วันที่ {as_of} แทน"
            )
        elif live.skipped_rows:
            warnings.append(
                f"อ่านสมุดธุรกรรมไม่ได้สักแถว — ใช้มูลค่า ETF จาก snapshot วันที่ {as_of} แทน "
                "(ไม่ได้แปลว่าสมุดไม่มี ETF)"
            )
        else:
            warnings.append(
                f"สมุดธุรกรรมไม่มี ETF — ยอด ETF ก้อนนี้เป็นมูลค่าที่กรอกไว้เองใน snapshot "
                f"วันที่ {as_of} ไม่ใช่ราคาสด"
            )
    elif etf_status == "unavailable":
        warnings.append(
            f"ดึงราคา ETF ไม่ได้เลย ({tickers}) และไม่มี snapshot ให้ใช้แทน "
            "— ยอดนี้ยังไม่รวม ETF (ไม่ได้แปลว่าไม่มี ETF)"
        )
    elif etf_status == "ledger_unreadable":
        warnings.append(
            "อ่านสมุดธุรกรรมไม่ได้สักแถว (ทุกแถวถูกตัดเพราะข้อมูลไม่ครบ) และไม่มีมูลค่า ETF "
            "ใน snapshot ให้ใช้แทน — ยอดนี้ยังไม่รวม ETF และยังบอกไม่ได้ว่ามี ETF อยู่หรือไม่"
        )
    elif live.missing_prices:  # partial — บางตัวมีราคา บางตัวหายไปจากยอด
        warnings.append(
            f"ดึงราคาไม่ได้: {tickers} — ยอดรวมยังไม่รวมมูลค่าส่วนนี้ (ไม่ได้นับเป็น 0)"
        )

    if etf_status in ("live", "partial"):
        uncounted = _uncounted_snapshot_etf(etf_assets, snapshot_etf, live.missing_prices)
        if uncounted:
            warnings.append(
                f"ETF ใน snapshot วันที่ {as_of} ที่ไม่มีในสมุดธุรกรรม ({', '.join(uncounted)}) "
                "ไม่ได้ถูกนับในยอดนี้ — ยอด ETF มาจากราคาสดของสมุดเท่านั้น"
            )

    if live.skipped_reason:
        warnings.append(live.skipped_reason)
    # ``is False`` เท่านั้น — ``None`` แปลว่า "ไม่ได้ใช้ FX เลย" ซึ่งไม่ใช่ค่าสำรอง
    # (เดิมเป็น ``not live.fx_is_live`` ที่กลืน ``None`` เข้ามาด้วย แล้วจะระเบิดที่
    # ``{None:.2f}`` — "ไม่ได้ใช้" ห้ามถูกรายงานเป็น "ใช้ค่าสำรอง")
    if live.fx_is_live is False:
        warnings.append(
            f"ใช้อัตราแลกเปลี่ยนสำรองจาก config ({live.fx_rate:.2f} บาท/ดอลลาร์) ไม่ใช่ค่าสด "
            "— ตัวเลขบาทอาจคลาดเคลื่อน"
        )

    age = _snapshot_age(as_of, today)
    if latest is None:
        warnings.append(
            "ยังไม่เคยบันทึก snapshot — ยอดนี้มีเฉพาะ ETF ยังไม่รวมเงินสด/สินทรัพย์อื่น/หนี้สิน"
        )
    elif age.status == "unreadable_date":
        warnings.append(
            f"snapshot ล่าสุดมีวันที่อ่านไม่ออก ({as_of}) — บอกไม่ได้ว่าสินทรัพย์นอก ETF "
            "และหนี้สินในยอดนี้เก่าแค่ไหน"
        )
    elif age.status == "future_date":
        warnings.append(
            f"snapshot ล่าสุดลงวันที่ในอนาคต ({as_of}) — บอกไม่ได้ว่าสินทรัพย์นอก ETF "
            "และหนี้สินในยอดนี้เก่าแค่ไหน"
        )
    elif age.stale:
        warnings.append(
            f"สินทรัพย์นอก ETF และหนี้สินมาจาก snapshot วันที่ {as_of} (เก่า {age.days} วัน) "
            "— บันทึก snapshot ใหม่เพื่อให้ตัวเลขตรงกับปัจจุบัน"
        )

    assets = non_etf_assets + etf_assets
    total_assets = sum(a.value_thb for a in assets)
    total_liabilities = sum(l.value_thb for l in liabilities)

    return NetWorthResponse(
        snapshot_date=today.isoformat(),
        assets=assets,
        liabilities=liabilities,
        total_assets_thb=round(total_assets, 2),
        total_liabilities_thb=round(total_liabilities, 2),
        net_worth_thb=round(total_assets - total_liabilities, 2),
        etf_live=etf_status == "live",
        etf_status=etf_status,
        missing_prices=live.missing_prices,
        skipped_rows=live.skipped_rows,
        skipped_reason=live.skipped_reason,
        fx_rate=live.fx_rate,
        fx_is_live=live.fx_is_live,
        fx_error=live.fx_error,
        as_of_snapshot_date=as_of,
        snapshot_age_days=age.days,
        snapshot_stale=age.stale,
        snapshot_age_status=age.status,
        warnings=warnings,
    )


def get_history(db: Session, months: int = 12) -> list[NetWorthResponse]:
    """Return one snapshot per date for the past N months, newest first."""
    today = date.today()
    cutoff = _months_back(today, months).isoformat()
    rows = (
        db.query(NetWorthSnapshot)
        .filter(NetWorthSnapshot.snapshot_date >= cutoff)
        .order_by(NetWorthSnapshot.snapshot_date.desc(), NetWorthSnapshot.id.desc())
        .all()
    )
    seen: set[str] = set()
    result: list[NetWorthResponse] = []
    for row in rows:
        if row.snapshot_date in seen:
            continue
        seen.add(row.snapshot_date)
        assets = [Asset(**a) for a in json.loads(row.assets_json)]
        liabilities = [Liability(**l) for l in json.loads(row.liabilities_json)]
        has_etf = any(a.type == "etf" for a in assets)
        age = _snapshot_age(row.snapshot_date, today)
        result.append(
            NetWorthResponse(
                snapshot_date=row.snapshot_date,
                assets=assets,
                liabilities=liabilities,
                total_assets_thb=row.total_assets_thb,
                total_liabilities_thb=row.total_liabilities_thb,
                net_worth_thb=row.net_worth_thb,
                etf_live=False,
                etf_status="from_snapshot" if has_etf else "no_holdings",
                as_of_snapshot_date=row.snapshot_date,
                snapshot_age_days=age.days,
                snapshot_stale=age.stale,
                snapshot_age_status=age.status,
            )
        )
    return result


def _ledger_price_report() -> tuple[list[str], list[str]]:
    """ตรวจสมุดว่ามี ETF ตัวไหนดึงราคาไม่ได้ ก่อนตรึงตัวเลขลง snapshot.

    snapshot ที่ ETF ขาดไปจะอยู่ถาวรและกลายเป็นฐานของกราฟย้อนหลัง จึงต้องเตือน
    (ไม่ปฏิเสธ — ผู้ใช้อาจกรอกมูลค่าเองโดยไม่พึ่งราคาสดก็ได้) — AUDIT H11 ข้อ 3
    """
    try:
        report = get_holdings()
    except Exception as exc:  # การตรวจล้มเหลว ≠ ไม่มีอะไรผิด — ต้องบอกไปตรง ๆ
        logger.warning("ตรวจราคา ETF ในสมุดก่อนบันทึก snapshot ไม่สำเร็จ: %s", exc)
        return [], [f"ตรวจสอบราคา ETF ในสมุดไม่ได้ ({exc}) — ยอด ETF ในก้อนนี้ยังไม่ได้ถูกทาน"]

    missing = [
        str(h.get("ticker") or "?")
        for h in (report.get("holdings") or [])
        if not h.get("price_ok") or h.get("current_value_usd") is None
    ]
    warnings: list[str] = []
    if missing:
        warnings.append(
            f"ราคาของ {', '.join(missing)} ดึงไม่ได้ตอนบันทึก — ถ้ายอด ETF ก้อนนี้มาจาก "
            "หน้า Net Worth มันจะขาดส่วนนั้นไป และจะถูกตรึงไว้ถาวร"
        )
    reason = str(report.get("skipped_reason") or "")
    if reason:
        warnings.append(reason)
    return missing, warnings


def save_snapshot(db: Session, payload: SnapshotRequest) -> NetWorthResponse:
    """Persist a manual snapshot and return it."""
    snapshot_date = payload.snapshot_date or date.today().isoformat()
    total_assets = sum(a.value_thb for a in payload.assets)
    total_liabilities = sum(l.value_thb for l in payload.liabilities)
    net_worth = total_assets - total_liabilities

    has_etf = any(a.type == "etf" for a in payload.assets)
    # ไม่มี ETF ในก้อนที่บันทึก = ไม่มีอะไรให้ทาน (และไม่ต้องไปดึงราคา)
    missing_prices, warnings = _ledger_price_report() if has_etf else ([], [])

    row = NetWorthSnapshot(
        snapshot_date=snapshot_date,
        total_assets_thb=round(total_assets, 2),
        total_liabilities_thb=round(total_liabilities, 2),
        net_worth_thb=round(net_worth, 2),
        assets_json=json.dumps([a.model_dump() for a in payload.assets], ensure_ascii=False),
        liabilities_json=json.dumps([l.model_dump() for l in payload.liabilities], ensure_ascii=False),
        created_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    age = _snapshot_age(snapshot_date, date.today())
    return NetWorthResponse(
        snapshot_date=snapshot_date,
        assets=payload.assets,
        liabilities=payload.liabilities,
        total_assets_thb=row.total_assets_thb,
        total_liabilities_thb=row.total_liabilities_thb,
        net_worth_thb=row.net_worth_thb,
        etf_live=False,
        etf_status="from_snapshot" if has_etf else "no_holdings",
        missing_prices=missing_prices,
        as_of_snapshot_date=snapshot_date,
        snapshot_age_days=age.days,
        snapshot_stale=age.stale,
        snapshot_age_status=age.status,
        warnings=warnings,
    )
