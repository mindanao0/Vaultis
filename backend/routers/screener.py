import asyncio

from fastapi import APIRouter, HTTPException

from backend.screener.engine import ScreenerEngine
from backend.screener.history_service import ScreenerHistoryService
from backend.screener.models import (
    CustomScreenerRequest,
    ScreenerPreset,
    ScreenerRule,
    ScreenerRunRequest,
)
from backend.screener.notifier import ScreenerNotifier
from backend.screener.presets import PRESETS, get_preset

from ..responses import UTF8JSONResponse

# default_response_class: คำอธิบายพรีเซ็ต/สรุปผลเป็นภาษาไทย ต้องประกาศ charset
# ให้ตรงตามที่ CLAUDE.md กำหนด — AUDIT_2026-08-06 D3.2
router = APIRouter(prefix="/api", tags=["Screener"], default_response_class=UTF8JSONResponse)

_engine = ScreenerEngine()
_history = ScreenerHistoryService()
_notifier = ScreenerNotifier()


@router.post("/screener/run")
async def run_screener(payload: ScreenerRunRequest):
    try:
        preset = get_preset(payload.preset)
        # yfinance เป็น sync I/O — ต้องออกจาก event loop ไม่งั้น API ทั้งตัวค้าง
        # ระหว่างสแกน (AUDIT_2026-08-06 ข้อ B6.1 — กฎเดียวกับ routers/websocket.py)
        results = await asyncio.to_thread(_engine.run, payload.symbols, preset)
        if results:
            await _history.save_results(results, payload.preset)
        ai_summary = await _notifier.build_ai_summary(results, payload.preset)
        return {
            "results": [r.__dict__ for r in results],
            "ai_summary": ai_summary,
            "total_signals": len(results),
            # สัญลักษณ์ที่ตรวจไม่ได้ — "ดึงไม่สำเร็จ" ≠ "ไม่มีสัญญาณ" (C1)
            "errors": list(getattr(results, "errors", [])),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"screener run failed: {exc}") from exc


@router.get("/screener/presets")
async def get_screener_presets():
    return [
        {"name": preset.name, "description": preset.description}
        for preset in PRESETS.values()
    ]


@router.post("/screener/custom")
async def run_custom_screener(payload: CustomScreenerRequest):
    try:
        rules = [
            ScreenerRule(
                field=str(rule.get("field", "")),
                operator=str(rule.get("operator", "")),
                value=rule.get("value"),
                # ``or ""`` ไม่ใช่ค่าดีฟอลต์ของ ``.get`` เพราะ ``{"description": null}``
                # จะได้ ``str(None)`` = "None" ไปโชว์เป็นเหตุผลที่กฎผ่าน
                # (ตัวกรณีไม่ใส่คำอธิบายเลย เอนจินเติมข้อความจากตัวกฎให้เอง — AUDIT_ROUND2_2026-08-07)
                description=str(rule.get("description") or ""),
            )
            for rule in payload.rules
        ]
        preset = ScreenerPreset(
            name="custom",
            rules=rules,
            logic=(payload.logic or "AND").upper(),
            description="Custom screener preset",
        )
        results = await asyncio.to_thread(_engine.run, payload.symbols, preset)
        return {
            "results": [r.__dict__ for r in results],
            "total_signals": len(results),
            "errors": list(getattr(results, "errors", [])),
        }
    except ValueError as exc:
        # นิยามที่ผู้เรียกส่งมาผิดเอง (logic ไม่ใช่ AND/OR, ไม่มีกฎสักข้อ) = 400 ไม่ใช่ 500
        # — เดิม logic ที่สะกดผิดถูกตีความเป็น OR เงียบ ๆ (AUDIT_ROUND2_2026-08-07)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"custom screener failed: {exc}") from exc
