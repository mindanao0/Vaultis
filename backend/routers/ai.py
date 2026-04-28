"""AI router endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from analysis.ai_advisor import ai_suggest_alerts

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/suggest-alerts")
def suggest_alerts() -> dict:
    """Suggest price alerts with AI analysis."""
    try:
        return ai_suggest_alerts()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"suggest-alerts failed: {exc}") from exc
