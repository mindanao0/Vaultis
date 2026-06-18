from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import GoalContributeRequest, GoalCreate, GoalRead
from ..services import goal_service

router = APIRouter(prefix="/api/goals", tags=["Goals"])


def _goal_to_dict(goal) -> dict:
    return GoalRead.model_validate(goal).model_dump(mode="json")


@router.post("", status_code=201)
def create_goal(payload: GoalCreate, db: Session = Depends(get_db)):
    try:
        goal = goal_service.create_goal(db, payload)
        return JSONResponse(
            status_code=201,
            content={"data": _goal_to_dict(goal)},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_goals(db: Session = Depends(get_db)):
    goals = goal_service.list_goals(db)
    return JSONResponse(
        content={"data": [_goal_to_dict(g) for g in goals]},
        media_type="application/json; charset=utf-8",
    )


@router.get("/{goal_id}/progress")
def get_progress(goal_id: int, db: Session = Depends(get_db)):
    try:
        progress = goal_service.get_progress(db, goal_id)
        return JSONResponse(
            content={"data": progress},
            media_type="application/json; charset=utf-8",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{goal_id}/contribute")
def contribute(goal_id: int, payload: GoalContributeRequest, db: Session = Depends(get_db)):
    try:
        progress = goal_service.update_progress(db, goal_id, payload.actual_contribution_thb)
        return JSONResponse(
            content={"data": progress},
            media_type="application/json; charset=utf-8",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{goal_id}")
def delete_goal(goal_id: int, db: Session = Depends(get_db)):
    if not goal_service.delete_goal(db, goal_id):
        raise HTTPException(status_code=404, detail="ไม่พบเป้าหมายการออม")
    return JSONResponse(
        content={"data": {"deleted": True, "id": goal_id}},
        media_type="application/json; charset=utf-8",
    )
