# -*- coding: utf-8 -*-
"""งานรายวันของโหมด PREDICT: ดึงราคา → ทำนาย → บันทึก (หนึ่งชุดต่อวันที่ของแท่ง) → ให้คะแนนคำทำนายที่ครบกำหนด → จำลองเกณฑ์ตัดสิน → เก็บสถานะ.

รันโดย scheduler ทุกวัน 06:30 (หลังตลาดสหรัฐปิด) และตอนเริ่มโปรเซส · ``python main.py --job predict_daily`` บังคับจำลองใหม่ ·
ไม่ต้องใช้ webhook ไม่มี LLM ไม่มีค่าใช้จ่าย · **ไม่โยน exception** (ตัวตั้งเวลาต้องเดินต่อ): ล้มเหลว = log ERROR + ``ok=False`` พร้อมเหตุผล
ตัวทำนายที่รันไม่ได้ถูกตัดพร้อมเหตุผลและเก็บไว้ใน state (ไม่เงียบ) · state เก็บที่ ``simulation/data/predict_state.json``
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
STATE_FILE = "predict_state.json"


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


def run_predict_daily(force: bool = False, now: datetime | None = None) -> dict[str, Any]:
    from analysis import predict_lab as pl
    from portfolio import predict_ledger as ledger
    from simulation import predictor_sim

    steps: list[str] = []
    try:
        prices, failed = pl.fetch_prices()
        preds, skipped, bar = pl.make_predictions(prices, failed=failed)
        n = ledger.record_predictions(preds, bar)
        steps.append(f"บันทึกคำทำนายชุด {bar} จำนวน {n} แถว" if n else f"ชุด {bar} บันทึกไว้แล้ววันนี้ (ไม่เขียนซ้ำ)")
        log = ledger.load_log()
        today = pd.Timestamp(prices.dropna(how="all").index.max())
        resolved = pl.resolve(log, prices, today)
        scored = pl.score_all(resolved)
        sim = predictor_sim.load()
        age = predictor_sim.age_days(sim, now)
        if force or sim is None or age is None or age > predictor_sim.MAX_AGE_DAYS:
            sim = predictor_sim.simulate_gate(prices)
            predictor_sim.save(sim)
            steps.append(f"จำลองเกณฑ์ตัดสิน ({sim['paths']:,} เส้นทาง/โลก)")
        slim = {k: {kk: vv for kk, vv in g.items() if kk != "cohorts"} for k, g in scored["groups"].items()}
        desc = pl.descriptive(resolved)
        _save_state({"updated_at": _now_iso(), "last_bar": bar, "records": int(len(log)), "days_recorded": int(log["date"].nunique()),
                     "skipped": skipped, "groups": slim, "n_pass": scored["n_pass"],
                     "descriptive": desc.to_dict(orient="records"), "ok": True})
        return {"ok": True, "steps": steps, "error": None}
    except (pl.PredictUnavailableError, ledger.PredictLedgerError, ValueError, OSError) as exc:
        logger.error("predict daily ล้มเหลว: %s — คำทำนายเดิมยังอยู่ ชุดของวันนี้ไม่ถูกเติมภายหลัง (กติกาที่ล็อก)", exc)
        old = load_state() or {}
        try:
            _save_state({**old, "ok": False, "error": str(exc), "updated_at": _now_iso()})
        except OSError:
            pass
        return {"ok": False, "steps": steps, "error": str(exc)}
