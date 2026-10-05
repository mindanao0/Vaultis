"""โลกจำลองอนาคต 5–20 ปี — งานหลักของระบบ: ทุกแผนที่คำนวณต้องลองรันที่นี่ด้วย.

* ``GET  /api/simulation/status``  สถานะข้อมูล (ดึงเมื่อไร · เก่าไหม) + ผลล่าสุดของแผนปัจจุบัน
* ``GET  /api/simulation/last``    ผลเต็มของแผนปัจจุบันที่ scheduler รันไว้ (อ่านทันที)
* ``POST /api/simulation/run``     รันแผนปัจจุบันใหม่ตอนนี้ (ใช้ CPU หลายวินาที — ทีละคำขอ)
* ``POST /api/simulation/whatif``  ลองสัดส่วนที่กำหนดเองเทียบ ERC / blend / 1/N

ผลคือช่วงผลลัพธ์ + ข้อจำกัดของโมเดล (ใช้เทียบสูตรกัน ไม่ใช่พยากรณ์) · ไม่มี LLM ไม่มีค่าใช้จ่าย
ต้องมี X-API-Key (กิน CPU และเปิดเผยแผนส่วนตัว)
"""
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from data.fetcher import PriceDataUnavailableError
from portfolio.targets import InvalidTargetWeights, TargetWeightsError
from simulation import data as sim_data
from simulation import service

from ..schemas import SimulationRunRequest, SimulationWhatIfRequest

router = APIRouter(prefix="/api/simulation", tags=["Simulation"])

# กิน CPU หลายวินาทีต่อคำขอ — ให้รันทีละอันเดียว (คำขอซ้อน = 409 ไม่ใช่รอคิวจนเซิร์ฟเวอร์ช้า)
_RUN_LOCK = threading.Lock()


def _json(data) -> JSONResponse:
    return JSONResponse(content={"data": data}, media_type="application/json; charset=utf-8")


def _raise_for(exc: Exception) -> None:
    if isinstance(exc, InvalidTargetWeights):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, (sim_data.SimulationDataError, PriceDataUnavailableError, TargetWeightsError)):
        # ข้อมูลล่ม ≠ คอนฟิกผิด ≠ เซิร์ฟเวอร์พัง → 503 (ชั่วคราว ลองใหม่ได้)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/status")
def simulation_status():
    last = service.load_last_plan()
    return _json({
        "data": sim_data.data_status(),
        "last_plan": None if not last else {
            "created_at": last.get("created_at"), "age_days": service.last_plan_age_days(last),
            "plan": last.get("plan"), "paths_per_world": last.get("paths_per_world"), "data_as_of": (last.get("data") or {}).get("as_of"),
        },
        "limitations": list(service.LIMITATIONS),
    })


@router.get("/last")
def simulation_last():
    last = service.load_last_plan()
    if last is None:
        raise HTTPException(status_code=404, detail="ยังไม่มีผล simulation ของแผนปัจจุบัน — รอตัวตั้งเวลา (06:00) หรือเรียก POST /api/simulation/run")
    return _json({**last, "summary_lines": service.summary_lines(last)})


@router.post("/run")
def simulation_run(payload: SimulationRunRequest):
    if not _RUN_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="กำลังรัน simulation อยู่ — รอให้เสร็จก่อนแล้วลองใหม่")
    try:
        result = service.simulate_current_plan(paths=payload.paths, workers=1)
        if payload.save:
            service.save_last_plan(result)
        return _json({**result, "summary_lines": service.summary_lines(result)})
    except Exception as exc:  # noqa: BLE001 — แปลงเป็นสถานะตามชนิด (ข้อมูล/คอนฟิก/อินพุต)
        _raise_for(exc)
    finally:
        _RUN_LOCK.release()


@router.post("/whatif")
def simulation_whatif(payload: SimulationWhatIfRequest):
    from utils.config import load_config

    if not _RUN_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="กำลังรัน simulation อยู่ — รอให้เสร็จก่อนแล้วลองใหม่")
    try:
        config = load_config()
        tickers = [str(t).strip().upper() for t in config["etf"]["tickers"]]
        panel = sim_data.load_panel(tickers)
        result = service.simulate_strategies(
            panel, {"ERC": "ERC", "BLEND": "BLEND", "1/N": "EQ", "สัดส่วนที่กำหนด": payload.weights},
            reference="ERC", budget_thb=float(config["dca"]["monthly_budget_thb"]), paths=payload.paths, workers=1)
        return _json({**result, "limitations": list(service.LIMITATIONS), "data_as_of": panel["meta"]["as_of"]})
    except Exception as exc:  # noqa: BLE001
        _raise_for(exc)
    finally:
        _RUN_LOCK.release()
