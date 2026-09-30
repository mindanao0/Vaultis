# -*- coding: utf-8 -*-
"""ส่งแผน DAR-DCA รายเดือนเข้า Discord — งานของ **พอร์ต DAR แยก** (ไม่แตะงาน/สถานะของแผน ERC หลัก).

รูปแบบเดียวกับ ``main.run_monthly_plan_if_due`` แต่แยกทุกอย่าง: ไฟล์สถานะของตัวเอง
(``VAULTIS_DAR_STATE_PATH``) · ตัวกันส่งซ้ำของตัวเอง · ไม่ใช้ LLM (ตัวเลขล้วน ไม่มีค่าใช้จ่าย)

- ถึงเวลา = ตั้งแต่ 08:00 วันที่ 1 จนสิ้นเดือน (เครื่องปิดอยู่ตอนนั้นก็ส่งย้อนหลังได้)
- ติดตั้งครั้งแรกภายใน 7 วันแรกของเดือน = ส่งแผนเดือนนี้เลย (พอร์ตเพิ่งเริ่มจากศูนย์ แผนยังทันใช้)
  หลังจากนั้น = เริ่มเดือนหน้า (ไม่ยิงแผนของเดือนที่ผ่านไปครึ่งทางแล้ว)
- เฉพาะ Discord ตอบสำเร็จเท่านั้นที่นับว่าส่งแล้ว · ล้ม ``DAR_MAX_FAILED_ATTEMPTS`` ครั้ง = หยุดถึงเดือนหน้า
  พร้อมส่งข้อความแจ้งว่าคำนวณไม่ได้ (ความล้มเหลวต้องดังถึงผู้ใช้ ไม่ใช่เงียบหายทั้งเดือน)
- ไฟล์สถานะอ่านไม่ออก = **ไม่ส่ง** แล้ว log ERROR (เดาว่ายังไม่ส่ง = ส่งซ้ำทุกชั่วโมง)

path สถานะอ่านจาก env **ครั้งเดียวตอน import** (เทสต์ monkeypatch ``DAR_STATE_PATH``)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DAR_STATE_PATH = Path(os.getenv("VAULTIS_DAR_STATE_PATH") or REPO_ROOT / ".dar_scheduler_state.json")
DAR_PLAN_HOUR = 8
DAR_MAX_FAILED_ATTEMPTS = 3
FIRST_INSTALL_SEND_WITHIN_DAYS = 7
EMBED_COLOR = 0x3498DB

# สำรองในหน่วยความจำ: ส่งสำเร็จแต่เขียนไฟล์สถานะไม่ได้ ⇒ อย่าส่งซ้ำทุกชั่วโมง
_dar_sent_in_process: set[str] = set()

_THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


class DarStateUnreadable(RuntimeError):
    """ไฟล์สถานะมีอยู่แต่อ่านไม่ออก — ไม่รู้ว่าส่งแล้วหรือยัง ห้ามเดา."""


def _now_bangkok() -> datetime:
    return datetime.now(ZoneInfo("Asia/Bangkok"))


def _load_state() -> dict[str, Any] | None:
    try:
        raw = DAR_STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DarStateUnreadable(f"{DAR_STATE_PATH}: JSON เสีย ({exc})") from exc
    if not isinstance(data, dict):
        raise DarStateUnreadable(f"{DAR_STATE_PATH}: ไม่ใช่ JSON object")
    return data


def _save_state(state: dict[str, Any]) -> None:
    DAR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DAR_STATE_PATH.with_name(DAR_STATE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DAR_STATE_PATH)


def thai_month(plan_month: str) -> str:
    year, month = plan_month.split("-")
    return f"{_THAI_MONTHS[int(month) - 1]} {year}"


def _fmt_units(units: float | None) -> str:
    return "≈ ? หน่วย (ไม่มีราคา)" if units is None else f"≈ {units:,.4f} หน่วย"


def format_dar_plan_message(plan: Any, comparison: dict[str, Any] | None = None, comparison_error: str | None = None) -> tuple[str, str]:
    """(หัวข้อ, เนื้อความ) ของการ์ด Discord — บอกชัดว่าซื้อกองไหนกี่บาท ≈ กี่หน่วย."""
    title = f"DAR-DCA · แผนเดือน {thai_month(plan.plan_month)}"
    lines = [f"**ซื้อเดือนนี้ รวม {plan.total_thb:,} บาท**"]
    for ln in sorted(plan.lines, key=lambda x: -x.amount_thb):
        price = f"@ ${ln.price_usd:,.2f}" if ln.price_usd else "@ ราคาไม่ทราบ"
        lines.append(f"• **{ln.ticker}** — {ln.amount_thb:,} บาท · {_fmt_units(ln.units)} {price} · {ln.signal.label}")
    if plan.unallocated_thb >= 1:
        lines.append(f"• ยังไม่จัดสรร (เศษจากการปัดหลักร้อย): {plan.unallocated_thb:,.0f} บาท")
    neutral = [ln for ln in plan.lines if ln.signal.neutral_reason]
    if neutral:
        lines.append("")
        lines.append("ได้ส่วนกลางเพราะประวัติยังไม่พอ: " + ", ".join(f"{ln.ticker} ({ln.signal.neutral_reason})" for ln in neutral))
    lines.append("")
    fx_note = "สด" if plan.fx_is_live else "⚠️ ค่าสำรองจาก config — จำนวนหน่วยอาจคลาดเคลื่อน"
    lines.append(f"ข้อมูลราคาถึงสิ้นเดือน {plan.data_through} · อัตราแลกเปลี่ยน {plan.fx_rate:.2f} บาท/ดอลลาร์ ({fx_note})")
    if plan.siblings:
        lines.append("ยืดประวัติด้วยกองพี่: " + ", ".join(f"{k}←{v}" for k, v in plan.siblings.items()))
    if comparison and comparison.get("rows_used"):
        diff = comparison.get("diff_thb")
        pct = comparison.get("diff_pct_of_invested")
        lines.append("")
        lines.append(
            f"พอร์ต DAR: ลงไป {comparison['invested_thb']:,.0f} บาท · มูลค่า (รวมปันผล) {comparison['dar_value_thb']:,.0f} บาท"
        )
        if diff is not None and pct is not None:
            lines.append(f"เทียบพอร์ตเงาแบ่งเท่ากัน (เงินเข้าเท่ากัน): {diff:+,.0f} บาท ({pct:+.2f}% ของเงินที่ลง)")
    elif comparison_error:
        lines.append("")
        lines.append(f"⚠️ เทียบผลพอร์ต DAR ไม่ได้รอบนี้: {comparison_error}")
    lines.append("")
    lines.append("สูตรทดลอง แยกจากแผน ERC หลัก · บันทึกการซื้อ/ดูผลที่หน้า DAR-DCA ใน dashboard")
    return title, "\n".join(lines)


def _default_build() -> Any:
    from analysis.dar_dca import MONTHLY_BUDGET_THB, TICKERS, build_plan, plan_month_of  # noqa: PLC0415

    return build_plan(list(TICKERS), MONTHLY_BUDGET_THB, plan_month_of(_now_bangkok()))


def _default_compare() -> dict[str, Any] | None:
    """เทียบพอร์ต DAR กับเงาแบ่งเท่ากัน — สมุดว่าง = ``None`` (ยังไม่มีอะไรให้เทียบ)."""
    from analysis.dar_dca import fetch_total_return_history  # noqa: PLC0415
    from portfolio.dar_ledger import compare_with_equal_shadow, load_dar_transactions  # noqa: PLC0415
    from utils.fx import get_usdthb  # noqa: PLC0415

    tx = load_dar_transactions()
    if tx.empty:
        return None
    first = tx["date"].min()
    years = max(1, int((_now_bangkok().replace(tzinfo=None) - first.to_pydatetime()).days / 365) + 1)
    prices = fetch_total_return_history(sorted(set(tx["ticker"])), years=years)
    return compare_with_equal_shadow(tx, prices, get_usdthb().rate)


def _default_send(webhook_url: str, title: str, description: str) -> dict[str, Any]:
    from alerts.notifier import send_discord_webhook  # noqa: PLC0415

    return send_discord_webhook(webhook_url, title, description, embed_color=EMBED_COLOR)


def _default_webhook() -> str:
    from utils.config import load_config  # noqa: PLC0415

    return str(load_config()["notifications"].get("discord_webhook_url", "")).strip()


def run_dar_plan_if_due(
    webhook_url: str | None = None,
    *,
    now: datetime | None = None,
    force: bool = False,
    build: Callable[[], Any] | None = None,
    compare: Callable[[], dict[str, Any] | None] | None = None,
    send: Callable[[str, str, str], dict[str, Any]] | None = None,
) -> str:
    """ส่งแผน DAR ของเดือนนี้ถ้าถึงเวลาและยังไม่เคยส่ง — เรียกซ้ำได้ปลอดภัย.

    คืนสถานะ: ``no_webhook`` · ``not_yet`` · ``already_sent`` · ``seeded`` · ``sent`` · ``failed`` ·
    ``gave_up`` · ``state_unreadable`` · ``force=True`` (``--job dar_plan``) ข้ามการเช็คเวลาและสถานะ
    แต่ยังบันทึกว่าส่งแล้ว ⇒ scheduler จะไม่ส่งซ้ำในเดือนเดียวกัน
    """
    now = now or _now_bangkok()
    month = now.strftime("%Y-%m")
    webhook_url = (webhook_url if webhook_url is not None else _default_webhook()).strip()
    if not webhook_url:
        return "no_webhook"
    if not force:
        if now.day == 1 and now.hour < DAR_PLAN_HOUR:
            return "not_yet"
        if month in _dar_sent_in_process:
            return "already_sent"
    try:
        state = _load_state()
    except DarStateUnreadable as exc:
        logger.error("ข้ามแผน DAR-DCA — อ่านไฟล์สถานะไม่ได้: %s (ลบ/แก้ไฟล์นี้แล้วจะกลับมาทำงาน)", exc)
        return "state_unreadable"
    state = dict(state or {})
    plan_state = dict(state.get("dar_plan") or {})

    if not force:
        if not plan_state:
            if now.day > FIRST_INSTALL_SEND_WITHIN_DAYS:
                state["dar_plan"] = {"sent_month": month, "seeded_at": now.isoformat(timespec="seconds")}
                try:
                    _save_state(state)
                except OSError as exc:
                    logger.error("เขียนไฟล์สถานะ DAR ไม่ได้: %s", exc)
                _dar_sent_in_process.add(month)
                logger.info("เริ่มจำสถานะแผน DAR ที่ %s — แผนแรกจะส่งเดือนถัดไป", DAR_STATE_PATH)
                return "seeded"
        elif plan_state.get("sent_month") == month:
            _dar_sent_in_process.add(month)
            return "already_sent"

    failed = int(plan_state.get("failed_attempts") or 0) if plan_state.get("failed_month") == month else 0
    if not force and failed >= DAR_MAX_FAILED_ATTEMPTS:
        return "gave_up"

    send = send or _default_send
    error: str | None = None
    try:
        plan = (build or _default_build)()
    except Exception as exc:  # noqa: BLE001 - ทุกความล้มเหลวของการคำนวณต้องถูกนับและรายงาน
        plan, error = None, f"{type(exc).__name__}: {exc}"

    if plan is not None:
        comparison, comparison_error = None, None
        try:
            comparison = (compare or _default_compare)()
        except Exception as exc:  # noqa: BLE001 - เทียบผลพังไม่ควรกั้นแผนของเดือน
            comparison_error = f"{type(exc).__name__}: {exc}"
            logger.warning("เทียบผลพอร์ต DAR ไม่ได้: %s", comparison_error)
        title, description = format_dar_plan_message(plan, comparison, comparison_error)
        result = send(webhook_url, title, description)
        if result.get("success"):
            _dar_sent_in_process.add(month)
            state["dar_plan"] = {"sent_month": month, "sent_at": now.isoformat(timespec="seconds")}
            try:
                _save_state(state)
            except OSError as exc:
                logger.error("เขียนไฟล์สถานะ DAR ไม่ได้: %s", exc)
            return "sent"
        error = f"ส่ง Discord ไม่สำเร็จ: {result.get('error')}"

    failed += 1
    plan_state = {**plan_state, "failed_month": month, "failed_attempts": failed, "last_error": error}
    state["dar_plan"] = plan_state
    logger.error("แผน DAR-DCA เดือน %s ล้ม (ครั้งที่ %d): %s", month, failed, error)
    status = "failed"
    if failed >= DAR_MAX_FAILED_ATTEMPTS and not force:
        status = "gave_up"
        send(webhook_url, f"DAR-DCA · แผนเดือน {thai_month(month)} ส่งไม่ได้",
             f"คำนวณ/ส่งแผน DAR เดือนนี้ไม่สำเร็จ {failed} ครั้ง — หยุดลองจนถึงเดือนหน้า\nสาเหตุล่าสุด: {error}")
    try:
        _save_state(state)
    except OSError as exc:
        logger.error("เขียนไฟล์สถานะ DAR ไม่ได้: %s", exc)
    return status
