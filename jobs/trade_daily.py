# -*- coding: utf-8 -*-
"""งานรายวันของโหมด TRADE (หุ้น 30 ตัว): ดึงราคา → ทำนาย (พรุ่งนี้/1 สัปดาห์/1 เดือน) → บันทึก → ให้คะแนน → เล่นซ้ำบัญชีกระดาษ → จำลองเกณฑ์ → เก็บสถานะ.

รันโดย scheduler ทุกวัน 06:35 (หลังตลาดสหรัฐปิด) และตอนเริ่มโปรเซส · ``python main.py --job trade_daily`` บังคับจำลองใหม่ ·
ไม่ต้องใช้ webhook ไม่มี LLM ไม่มีค่าใช้จ่าย · **ไม่โยน exception** (ตัวตั้งเวลาต้องเดินต่อ): ล้มเหลว = log ERROR + ``ok=False`` พร้อมเหตุผล ·
สถานะ (ป้าย "วันนี้ควรซื้อ/ขาย/ถือ" ต่อบัญชี + คำทำนายพรุ่งนี้) เก็บที่ ``simulation/data/trade_state.json`` — สร้างใหม่ได้เสมอจากสมุด + ราคา
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)
STATE_FILE = "trade_state.json"


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="seconds")


def load_state(directory: Path | None = None) -> dict[str, Any] | None:
    from simulation import data as sim_data

    p = Path(directory or sim_data.DATA_DIR) / STATE_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_state(state: dict[str, Any]) -> None:
    from simulation import data as sim_data

    d = Path(sim_data.DATA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    os.replace(tmp, d / STATE_FILE)


def _tomorrow_table(preds: list[Any]) -> list[dict[str, Any]]:
    """คำทำนายพรุ่งนี้ (1d) ต่อหุ้น: ทิศของแต่ละตัวทำนาย + เสียงข้างมาก."""
    rows: dict[str, dict[str, Any]] = {}
    for p in preds:
        if p.horizon != "1d":
            continue
        rows.setdefault(p.ticker, {"ticker": p.ticker, "price_usd": p.price_usd})[p.predictor] = int(p.direction)
    return sorted(rows.values(), key=lambda r: r["ticker"])


def run_trade_daily(force: bool = False, now: datetime | None = None) -> dict[str, Any]:
    from analysis import trade_lab as tl
    from portfolio import trade_ledger as ledger
    from simulation import trade_sim

    steps: list[str] = []
    try:
        open_, close, failed = tl.fetch_ohlc()
        preds, skipped, bar = tl.make_predictions(close, failed=failed)
        n = ledger.record_predictions(preds, bar)
        steps.append(f"บันทึกคำทำนายชุด {bar} จำนวน {n} แถว" if n else f"ชุด {bar} บันทึกไว้แล้ว (ไม่เขียนซ้ำ)")
        log = ledger.load_log()
        today = pd.Timestamp(close["VOO"].dropna().index.max())
        resolved = tl.resolve(log, close, today)
        scored = tl.score_all(resolved)
        replay = tl.replay_accounts(log, open_, close)
        verdicts = tl.account_verdicts(replay)
        sim = trade_sim.load()
        age = trade_sim.age_days(sim, now)
        if force or sim is None or age is None or age > trade_sim.MAX_AGE_DAYS:
            sim = trade_sim.simulate_gate(close)
            trade_sim.save(sim)
            steps.append(f"จำลองเกณฑ์ตัดสิน ({sim['paths']:,} เส้นทาง/โลก)")
        accounts = {}
        for name, acc in replay["accounts"].items():
            nav = acc["nav"]
            accounts[name] = {"pending": acc["pending"], "positions": acc["positions"], "n_trades": acc["n_trades"], "fees_paid_usd": acc["fees_paid_usd"],
                              "unpriceable": acc["unpriceable"], "nav_usd": None if nav.empty or pd.isna(nav.iloc[-1]) else float(nav.iloc[-1]),
                              "nav_start_usd": None if nav.empty else float(nav.iloc[0]), "verdict": verdicts.get(name)}
        bench = {k: (None if s.empty or pd.isna(s.iloc[-1]) else float(s.iloc[-1])) for k, s in replay["benchmarks"].items()}
        slim = {k: {kk: vv for kk, vv in g.items()} for k, g in scored["groups"].items()}
        _save_state({"updated_at": _now_iso(), "last_bar": bar, "records": int(len(log)), "days_recorded": int(log["date"].nunique()),
                     "skipped": skipped, "groups": slim, "n_pass": scored["n_pass"], "accounts": accounts, "benchmarks": bench,
                     "tomorrow": _tomorrow_table(preds), "descriptive": tl.descriptive(resolved).to_dict(orient="records"), "ok": True})
        return {"ok": True, "steps": steps, "error": None}
    except (tl.TradeUnavailableError, ledger.TradeLedgerError, ValueError, OSError) as exc:
        logger.error("trade daily ล้มเหลว: %s — คำทำนายเดิมยังอยู่ ชุดของวันนี้ไม่ถูกเติมภายหลัง (กติกาที่ล็อก)", exc)
        old = load_state() or {}
        try:
            _save_state({**old, "ok": False, "error": str(exc), "updated_at": _now_iso()})
        except OSError:
            pass
        return {"ok": False, "steps": steps, "error": str(exc)}
