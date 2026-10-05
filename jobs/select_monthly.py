# -*- coding: utf-8 -*-
"""ส่งแผน SELECT-DCA รายเดือนเข้า Discord — งานของ **พอร์ต SELECT แยก** (โหมด "โมเดลเลือกกองเอง").

รูปแบบเดียวกับ ``jobs/dar_monthly.py`` (ไฟล์สถานะของตัวเอง ``VAULTIS_SELECT_STATE_PATH`` · ตัวกันส่งซ้ำ · ไม่ใช้ LLM) แต่ต่างสองข้อ:

* **ปิดไว้ก่อน (opt-in):** ส่งเฉพาะเมื่อ ``config.json`` → ``select.discord_enabled = true`` (เปิดที่หน้า Settings) — มติผู้ใช้ 2026-10-05:
  ข้อความ "ซื้อกองนี้กี่บาท" ที่ออกไปนอกเครื่องต้องเป็นการตัดสินใจของผู้ใช้หลังตรวจรายชื่อกองบน Dime แล้ว ไม่ใช่ค่าเริ่มต้น
* **ข้อความต้องพกสถานะหลักฐานไปด้วยเสมอ** (ผ่านเกณฑ์ที่ล็อกไว้ใน JST แต่ยังไม่ใช่หลักฐานว่าใช้เงินจริงได้) — ``analysis.select_dca.evidence_summary``

ถึงเวลา = ตั้งแต่ 08:00 วันที่ 1 จนสิ้นเดือน (ส่งย้อนหลังได้) · ติดตั้ง/เปิดครั้งแรกภายใน 7 วันแรกของเดือน = ส่งเดือนนี้เลย หลังจากนั้นเริ่มเดือนหน้า ·
เฉพาะ Discord ตอบสำเร็จถึงนับว่าส่งแล้ว · ล้ม ``SELECT_MAX_FAILED_ATTEMPTS`` ครั้ง = หยุดถึงเดือนหน้าพร้อมแจ้ง · ไฟล์สถานะอ่านไม่ออก = **ไม่ส่ง** แล้ว log ERROR
path สถานะอ่านจาก env **ครั้งเดียวตอน import** (เทสต์ monkeypatch ``SELECT_STATE_PATH``)
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
SELECT_STATE_PATH = Path(os.getenv("VAULTIS_SELECT_STATE_PATH") or REPO_ROOT / ".select_scheduler_state.json")
SELECT_PLAN_HOUR = 8
SELECT_MAX_FAILED_ATTEMPTS = 3
FIRST_INSTALL_SEND_WITHIN_DAYS = 7
EMBED_COLOR = 0xE67E22

_select_sent_in_process: set[str] = set()
_THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


class SelectStateUnreadable(RuntimeError):
    """ไฟล์สถานะมีอยู่แต่อ่านไม่ออก — ไม่รู้ว่าส่งแล้วหรือยัง ห้ามเดา."""


def _now_bangkok() -> datetime:
    return datetime.now(ZoneInfo("Asia/Bangkok"))


def _load_state() -> dict[str, Any] | None:
    try:
        raw = SELECT_STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SelectStateUnreadable(f"{SELECT_STATE_PATH}: JSON เสีย ({exc})") from exc
    if not isinstance(data, dict):
        raise SelectStateUnreadable(f"{SELECT_STATE_PATH}: ไม่ใช่ JSON object")
    return data


def _save_state(state: dict[str, Any]) -> None:
    SELECT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SELECT_STATE_PATH.with_name(SELECT_STATE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SELECT_STATE_PATH)


def thai_month(plan_month: str) -> str:
    year, month = plan_month.split("-")
    return f"{_THAI_MONTHS[int(month) - 1]} {year}"


def _fmt_units(units: float | None) -> str:
    return "≈ ? หน่วย (ไม่มีราคา)" if units is None else f"≈ {units:,.4f} หน่วย"


def format_select_plan_message(plan: Any, comparison: dict[str, Any] | None = None, comparison_error: str | None = None,
                               evidence: dict[str, Any] | None = None, evidence_error: str | None = None) -> tuple[str, str]:
    """(หัวข้อ, เนื้อความ) — ซื้อกองไหนกี่บาท เพราะอะไร + สถานะหลักฐาน + ข้อควรระวัง (ห้ามส่งแผนโดยไม่มีสองอย่างหลัง)."""
    title = f"SELECT-DCA · แผนเดือน {thai_month(plan.plan_month)}"
    n = len(plan.universe)
    lines = [f"**โมเดลเลือก {len(plan.lines)} จาก {n} ตลาด — ซื้อเดือนนี้ รวม {plan.total_thb:,} บาท**"]
    for ln in sorted(plan.lines, key=lambda x: (x.rank, x.ticker)):
        price = f"@ ${ln.price_usd:,.2f}" if ln.price_usd else "@ ราคาไม่ทราบ"
        why = f"อันดับ {ln.rank} ปันผล {ln.yield_pct:.2f}%" if ln.yield_pct is not None else f"อันดับ {ln.rank}"
        lines.append(f"• **{ln.ticker}** ({ln.name_th}) — {ln.amount_thb:,} บาท · {_fmt_units(ln.units)} {price} · {why}")
    if plan.unallocated_thb >= 1:
        lines.append(f"• ยังไม่จัดสรร (เศษจากการปัดหลักร้อย): {plan.unallocated_thb:,.0f} บาท")
    lines.append("")
    lines.append(f"กฎ: จัดอันดับ ณ {plan.asof} ด้วย dividend yield 12 เดือน (คงเดิมทั้งปี) · ตลาดที่มูลค่า ≥ 25% ของพอร์ตไม่ซื้อเพิ่ม")
    for note in plan.notes:
        lines.append(f"ℹ️ {note}")
    if plan.skipped:
        lines.append("ยังไม่อยู่ในจักรวาล: " + ", ".join(f"{k} ({v})" for k, v in plan.skipped.items()))
    if plan.alt_rule_top:
        lines.append("กฎ DAR top-5 (เทียบเฉย ๆ ไม่ได้ใช้ซื้อ) เลือก: " + ", ".join(plan.alt_rule_top))
    fx_note = "สด" if plan.fx_is_live else "⚠️ ค่าสำรองจาก config — จำนวนหน่วยอาจคลาดเคลื่อน"
    lines.append(f"ข้อมูลราคาถึง {plan.data_through} · อัตราแลกเปลี่ยน {plan.fx_rate:.2f} บาท/ดอลลาร์ ({fx_note})")
    if comparison and comparison.get("rows_used"):
        diff, pct = comparison.get("diff_thb"), comparison.get("diff_pct_of_invested")
        lines.append("")
        lines.append(f"พอร์ต SELECT: ลงไป {comparison['invested_thb']:,.0f} บาท · มูลค่า (รวมปันผล) {comparison['select_value_thb']:,.0f} บาท")
        if diff is not None and pct is not None:
            lines.append(f"เทียบพอร์ตเงาแบ่งเท่ากันทั้งจักรวาล (เงินเข้าเท่ากัน): {diff:+,.0f} บาท ({pct:+.2f}% ของเงินที่ลง)")
    elif comparison_error:
        lines.append("")
        lines.append(f"⚠️ เทียบผลพอร์ต SELECT ไม่ได้รอบนี้: {comparison_error}")
    lines.append("")
    if evidence:
        b = evidence["rows"]["yield_topk_K5"]
        lines.append(
            f"📊 หลักฐาน: กฎนี้ผ่านเกณฑ์ที่ล็อกไว้ก่อนรันบนตลาดรายประเทศ 1887–1974 (JST: เฉลี่ย {b['mean_pct']:+.1f}% ชนะ {b['win_pct']:.0f}% "
            f"p10 {b['p10_pct']:+.1f}% เทียบแบ่งเท่ากัน) **แต่ไม่ใช่หลักฐานว่าใช้เงินจริงได้**:")
        for cav in evidence["caveats"][:4]:
            lines.append(f"  – {cav}")
    else:
        lines.append(f"⚠️ แสดงสถานะหลักฐานไม่ได้ ({evidence_error}) — อย่าอ่านว่าแผนนี้ผ่านการยืนยัน")
    lines.append("")
    lines.append("⚠️ ตรวจก่อนซื้อว่า Dime ขายกองเหล่านี้จริง · โหมดทดลอง แยกจากแผนหลัก (blend) และพอร์ต DAR · บันทึกการซื้อ/ดูผลที่หน้า SELECT-DCA ใน dashboard")
    return title, "\n".join(lines)


# ---------------------------------------------------------------- ตัวต่อกับของจริง (เทสต์สตับตรงนี้)
def _default_enabled() -> bool:
    from utils.config import load_config  # noqa: PLC0415

    return bool(load_config().get("select", {}).get("discord_enabled", False))


def _default_build() -> Any:
    from analysis.select_dca import build_select_plan_live  # noqa: PLC0415

    return build_select_plan_live(_now_bangkok())


def _default_compare() -> dict[str, Any] | None:
    from analysis.select_dca import compare_live  # noqa: PLC0415

    return compare_live(_now_bangkok())


def _default_evidence() -> dict[str, Any]:
    from analysis.select_dca import evidence_summary  # noqa: PLC0415

    return evidence_summary()


def _default_send(webhook_url: str, title: str, description: str) -> dict[str, Any]:
    from alerts.notifier import send_discord_webhook  # noqa: PLC0415

    return send_discord_webhook(webhook_url, title, description, embed_color=EMBED_COLOR)


def _default_webhook() -> str:
    from utils.config import load_config  # noqa: PLC0415

    return str(load_config()["notifications"].get("discord_webhook_url", "")).strip()


def run_select_plan_if_due(
    webhook_url: str | None = None,
    *,
    now: datetime | None = None,
    force: bool = False,
    enabled: Callable[[], bool] | None = None,
    build: Callable[[], Any] | None = None,
    compare: Callable[[], dict[str, Any] | None] | None = None,
    evidence: Callable[[], dict[str, Any]] | None = None,
    send: Callable[[str, str, str], dict[str, Any]] | None = None,
) -> str:
    """ส่งแผน SELECT ของเดือนนี้ถ้า **เปิดไว้** ถึงเวลา และยังไม่เคยส่ง — เรียกซ้ำได้ปลอดภัย.

    สถานะ: ``disabled`` · ``no_webhook`` · ``not_yet`` · ``already_sent`` · ``seeded`` · ``sent`` · ``failed`` · ``gave_up`` ·
    ``state_unreadable`` · ``force=True`` (``--job select_plan``) ข้ามการเช็คเวลา/สถานะ (แต่ **ไม่ข้ามสวิตช์ที่ปิดอยู่**)
    """
    now = now or _now_bangkok()
    month = now.strftime("%Y-%m")
    if not (enabled or _default_enabled)():
        return "disabled"
    webhook_url = (webhook_url if webhook_url is not None else _default_webhook()).strip()
    if not webhook_url:
        return "no_webhook"
    if not force:
        if now.day == 1 and now.hour < SELECT_PLAN_HOUR:
            return "not_yet"
        if month in _select_sent_in_process:
            return "already_sent"
    try:
        state = _load_state()
    except SelectStateUnreadable as exc:
        logger.error("ข้ามแผน SELECT-DCA — อ่านไฟล์สถานะไม่ได้: %s (ลบ/แก้ไฟล์นี้แล้วจะกลับมาทำงาน)", exc)
        return "state_unreadable"
    state = dict(state or {})
    plan_state = dict(state.get("select_plan") or {})

    if not force:
        if not plan_state:
            if now.day > FIRST_INSTALL_SEND_WITHIN_DAYS:
                state["select_plan"] = {"sent_month": month, "seeded_at": now.isoformat(timespec="seconds")}
                try:
                    _save_state(state)
                except OSError as exc:
                    logger.error("เขียนไฟล์สถานะ SELECT ไม่ได้: %s", exc)
                _select_sent_in_process.add(month)
                logger.info("เริ่มจำสถานะแผน SELECT ที่ %s — แผนแรกจะส่งเดือนถัดไป", SELECT_STATE_PATH)
                return "seeded"
        elif plan_state.get("sent_month") == month:
            _select_sent_in_process.add(month)
            return "already_sent"

    failed = int(plan_state.get("failed_attempts") or 0) if plan_state.get("failed_month") == month else 0
    if not force and failed >= SELECT_MAX_FAILED_ATTEMPTS:
        return "gave_up"

    send = send or _default_send
    error: str | None = None
    try:
        plan = (build or _default_build)()
    except Exception as exc:  # noqa: BLE001 - ทุกความล้มเหลวของการคำนวณต้องถูกนับและรายงาน
        plan, error = None, f"{type(exc).__name__}: {exc}"

    if plan is not None:
        comparison, comparison_error, ev, ev_error = None, None, None, None
        try:
            comparison = (compare or _default_compare)()
        except Exception as exc:  # noqa: BLE001 - เทียบผลพังไม่ควรกั้นแผนของเดือน
            comparison_error = f"{type(exc).__name__}: {exc}"
            logger.warning("เทียบผลพอร์ต SELECT ไม่ได้: %s", comparison_error)
        try:
            ev = (evidence or _default_evidence)()
        except Exception as exc:  # noqa: BLE001 - ขาดหลักฐาน = บอกในข้อความ ไม่ใช่ส่งแผนเงียบ ๆ หรือไม่ส่งเลย
            ev_error = f"{type(exc).__name__}: {exc}"
        title, description = format_select_plan_message(plan, comparison, comparison_error, ev, ev_error)
        result = send(webhook_url, title, description[:3900])
        if result.get("success"):
            _select_sent_in_process.add(month)
            state["select_plan"] = {"sent_month": month, "sent_at": now.isoformat(timespec="seconds")}
            try:
                _save_state(state)
            except OSError as exc:
                logger.error("เขียนไฟล์สถานะ SELECT ไม่ได้: %s", exc)
            return "sent"
        error = f"ส่ง Discord ไม่สำเร็จ: {result.get('error')}"

    failed += 1
    state["select_plan"] = {**plan_state, "failed_month": month, "failed_attempts": failed, "last_error": error}
    logger.error("แผน SELECT-DCA เดือน %s ล้ม (ครั้งที่ %d): %s", month, failed, error)
    status = "failed"
    if failed >= SELECT_MAX_FAILED_ATTEMPTS and not force:
        status = "gave_up"
        send(webhook_url, f"SELECT-DCA · แผนเดือน {thai_month(month)} ส่งไม่ได้",
             f"คำนวณ/ส่งแผน SELECT เดือนนี้ไม่สำเร็จ {failed} ครั้ง — หยุดลองจนถึงเดือนหน้า\nสาเหตุล่าสุด: {error}")
    try:
        _save_state(state)
    except OSError as exc:
        logger.error("เขียนไฟล์สถานะ SELECT ไม่ได้: %s", exc)
    return status
