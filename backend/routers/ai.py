import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from analysis.ai_advisor import get_monthly_advice

from ..database import get_db
from ..models import Config
from ..schemas import AiAdviceRequest

router = APIRouter(prefix="/api/ai", tags=["AI"])
AI_HISTORY_KEY = "ai_history"


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
        result = get_monthly_advice(budget_thb=payload.budget_thb)
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
        return {"data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/history")
def ai_history(db: Session = Depends(get_db)):
    return {"data": _get_history(db)}
