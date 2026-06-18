"""AI router endpoints."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from analysis.ai_advisor import ai_suggest_alerts, get_monthly_advice

from ..database import get_db
from ..models import Config
from ..schemas import AiAdviceRequest

router = APIRouter(prefix="/api/ai", tags=["ai"])
AI_HISTORY_KEY = "ai_history"

_cache: dict[str, Any] = {}


def _get_history(db: Session) -> list[dict]:
    row = db.query(Config).filter(Config.key == AI_HISTORY_KEY).first()
    if not row:
        return []
    try:
        data = json.loads(row.value)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(db: Session, history: list[dict]) -> None:
    row = db.query(Config).filter(Config.key == AI_HISTORY_KEY).first()
    payload = json.dumps(history, ensure_ascii=False)
    if row:
        row.value = payload
    else:
        db.add(Config(key=AI_HISTORY_KEY, value=payload))
    db.commit()


@router.post("/advice")
def ai_advice(payload: AiAdviceRequest, db: Session = Depends(get_db)):
    try:
        cache_key = f"{datetime.now().strftime('%Y%m%d%H')}_{payload.budget_thb}"
        if cache_key in _cache:
            print("Backend cache hit")
            return JSONResponse(
                content={"data": _cache[cache_key]},
                media_type="application/json; charset=utf-8",
            )

        print("Backend calling Groq...")
        result = get_monthly_advice(budget_thb=payload.budget_thb)
        _cache[cache_key] = result

        history = _get_history(db)
        history.insert(
            0,
            {
                "created_at": datetime.utcnow().isoformat(),
                "budget_thb": payload.budget_thb,
                "advice_text": result.get("advice_text", ""),
            },
        )
        _save_history(db, history[:20])
        return JSONResponse(
            content={"data": result},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/history")
def ai_history(db: Session = Depends(get_db)):
    return JSONResponse(
        content={"data": _get_history(db)},
        media_type="application/json; charset=utf-8",
    )


@router.post("/suggest-alerts")
def suggest_alerts():
    """Suggest price alerts with AI analysis."""
    try:
        return JSONResponse(
            content=ai_suggest_alerts(),
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"suggest-alerts failed: {exc}") from exc
