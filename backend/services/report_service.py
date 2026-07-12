from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta
from typing import Any

import requests
from sqlalchemy.orm import Session

from analysis.llm import chat_text

from ..database import SessionLocal
from ..models.orm import MonthlyReport
from ..screener.history_service import ScreenerHistoryService
from ..services import goal_service, networth_service, portfolio_service

_SYSTEM_PROMPT = (
    "คุณเป็น financial advisor สรุปภาพรวมการเงินรายเดือน "
    "เขียนแบบกระชับ อ่าน 2 นาทีจบ มี 4 หัวข้อ: "
    "1) ภาพรวมพอร์ต 2) Net Worth 3) สัญญาณน่าสนใจ 4) แนะนำเดือนหน้า "
    "ตัวเลขทั้งหมดคำนวณมาแล้ว — อธิบายเท่านั้น ห้ามคำนวณใหม่ "
    "ลงท้ายด้วย disclaimer เสมอ"
)


# ── data aggregators ─────────────────────────────────────────────────────────

def get_portfolio_summary(db: Session) -> dict[str, Any]:
    summary = portfolio_service.get_portfolio_summary()
    holdings = [h for h in portfolio_service.get_holdings() if h.get("price_ok")]
    top = sorted(holdings, key=lambda h: h["current_value_usd"] or 0, reverse=True)[:3]
    return {
        "holdings_count": summary["holdings_count"],
        "current_value_usd": summary["current_value_usd"],
        "invested_usd": summary["invested_usd"],
        "pnl_usd": summary["pnl_usd"],
        "missing_prices": summary.get("missing_prices", []),
        "top_holdings": [
            {"ticker": h["ticker"], "return_pct": h["return_pct"]}
            for h in top
        ],
    }


def get_networth_change(db: Session) -> dict[str, Any]:
    history = networth_service.get_history(db, months=3)
    if not history:
        return {"available": False, "current_net_worth_thb": 0, "change_thb": 0, "change_pct": 0}

    today_month = date.today().isoformat()[:7]
    current = history[0]
    previous = next(
        (s for s in history[1:] if s.snapshot_date[:7] < today_month), None
    )

    current_nw = current.net_worth_thb
    previous_nw = previous.net_worth_thb if previous else current_nw
    change_thb = current_nw - previous_nw
    change_pct = (change_thb / previous_nw * 100) if previous_nw else 0.0

    return {
        "available": True,
        "current_net_worth_thb": current_nw,
        "previous_net_worth_thb": previous_nw,
        "change_thb": round(change_thb, 2),
        "change_pct": round(change_pct, 2),
    }


async def get_screener_summary() -> dict[str, Any]:
    svc = ScreenerHistoryService()
    records = await svc.get_history(limit=500)

    cutoff = datetime.utcnow() - timedelta(days=30)
    monthly: list[dict] = []
    for r in records:
        ts = r.get("created_at")
        if ts is None:
            continue
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
        if isinstance(ts, datetime) and ts >= cutoff:
            monthly.append(r)

    preset_counts: dict[str, int] = {}
    for r in monthly:
        p = str(r.get("preset_name", "unknown"))
        preset_counts[p] = preset_counts.get(p, 0) + 1

    return {
        "total_signals": len(monthly),
        "symbols_with_signals": list({r["symbol"] for r in monthly}),
        "by_preset": preset_counts,
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

def generate_narrative(all_data: dict[str, Any], month: str) -> str:
    pf = all_data["portfolio"]
    nw = all_data["networth"]
    sc = all_data["screener"]
    go = all_data["goals"]

    top_txt = ", ".join(
        f"{h['ticker']} ({h['return_pct']:+.1f}%)" for h in pf["top_holdings"]
    ) or "ไม่มีข้อมูล"

    nw_txt = (
        f"Net Worth {nw['current_net_worth_thb']:,.0f} THB "
        f"(เปลี่ยนแปลง {nw['change_thb']:+,.0f} THB / {nw['change_pct']:+.1f}%)"
        if nw["available"] else "ยังไม่มีข้อมูล Net Worth"
    )

    preset_txt = ", ".join(f"{k}:{v}" for k, v in sc["by_preset"].items()) or "ไม่มี"

    missing = pf.get("missing_prices") or []
    missing_txt = (
        f"\n- ⚠️ ดึงราคาไม่ได้ (ไม่ถูกนับในมูลค่า): {', '.join(map(str, missing))}" if missing else ""
    )

    user_msg = (
        f"สรุปข้อมูลการเงินเดือน {month}\n\n"
        f"[พอร์ตโฟลิโอ]\n"
        f"- มูลค่ารวม: {pf['current_value_usd']:,.2f} USD\n"
        f"- กำไร/ขาดทุน: {pf['pnl_usd']:+,.2f} USD\n"
        f"- จำนวน ETF: {pf['holdings_count']} ตัว\n"
        f"- Top holdings: {top_txt}{missing_txt}\n\n"
        f"[Net Worth]\n"
        f"- {nw_txt}\n\n"
        f"[Screener Signals (30 วัน)]\n"
        f"- สัญญาณทั้งหมด: {sc['total_signals']}\n"
        f"- ETF ที่มีสัญญาณ: {', '.join(sc['symbols_with_signals']) or 'ไม่มี'}\n"
        f"- ตามประเภท: {preset_txt}\n\n"
        f"[เป้าหมายการออม]\n"
        f"- ทั้งหมด {go['total']} เป้าหมาย\n"
        f"- On track: {', '.join(go['on_track']) or 'ไม่มี'}\n"
        f"- Off track: {', '.join(go['off_track']) or 'ไม่มี'}\n"
    )

    try:
        return chat_text(_SYSTEM_PROMPT, user_msg, max_tokens=1600, temperature=0.3)
    except Exception as exc:
        return f"ไม่สามารถสร้าง narrative ได้: {exc}"


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


# ── orchestrator ──────────────────────────────────────────────────────────────

async def generate_and_save_report() -> dict[str, Any]:
    """Aggregate data, call Groq, persist to SQLite, send Telegram."""
    db = SessionLocal()
    try:
        month = date.today().strftime("%Y-%m")
        all_data = await _aggregate_data(db)
        content = await asyncio.to_thread(generate_narrative, all_data, month)
        sent_at = datetime.utcnow()

        existing = db.query(MonthlyReport).filter(MonthlyReport.month == month).first()
        if existing:
            existing.content = content
            existing.sent_at = sent_at
        else:
            db.add(MonthlyReport(month=month, content=content, sent_at=sent_at))
        db.commit()

        await _send_telegram(content, month)
        return {"month": month, "content": content, "sent_at": sent_at.isoformat()}
    finally:
        db.close()


# ── read helpers ──────────────────────────────────────────────────────────────

def list_reports(db: Session) -> list[MonthlyReport]:
    return db.query(MonthlyReport).order_by(MonthlyReport.month.desc()).all()


def get_report(db: Session, month: str) -> MonthlyReport | None:
    return db.query(MonthlyReport).filter(MonthlyReport.month == month).first()
