"""โลกจำลองอนาคต 5–20 ปี — งานหลักของระบบ: ทุกแผนที่คำนวณต้องลองรันที่นี่ด้วย.

* ``GET  /api/simulation/status``  สถานะข้อมูล (ดึงเมื่อไร · เก่าไหม) + ผลล่าสุดของแผนปัจจุบัน
* ``GET  /api/simulation/last``    ผลเต็มของแผนปัจจุบันที่ scheduler รันไว้ (อ่านทันที)
* ``POST /api/simulation/run``     รันแผนปัจจุบันใหม่ตอนนี้ (ใช้ CPU หลายวินาที — ทีละคำขอ)
* ``POST /api/simulation/whatif``  ลองสัดส่วนที่กำหนดเอง (กองไหนก็ได้ที่มีข้อมูล รวมกองเสริม) เทียบ ERC / blend / 1/N
* ``GET  /api/simulation/funds``   กองที่ใช้ได้ + ชนิดที่โมเดลสมมติ
* ``GET  /api/simulation/calibration``  โมเดลเทียบประวัติจริง (ผันผวน/สัมพันธ์/หางหนา/ผันผวนเป็นกลุ่ม/MaxDD)

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
        "limitations": (last or {}).get("limitations") or list(service.LIMITATIONS),
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
    """ลองสัดส่วนที่กำหนดเองบน **จักรวาลกองที่ระบุเอง** (รวมกองเสริม เช่น VXUS/BND/TLT/VNQ ได้) เทียบ ERC / blend / 1/N."""
    from utils.config import load_config

    if not _RUN_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="กำลังรัน simulation อยู่ — รอให้เสร็จก่อนแล้วลองใหม่")
    try:
        result = service.simulate_universe(
            payload.weights, budget_thb=float(load_config()["dca"]["monthly_budget_thb"]), paths=payload.paths, workers=1)
        return _json(result)
    except Exception as exc:  # noqa: BLE001
        _raise_for(exc)
    finally:
        _RUN_LOCK.release()


@router.get("/calibration")
def simulation_calibration():
    """ผลตรวจว่าโมเดลมี "หน้าตา" เหมือนประวัติจริงไหม (ความผันผวน ความสัมพันธ์ หางหนา ฯลฯ) — โมเดลหลวมกว่าอดีตต้องเห็นตรงนี้."""
    cal = service.load_calibration()
    if cal is None:
        raise HTTPException(status_code=404, detail="ยังไม่มีผลตรวจความแม่นยำของโมเดล — รอตัวตั้งเวลา (06:00) หรือรัน `python main.py --job sim_refresh`")
    return _json({**cal, "summary_lines": service.calibration_lines(cal)})


@router.get("/funds")
def simulation_funds():
    """กองที่ใช้ใน simulation ได้ (มีข้อมูลที่ดึงไว้แล้ว) พร้อมชนิดที่โมเดลสมมติให้ — ชนิดเป็นข้อสมมติ ไม่ใช่ข้อมูลที่วัด."""
    from simulation.universe import asset_for, is_guessed

    status = sim_data.data_status()
    funds = status.get("funds_available", []) if status.get("exists") else []
    return _json({"data_exists": bool(status.get("exists")), "funds": [
        {"ticker": t, "kind": asset_for(t).kind, "note": asset_for(t).note, "kind_is_guess": is_guessed(t)} for t in funds]})
