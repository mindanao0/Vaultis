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

router = APIRouter(prefix="/api", tags=["Screener"])

_engine = ScreenerEngine()
_history = ScreenerHistoryService()
_notifier = ScreenerNotifier()


@router.post("/screener/run")
async def run_screener(payload: ScreenerRunRequest):
    try:
        preset = get_preset(payload.preset)
        results = _engine.run(payload.symbols, preset)
        if results:
            await _history.save_results(results, payload.preset)
        ai_summary = await _notifier.build_ai_summary(results, payload.preset)
        return {
            "results": [r.__dict__ for r in results],
            "ai_summary": ai_summary,
            "total_signals": len(results),
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
                description=str(rule.get("description", "")),
            )
            for rule in payload.rules
        ]
        preset = ScreenerPreset(
            name="custom",
            rules=rules,
            logic=(payload.logic or "AND").upper(),
            description="Custom screener preset",
        )
        results = _engine.run(payload.symbols, preset)
        return {"results": [r.__dict__ for r in results], "total_signals": len(results)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"custom screener failed: {exc}") from exc
