from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.orm import Session

from analysis.llm import AI_DISABLED_MESSAGE, LLMDisabledError, chat_text

from ..database import SessionLocal
from ..models.orm import MonthlyReport
from ..screener.history_service import (
    HISTORY_OFF_REASON,
    LEGACY_NAIVE_TZ,
    ScreenerHistoryService,
)
from ..services import goal_service, networth_service, portfolio_service

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "คุณเป็น financial advisor สรุปภาพรวมการเงินรายเดือน "
    "เขียนแบบกระชับ อ่าน 2 นาทีจบ มี 4 หัวข้อ: "
    "1) ภาพรวมพอร์ต 2) Net Worth 3) สัญญาณน่าสนใจ 4) แนะนำเดือนหน้า "
    "ตัวเลขทั้งหมดคำนวณมาแล้ว — อธิบายเท่านั้น ห้ามคำนวณใหม่ "
    "ลงท้ายด้วย disclaimer เสมอ"
)


# ── data aggregators ─────────────────────────────────────────────────────────

def get_portfolio_summary(db: Session) -> dict[str, Any]:
    """สรุปพอร์ตสำหรับรายงานรายเดือน.

    ต้องส่งต่อทั้ง ``missing_prices`` (ราคาที่ดึงไม่ได้) และ ``skipped_rows``
    (ธุรกรรมที่ข้อมูลไม่ครบจนถูกตัดออกจากยอดรวม) — ยอดที่น้อยกว่าจริง
    ต้องมีคำเตือนกำกับเสมอ ห้ามตัดเงียบ ๆ (FIX_PLAN ข้อ 1.2 + C2)
    """
    summary = portfolio_service.get_portfolio_summary()
    holdings = [h for h in portfolio_service.get_holdings()["holdings"] if h.get("price_ok")]
    top = sorted(holdings, key=lambda h: h["current_value_usd"] or 0, reverse=True)[:3]
    return {
        "holdings_count": summary["holdings_count"],
        "current_value_usd": summary["current_value_usd"],
        "invested_usd": summary["invested_usd"],
        "pnl_usd": summary["pnl_usd"],
        "missing_prices": summary.get("missing_prices", []),
        "skipped_rows": list(summary.get("skipped_rows") or []),
        "skipped_reason": str(summary.get("skipped_reason") or ""),
        "top_holdings": [
            {"ticker": h["ticker"], "return_pct": h["return_pct"]}
            for h in top
        ],
    }


def get_networth_change(db: Session) -> dict[str, Any]:
    """Net Worth ปัจจุบัน + ผลต่างจากเดือนก่อน (ถ้ามีจุดเทียบจริง).

    ``has_baseline=False`` แปลว่า **ไม่มีเดือนก่อนหน้าให้เทียบ** — ``change_thb``
    และ ``change_pct`` เป็น ``None`` ห้ามเป็น 0 เดิมโค้ดยัดค่าปัจจุบันเป็นฐานเทียบ
    ของตัวเองแล้วรายงาน "+0 / +0.0%" ทั้งที่ตัวเลขนั้นไม่มีอยู่จริง แล้วส่งต่อ
    เข้าพรอมป์ของ LLM ด้วย = โค้ดกุตัวเลขให้ AI อธิบาย (AUDIT_2026-08-06 M-R2)
    """
    # months=3 ครอบเดือนก่อนหน้าได้แน่ (snapshot รายเดือนอยู่ห่างกันไม่เกิน ~31 วัน)
    history = networth_service.get_history(db, months=3)
    if not history:
        return {
            "available": False,
            "has_baseline": False,
            "current_net_worth_thb": None,
            "previous_net_worth_thb": None,
            "change_thb": None,
            "change_pct": None,
        }

    today_month = date.today().isoformat()[:7]
    current = history[0]
    previous = next(
        (s for s in history[1:] if s.snapshot_date[:7] < today_month), None
    )
    current_nw = current.net_worth_thb

    if previous is None:
        return {
            "available": True,
            "has_baseline": False,
            "current_net_worth_thb": current_nw,
            "previous_net_worth_thb": None,
            "change_thb": None,
            "change_pct": None,
        }

    previous_nw = previous.net_worth_thb
    change_thb = current_nw - previous_nw
    # ฐานเป็น 0 → เปอร์เซ็นต์ไม่มีนิยาม ห้ามรายงานเป็น 0.0%
    change_pct = round(change_thb / previous_nw * 100, 2) if previous_nw else None

    return {
        "available": True,
        "has_baseline": True,
        "current_net_worth_thb": current_nw,
        "previous_net_worth_thb": previous_nw,
        "change_thb": round(change_thb, 2),
        "change_pct": change_pct,
    }


def _as_aware_utc(ts: Any) -> datetime | None:
    """แปลงค่า ``created_at`` ที่อ่านมาให้เป็น datetime แบบ aware (UTC).

    Postgres คอลัมน์ ``TIMESTAMP`` (ไร้ tz) คืน datetime ไร้ tz ซึ่งเทียบกับ cutoff
    แบบ aware ไม่ได้ — ค่าเหล่านั้นถูกเขียนด้วย ``NOW()`` ในคอนเทนเนอร์ที่ตั้ง
    ``TZ=Asia/Bangkok`` จึงต้องตีความเป็นเวลาไทย (AUDIT_2026-08-06 H3)
    คืน ``None`` เมื่อแปลงไม่ได้ — ผู้เรียกต้องนับไว้ ห้ามทิ้งเงียบ
    """
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return None
    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo(LEGACY_NAIVE_TZ))
    return ts.astimezone(UTC)


async def get_screener_summary() -> dict[str, Any]:
    """สรุปสัญญาณ screener 30 วันล่าสุด.

    ``available=False`` = **อ่านประวัติไม่ได้** (ฐานล่ม หรือไม่ได้เปิดใช้) ซึ่งคนละ
    เรื่องกับ "ไม่มีสัญญาณ" — ในกรณีนั้น ``total_signals`` เป็น ``None`` และผู้เรียก
    ต้องพิมพ์ ``unavailable_reason`` แทนตัวเลข (AUDIT_2026-08-06 M-R3)
    """
    svc = ScreenerHistoryService()
    records, status = await svc.get_history_with_status(limit=500)

    if status["status"] != "ok":
        detail = str(status.get("detail") or "").strip()
        if status["status"] == "off":
            reason = detail or HISTORY_OFF_REASON
        else:
            reason = f"อ่านประวัติ screener ไม่ได้: {detail[:200] or 'ไม่ทราบสาเหตุ'}"
        return {
            "available": False,
            "unavailable_reason": reason,
            "total_signals": None,
            "symbols_with_signals": [],
            "by_preset": {},
            "undated_records": 0,
        }

    cutoff = datetime.now(UTC) - timedelta(days=30)
    monthly: list[dict] = []
    undated = 0
    for r in records:
        ts = _as_aware_utc(r.get("created_at"))
        if ts is None:
            undated += 1
            continue
        if ts >= cutoff:
            monthly.append(r)

    preset_counts: dict[str, int] = {}
    for r in monthly:
        p = str(r.get("preset_name", "unknown"))
        preset_counts[p] = preset_counts.get(p, 0) + 1

    return {
        "available": True,
        "unavailable_reason": "",
        "total_signals": len(monthly),
        "symbols_with_signals": list({r["symbol"] for r in monthly}),
        "by_preset": preset_counts,
        # แถวที่วันที่อ่านไม่ออกถูกตัดออกจากยอด — ต้องรายงาน ห้ามหายเงียบ
        "undated_records": undated,
    }


def get_goals_summary(db: Session) -> dict[str, Any]:
    goals = goal_service.list_goals(db)
    on_track, off_track = [], []
    for goal in goals:
        progress = goal_service._build_progress(goal)
        (on_track if progress["on_track"] else off_track).append(goal.name)
    return {"total": len(goals), "on_track": on_track, "off_track": off_track}


async def _aggregate_data(db: Session) -> dict[str, Any]:
    screener = await get_screener_summary()
    return {
        "portfolio": get_portfolio_summary(db),
        "networth": get_networth_change(db),
        "screener": screener,
        "goals": get_goals_summary(db),
    }


# ── narrative ─────────────────────────────────────────────────────────────────

def _holding_txt(h: dict[str, Any]) -> str:
    """ชื่อกอง + ผลตอบแทน — ``return_pct=None`` คือ "ไม่รู้" ไม่ใช่ 0%.

    ``portfolio_service._clean()`` ตั้งใจคืน ``None`` เมื่อคำนวณผลตอบแทนไม่ได้
    (ต้นทุนรวม = 0 → inf) การ format ตรง ๆ ระเบิดเป็น ``TypeError`` (M-R4)
    """
    ret = h.get("return_pct")
    if ret is None:
        return f"{h['ticker']} (ไม่มีข้อมูลผลตอบแทน)"
    return f"{h['ticker']} ({ret:+.1f}%)"


_PRICE_UNAVAILABLE = "ดึงราคาไม่ได้"


def _usd_txt(value: Any, *, signed: bool = False, unknown: str = _PRICE_UNAVAILABLE) -> str:
    """จำนวนเงิน USD ที่อาจเป็น ``None`` — "ไม่รู้" ต้องอ่านออก ห้ามเป็น ``0.00``.

    ``portfolio_service.get_portfolio_summary()`` **ตั้งใจ** คืน ``None`` ให้
    ``current_value_usd``/``pnl_usd`` เมื่อดึงราคาไม่ได้สักกอง (AUDIT_2026-08-06 H9)
    การ format ตรง ๆ ด้วย ``:,.2f`` ระเบิดเป็น ``TypeError`` ทั้งเส้นทาง AI และ
    เส้นทางฟรี ⇒ รายงานรายเดือนสร้างไม่ได้เลย (K1) และการยัด ``or 0`` แทนก็ผิด
    เท่ากัน เพราะกลายเป็นรายงานว่าพอร์ตมีมูลค่า 0 บาท
    """
    if value is None:
        return unknown
    return f"{value:+,.2f} USD" if signed else f"{value:,.2f} USD"


def _prices_unknown(pf: dict[str, Any]) -> bool:
    """มูลค่า/กำไรของพอร์ตยัง "ไม่รู้" หรือไม่ (คนละเรื่องกับสมุดว่างที่เป็น 0 จริง)."""
    return pf.get("current_value_usd") is None or pf.get("pnl_usd") is None


def _networth_txt(nw: dict[str, Any], *, prefix: str, unit: str) -> str:
    """บรรทัด Net Worth — ไม่มีจุดเทียบต้องเขียนว่าไม่มี ห้ามพิมพ์ +0 / +0.0%.

    ค่าที่อ่านไม่ได้ (``None`` ทั้งที่ ``available=True``) ต้องเขียนว่าอ่านไม่ได้
    เช่นกัน — format ตรง ๆ จะระเบิดและฆ่ารายงานทั้งฉบับ (K1)
    """
    if not nw.get("available"):
        return "ยังไม่มีข้อมูล Net Worth"
    current = nw.get("current_net_worth_thb")
    if current is None:
        return f"⚠️ {prefix} อ่านค่าไม่ได้"
    head = f"{prefix} {current:,.0f} {unit}"
    if not nw.get("has_baseline"):
        return f"{head} (ยังไม่มีเดือนก่อนหน้าให้เทียบ)"
    change = nw.get("change_thb")
    if change is None:
        return f"{head} (⚠️ ผลต่างจากเดือนก่อนอ่านค่าไม่ได้)"
    pct = nw.get("change_pct")
    pct_txt = f"{pct:+.1f}%" if pct is not None else "เทียบเป็น % ไม่ได้ (ฐานเป็น 0)"
    return f"{head} ({change:+,.0f} / {pct_txt})"


def _screener_txt(sc: dict[str, Any]) -> str:
    """บรรทัดสัญญาณ screener — "อ่านไม่ได้" ต้องไม่ถูกเขียนเป็น "0 รายการ"."""
    if not sc.get("available", True):
        reason = sc.get("unavailable_reason") or "ไม่ทราบสาเหตุ"
        return f"⚠️ สัญญาณ screener 30 วัน: อ่านประวัติไม่ได้ — {reason}"
    line = f"สัญญาณ screener 30 วัน: {sc['total_signals']} รายการ"
    if sc.get("undated_records"):
        line += f" (⚠️ อีก {sc['undated_records']} แถววันที่อ่านไม่ออก ไม่ถูกนับ)"
    return line


def _plain_narrative(
    all_data: dict[str, Any], month: str, note: str | None = None
) -> str:
    """รายงานแบบไม่ใช้ AI — ตัวเลขเดียวกัน ไม่มีค่าใช้จ่าย.

    ``note`` แทนข้อความ "AI ปิดอยู่" เมื่อเหตุผลที่ไม่ได้ใช้ AI ไม่ใช่การปิดเพื่อ
    คุมค่าใช้จ่าย (เช่น เรียกโมเดลแล้วล้มเหลว — M-R1)
    """
    pf = all_data["portfolio"]
    nw = all_data["networth"]
    sc = all_data["screener"]
    go = all_data["goals"]

    lines = [f"📊 สรุปการเงินเดือน {month} (จากโมเดล — ไม่ใช้ AI)", ""]
    lines.append(
        f"พอร์ต: {pf['holdings_count']} ETF | มูลค่า {_usd_txt(pf['current_value_usd'])} | "
        f"กำไร/ขาดทุน {_usd_txt(pf['pnl_usd'], signed=True)}"
    )
    if _prices_unknown(pf):
        # เงินที่จ่ายไปแล้วยังรู้อยู่เสมอ — ไม่ต้องพึ่งราคาปัจจุบัน จึงยังบอกผู้ใช้ได้
        invested = _usd_txt(pf.get("invested_usd"), unknown="ไม่ทราบ")
        lines.append(
            f"⚠️ มูลค่า/กำไรขาดทุนยังไม่รู้ เพราะดึงราคาปัจจุบันไม่ได้ (ไม่ใช่ 0) — "
            f"เงินที่ลงทุนไปแล้ว {invested}"
        )
    if pf.get("missing_prices"):
        lines.append(f"⚠️ ดึงราคาไม่ได้: {', '.join(map(str, pf['missing_prices']))}")
    # ธุรกรรมที่ถูกตัดออกจากยอดรวมต้องปรากฏในรายงานที่ส่งถึงผู้ใช้ด้วย
    if pf.get("skipped_reason"):
        lines.append(f"⚠️ {pf['skipped_reason']}")
    if nw.get("available"):
        lines.append(_networth_txt(nw, prefix="Net Worth:", unit="บาท"))
    lines.append(_screener_txt(sc))
    if go["off_track"]:
        lines.append(f"เป้าหมายที่ยังไม่เข้าเป้า: {', '.join(go['off_track'])}")
    lines.append("")
    lines.append(note or AI_DISABLED_MESSAGE)
    lines.append("")
    lines.append("ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน")
    return "\n".join(lines)


def _build_prompt(all_data: dict[str, Any], month: str) -> str:
    """ประกอบพรอมป์จากตัวเลขที่คำนวณเสร็จแล้ว — AI อธิบายอย่างเดียว."""
    pf = all_data["portfolio"]
    nw = all_data["networth"]
    sc = all_data["screener"]
    go = all_data["goals"]

    top_txt = ", ".join(_holding_txt(h) for h in pf["top_holdings"]) or "ไม่มีข้อมูล"
    nw_txt = _networth_txt(nw, prefix="Net Worth", unit="THB")

    if sc.get("available", True):
        preset_txt = ", ".join(f"{k}:{v}" for k, v in sc["by_preset"].items()) or "ไม่มี"
        screener_block = (
            f"- สัญญาณทั้งหมด: {sc['total_signals']}\n"
            f"- ETF ที่มีสัญญาณ: {', '.join(sc['symbols_with_signals']) or 'ไม่มี'}\n"
            f"- ตามประเภท: {preset_txt}\n"
        )
        if sc.get("undated_records"):
            screener_block += (
                f"- ⚠️ อีก {sc['undated_records']} แถววันที่อ่านไม่ออก ไม่ถูกนับในยอดข้างบน\n"
            )
    else:
        # ห้ามป้อนเลข 0 ให้ AI เมื่อยังไม่รู้ว่ามีกี่รายการ (M-R3)
        screener_block = (
            f"- ⚠️ อ่านประวัติ screener ไม่ได้ ({sc.get('unavailable_reason') or 'ไม่ทราบสาเหตุ'})"
            " — ห้ามสรุปว่าไม่มีสัญญาณ ให้บอกผู้ใช้ว่าข้อมูลส่วนนี้ยังไม่รู้\n"
        )

    missing = pf.get("missing_prices") or []
    missing_txt = (
        f"\n- ⚠️ ดึงราคาไม่ได้ (ไม่ถูกนับในมูลค่า): {', '.join(map(str, missing))}" if missing else ""
    )
    # AI อธิบายอย่างเดียว แต่ต้องได้รู้ว่าตัวเลขข้างบนไม่ครบเพราะอะไร
    skipped_reason = pf.get("skipped_reason") or ""
    missing_txt += f"\n- ⚠️ {skipped_reason}" if skipped_reason else ""
    if _prices_unknown(pf):
        # ห้ามป้อนเลข 0 ให้ AI เมื่อยังไม่รู้มูลค่าจริง (หลักการเดียวกับ M-R3)
        missing_txt += (
            f"\n- ⚠️ ดึงราคาปัจจุบันไม่ได้ มูลค่า/กำไรขาดทุนจึงยังไม่รู้"
            f" (เงินที่ลงทุนไปแล้ว {_usd_txt(pf.get('invested_usd'), unknown='ไม่ทราบ')})"
            " — ห้ามสรุปว่าเป็น 0 หรือเดาตัวเลข ให้บอกผู้ใช้ว่าข้อมูลส่วนนี้ยังไม่รู้"
        )

    return (
        f"สรุปข้อมูลการเงินเดือน {month}\n\n"
        f"[พอร์ตโฟลิโอ]\n"
        f"- มูลค่ารวม: {_usd_txt(pf['current_value_usd'])}\n"
        f"- กำไร/ขาดทุน: {_usd_txt(pf['pnl_usd'], signed=True)}\n"
        f"- จำนวน ETF: {pf['holdings_count']} ตัว\n"
        f"- Top holdings: {top_txt}{missing_txt}\n\n"
        f"[Net Worth]\n"
        f"- {nw_txt}\n\n"
        f"[Screener Signals (30 วัน)]\n"
        f"{screener_block}\n"
        f"[เป้าหมายการออม]\n"
        f"- ทั้งหมด {go['total']} เป้าหมาย\n"
        f"- On track: {', '.join(go['on_track']) or 'ไม่มี'}\n"
        f"- Off track: {', '.join(go['off_track']) or 'ไม่มี'}\n"
    )


def generate_narrative_with_source(
    all_data: dict[str, Any], month: str, user_initiated: bool = False
) -> tuple[str, str]:
    """คืน ``(เนื้อรายงาน, "ai"|"plain")``.

    การประกอบพรอมป์อยู่ใน ``try`` เดียวกับ LLM โดยตั้งใจ — เดิม ``top_txt``/``nw_txt``
    ถูกประกอบก่อน try จึงทำให้ ``TypeError`` (M-R4) ฆ่าแม้แต่เส้นทางที่ไม่ใช้ AI
    """
    try:
        user_msg = _build_prompt(all_data, month)
        return chat_text(
            _SYSTEM_PROMPT,
            user_msg,
            max_tokens=1600,
            temperature=0.3,
            user_initiated=user_initiated,
        ), "ai"
    except LLMDisabledError:
        # งานอัตโนมัติ (cron วันที่ 1) — ส่งรายงานจากตัวเลขแทน ไม่มีค่าใช้จ่าย
        return _plain_narrative(all_data, month), "plain"
    except Exception as exc:
        # คีย์ผิด / 529 overloaded / โควตาหมด ไม่ใช่ LLMDisabledError — เดิมคืนสตริง
        # error เปล่า ๆ เป็นตัวรายงาน แล้วทับรายงานเดิมทิ้ง (M-R1)
        logger.warning("[report_service] AI เขียนบทสรุปไม่สำเร็จ: %s", exc)
        note = (
            f"⚠️ AI เขียนบทสรุปไม่สำเร็จ: {exc} — "
            "ตัวเลขด้านบนมาจากโมเดลในระบบตามปกติ"
        )
        return _plain_narrative(all_data, month, note=note), "plain"


def generate_narrative(
    all_data: dict[str, Any], month: str, user_initiated: bool = False
) -> str:
    return generate_narrative_with_source(all_data, month, user_initiated)[0]


# ── Telegram ──────────────────────────────────────────────────────────────────

async def _send_telegram(content: str, month: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

    text = f"📊 *Vaultis Monthly Report — {month}*\n\n{content}"
    if len(text) > 4096:
        text = text[:4090] + "…"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        await asyncio.to_thread(
            requests.post, url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
    except Exception as exc:
        print(f"[report_service] send_telegram error: {exc}")


# ── schema ────────────────────────────────────────────────────────────────────

_SCHEMA_CHECKED: set[str] = set()


def _ensure_report_schema(db: Session) -> None:
    """เติมคอลัมน์ ``source`` ให้ฐานที่สร้างไว้ก่อนมีคอลัมน์นี้.

    ``Base.metadata.create_all`` สร้างตารางใหม่ให้เท่านั้น ไม่ ALTER ตารางเดิม —
    ฐาน ``vaultis.db`` ของผู้ใช้ที่มีอยู่แล้วจะถูก SELECT คอลัมน์ที่ไม่มีจริงแล้วพัง
    แถวเก่าได้ ``plain`` เพราะไม่มีหลักฐานว่าเป็นรายงานที่ AI เขียน
    """
    bind = db.get_bind()
    key = str(getattr(bind, "url", bind))
    if key in _SCHEMA_CHECKED:
        return

    table = MonthlyReport.__tablename__
    inspector = sa_inspect(bind)
    if inspector.has_table(table):
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "source" not in columns:
            logger.info("[report_service] เติมคอลัมน์ source ให้ตาราง %s", table)
            db.execute(
                text(f"ALTER TABLE {table} ADD COLUMN source VARCHAR NOT NULL DEFAULT 'plain'")
            )
            db.commit()
    _SCHEMA_CHECKED.add(key)


# ── orchestrator ──────────────────────────────────────────────────────────────

async def generate_and_save_report(user_initiated: bool = False) -> dict[str, Any]:
    """รวบรวมข้อมูล → สร้างรายงาน → บันทึก SQLite → ส่ง Telegram.

    เรียกจาก cron (วันที่ 1) จะไม่ใช้ AI (ไม่มีค่าใช้จ่าย) — ได้รายงานจากตัวเลขแทน

    คืน ``saved=False`` เมื่อเดือนนั้นมีรายงานที่ **AI เขียนไว้แล้ว** และรอบนี้เป็น
    รายงานจากตัวเลข — รายงานยังถูกส่ง แต่ไม่ทับของเดิมที่ผู้ใช้จ่ายเงินไปแล้ว (M-R1)
    ความล้มเหลวถูก log + แจ้งเตือนก่อนโยนต่อ เพื่อไม่ให้ cron ตายเงียบ (H3)
    """
    month = date.today().strftime("%Y-%m")
    db: Session | None = None
    try:
        db = SessionLocal()
        _ensure_report_schema(db)
        all_data = await _aggregate_data(db)
        content, source = await asyncio.to_thread(
            generate_narrative_with_source, all_data, month, user_initiated
        )
        sent_at = datetime.now(UTC)

        existing = db.query(MonthlyReport).filter(MonthlyReport.month == month).first()
        saved = True
        note = ""
        if existing is None:
            db.add(MonthlyReport(month=month, content=content, sent_at=sent_at, source=source))
        elif existing.source == "ai" and source != "ai":
            saved = False
            note = (
                f"ไม่บันทึกทับรายงานเดือน {month} ที่ AI เขียนไว้แล้ว — "
                "รายงานฉบับนี้ถูกส่งอย่างเดียว"
            )
            logger.info("[report_service] %s", note)
        else:
            existing.content = content
            existing.sent_at = sent_at
            existing.source = source
        db.commit()

        await _send_telegram(content, month)
        return {
            "month": month,
            "content": content,
            "sent_at": sent_at.isoformat(),
            "source": source,
            "saved": saved,
            "note": note,
        }
    except Exception as exc:
        # cron วันที่ 1 (APScheduler) กลืน exception ไว้ในล็อกของตัวเอง ผู้ใช้ไม่มีทางรู้
        # ว่าเดือนนั้นไม่มีรายงาน — ต้องแจ้งออกไปก่อน แล้วค่อยโยนต่อให้ผู้เรียก (API = 500)
        logger.exception("[report_service] สร้างรายงานเดือน %s ไม่สำเร็จ", month)
        try:
            await _send_telegram(f"⚠️ สร้างรายงานรายเดือนไม่สำเร็จ: {exc}", month)
        except Exception:  # การแจ้งเตือนล้มเหลวต้องไม่กลบสาเหตุจริง
            logger.exception("[report_service] แจ้งเตือนความล้มเหลวไม่สำเร็จ")
        raise
    finally:
        if db is not None:
            db.close()


# ── read helpers ──────────────────────────────────────────────────────────────

def list_reports(db: Session) -> list[MonthlyReport]:
    _ensure_report_schema(db)
    return db.query(MonthlyReport).order_by(MonthlyReport.month.desc()).all()


def get_report(db: Session, month: str) -> MonthlyReport | None:
    _ensure_report_schema(db)
    return db.query(MonthlyReport).filter(MonthlyReport.month == month).first()
