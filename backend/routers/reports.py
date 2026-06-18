import traceback

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.report_models import MonthlyReportRead
from ..services import report_service

router = APIRouter(prefix="/api/reports", tags=["Reports"])


@router.post("/generate")
async def generate_report():
    try:
        result = await report_service.generate_and_save_report()
        return JSONResponse(
            content={"data": result},
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("")
def list_reports(db: Session = Depends(get_db)):
    reports = report_service.list_reports(db)
    return JSONResponse(
        content={"data": [MonthlyReportRead.model_validate(r).model_dump(mode="json") for r in reports]},
        media_type="application/json; charset=utf-8",
    )


@router.get("/{month}")
def get_report(month: str, db: Session = Depends(get_db)):
    report = report_service.get_report(db, month)
    if not report:
        raise HTTPException(status_code=404, detail=f"ไม่พบรายงานเดือน {month}")
    return JSONResponse(
        content={"data": MonthlyReportRead.model_validate(report).model_dump(mode="json")},
        media_type="application/json; charset=utf-8",
    )
