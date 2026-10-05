# -*- coding: utf-8 -*-
"""ดึงข้อมูลสดของ simulation + รันแผนปัจจุบันในโลกจำลอง แล้วเก็บผลไว้ให้ dashboard / API / Discord อ่านทันที.

รันโดย scheduler ทุกวัน 06:00 และตอนเริ่มโปรเซส (``python main.py --job sim_refresh`` = บังคับรันใหม่ทั้งหมด)

ทำเมื่อ "ถึงเวลา" เท่านั้น (ไม่ยิงเน็ตทุกครั้ง):
* ข้อมูลดิบ: ยังไม่มี · เก่ากว่า ``STALE_AFTER_DAYS`` · หรือไม่ครอบกองที่ติดตามอยู่ตอนนี้ (เพิ่งเพิ่มกองใน Settings) → ดึงใหม่
* ผลของแผน: ยังไม่มี · เก่ากว่า 7 วัน · ข้อมูลเพิ่งถูกดึงใหม่ · หรือแผนเปลี่ยน (วิธีสัดส่วน/รายชื่อกอง/งบ) → รันใหม่

ล้มเหลว = log ERROR + คืน ``ok=False`` พร้อมเหตุผล (ข้อมูล/ผลเดิมยังอยู่ แต่หน้าจอบอกว่าเก่า) — **ไม่เดา ไม่ปลอมผล**
ไม่มี LLM ไม่มีค่าใช้จ่าย
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

PLAN_RESULT_MAX_AGE_DAYS = 7.0


def _workers() -> int:
    return max(1, min(4, os.cpu_count() or 1))


def _plan_inputs(config: dict[str, Any], method: str) -> dict[str, Any]:
    return {"method": method, "tickers": sorted(str(t).strip().upper() for t in config["etf"]["tickers"]),
            "budget_thb": float(config["dca"]["monthly_budget_thb"])}


def run_simulation_refresh(force: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """คืน ``{"ok": bool, "steps": [...], "error": str|None}`` — ไม่โยน exception (ตัวตั้งเวลาต้องเดินต่อ)."""
    from data.fetcher import PriceDataUnavailableError
    from portfolio.targets import TargetWeightsError, get_target_weights_with_status
    from simulation import data as sim_data
    from simulation import service
    from utils.config import load_config

    steps: list[str] = []
    try:
        config = load_config()
        tickers = [str(t).strip().upper() for t in config["etf"]["tickers"]]
        status = sim_data.data_status(now=now)
        # ข้อมูลดิบต้องมีราคาครบทุกอย่างที่ต้องใช้ (กอง + กองพี่ + ค่าเงิน) — เพิ่งเพิ่มกองใน Settings = ยังไม่ครอบ → ดึงใหม่
        covers = status.get("exists") and set(sim_data.required_tickers(tickers)) <= set(status.get("series_tickers", []))
        fetched = False
        if force or not status.get("exists") or status.get("stale") or not covers:
            why = "บังคับ" if force else (status.get("reason") or "กองที่ติดตามไม่ครอบคลุมข้อมูลที่ดึงไว้")
            logger.info("simulation: ดึงข้อมูลสด (%s)", why)
            sim_data.save_raw(sim_data.fetch_raw(tickers))
            fetched = True
            steps.append("ดึงข้อมูลสด")
        panel = sim_data.load_panel(tickers)
        target = get_target_weights_with_status(tickers)
        last = service.load_last_plan()
        age = service.last_plan_age_days(last, now)
        inputs = _plan_inputs(config, target.method)
        changed = last is None or last.get("inputs") != inputs
        due = force or fetched or changed or age is None or age > PLAN_RESULT_MAX_AGE_DAYS
        if due:
            result = service.simulate_current_plan(panel=panel, workers=_workers(), config=config, target_status=target)
            result["inputs"] = inputs
            service.save_last_plan(result)
            steps.append(f"รันแผน {result['plan']['plan_strategy']} ({result['paths_per_world']:,} เส้นทาง/โลก)")
        else:
            steps.append("ไม่ต้องรันใหม่ (ผลล่าสุดยังใหม่และแผนไม่เปลี่ยน)")
        # ตรวจความแม่นยำของโมเดลเทียบประวัติจริง: ข้อมูลเพิ่งถูกดึงใหม่ / ยังไม่เคยตรวจ / ผลเก่ากว่า 7 วัน
        cal = service.load_calibration()
        cal_age = service.last_plan_age_days(cal, now)
        if force or fetched or cal is None or cal_age is None or cal_age > PLAN_RESULT_MAX_AGE_DAYS:
            from simulation import validate

            service.save_calibration(validate.calibration_report(panel))
            steps.append("ตรวจความแม่นยำของโมเดลเทียบประวัติจริง")
        return {"ok": True, "steps": steps, "error": None, "fetched": fetched}
    except (sim_data.SimulationDataError, PriceDataUnavailableError, TargetWeightsError, ValueError) as exc:
        logger.error("simulation refresh ล้มเหลว: %s — ข้อมูล/ผลเดิม (ถ้ามี) ยังอยู่แต่จะถูกแสดงว่าเก่า", exc)
        return {"ok": False, "steps": steps, "error": str(exc), "fetched": False}
