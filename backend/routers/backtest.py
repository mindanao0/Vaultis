"""Backtest router: POST /api/backtest

นโยบายความล้มเหลว (C1 — fail loud ห้ามกลบสาเหตุ): endpoint นี้ล้มได้ 3 แบบซึ่ง
ผู้ใช้ต้องแยกออกจากกัน ห้ามยุบเป็น 500 ก้อนเดียวเหมือนเดิม

- **ดึงราคาไม่สำเร็จ** (``PriceDataUnavailableError``) → 503 "ดึงราคา ... ไม่สำเร็จ"
  ปัญหาอยู่ที่ข้อมูลต้นทาง ไม่ใช่บั๊ก — ลองใหม่ทีหลังได้
- **ข้อมูลไม่พอ/ช่วงวันที่ใช้ไม่ได้** (``ValueError`` จาก engine ซึ่งมีข้อความไทยอธิบาย
  ตัวเองอยู่แล้ว เช่น "ผลว่าง", "ข้อมูลไม่พอแบ่งช่วง train/test") → 400 พร้อมเหตุผลจริง
- **บั๊กจริง** → 500 "ระบบมีข้อผิดพลาด"

``PriceDataUnavailableError`` ไม่ได้เป็นเพียงทฤษฎี: ``BacktestEngine.fetch_data`` โยน
ชนิดนี้จริงหลัง retry 3 ครั้ง (เดิมโยน ``ValueError`` ทำให้ "ดึงราคาไม่ได้" ถูกเล่าเป็น
400 = ความผิดของผู้เรียก — AUDIT_2026-08-06 B3.5)

ส่วนที่ไม่นิยาม (``num_trades == 0``) คืน ``null`` ทุกช่องพร้อม ``detail`` ภาษาไทย
**ไม่ใช่ 200 OK ที่มีศูนย์ปลอม** และ ``outperformed`` เป็น ``null`` เพราะเทียบไม่ได้

ส่วนคำอธิบายจาก AI ที่ล้มเหลว **ไม่** ลากตัวเลขที่คำนวณเสร็จแล้วลงไปด้วย (AI อธิบาย
โค้ดคำนวณ) — แต่ต้องดักเฉพาะ ``RuntimeError`` ซึ่งเป็นชนิดที่ ``analysis/llm.py``
ห่อความล้มเหลวทุกอย่างของ provider ไว้ ส่วน KeyError/TypeError คือบั๊กของเราเอง
ต้องปล่อยให้ดังเป็น 500 ห้ามแต่งตัวเป็น "AI ล้มเหลว"

แบบที่ **สี่** ที่เพิ่งเพิ่ม: **คำขอผิดรูป** (``start``/``end`` ไม่ใช่ YYYY-MM-DD หรือ
สลับหัวท้าย) → 422 ก่อนแตะเน็ตเลยแม้แต่ครั้งเดียว ดู ``_validated_date_range``
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.exceptions import RequestValidationError

from analysis.backtest_engine import BacktestEngine
from analysis.backtest_summary import generate_summary
from analysis.llm import AI_DISABLED_MESSAGE
from backend.models.backtest_models import BacktestRequest, BacktestResponse
from backend.schemas import parse_iso_date
from data.fetcher import PriceDataUnavailableError

router = APIRouter(prefix="/api", tags=["backtest"])

_engine = BacktestEngine()

# ป้ายภาษาไทยของสองช่องนี้ ใช้ทั้งในข้อความ error และในเอกสาร
_DATE_FIELDS = (("start", "วันเริ่มต้น (start)"), ("end", "วันสิ้นสุด (end)"))


def _price_unavailable(symbol: str, exc: Exception) -> HTTPException:
    """503 — ดึงราคาไม่สำเร็จ (ต่างจาก "ระบบมีข้อผิดพลาด" ซึ่งแปลว่าโค้ดเราพัง)"""
    return HTTPException(status_code=503, detail=f"ดึงราคา {symbol} ไม่สำเร็จ: {exc}")


def _validated_date_range(payload: BacktestRequest) -> tuple[str, str]:
    """ตรวจ ``start``/``end`` ก่อนแตะเน็ต — วันที่ผิดรูปคือความผิดของคำขอ ไม่ใช่ของแหล่งข้อมูล.

    AUDIT_ROUND2_2026-08-07: ``BacktestRequest.start``/``.end`` ประกาศเป็น ``str`` เปล่า
    ไม่มี validator ⇒ ``start="banana"`` เดินผ่าน Pydantic ไปตายที่ yfinance ซึ่งชั้นล่าง
    นับเป็น "ผลว่าง" retry 3 รอบ แล้วโยน ``PriceDataUnavailableError`` → **503 "ดึงราคา
    VOO ไม่สำเร็จ"** ตามนโยบายที่ไฟล์นี้เขียนไว้เองว่า 503 = "ปัญหาอยู่ที่ข้อมูลต้นทาง
    ลองใหม่ทีหลังได้" ซึ่งเป็นคำอธิบายที่ผิดความจริงและทำให้ผู้ใช้ลองใหม่ซ้ำ ๆ โดยไม่มี
    วันสำเร็จ · แต่ละครั้งเผาการยิงเน็ตจริง 3 รอบ (เชื้อของ rate-limit)

    ใช้ ``RequestValidationError`` ไม่ใช่ ``HTTPException(422, detail="ข้อความ")`` เพราะ
    openapi ประกาศ 422 ว่าเป็น ``HTTPValidationError`` ที่ ``detail`` เป็น **array ของ
    object ที่มี loc/msg** — ไคลเอนต์ที่อ่าน ``detail[0].loc`` ต้องไม่พังเพราะเราตอบ
    เป็นสตริง (รูปเดียวกับที่ Pydantic ตอบ จึงชี้ได้ว่าฟิลด์ไหนผิด)

    คืนวันที่ที่ normalize เป็น ``YYYY-MM-DD`` แล้ว: ``date.fromisoformat`` ของ Python ≥3.11
    รับ ``"20260105"`` ด้วย ซึ่ง yfinance อ่านไม่ออก — ต้องไม่ปล่อยรูปแบบนั้นลงไป
    """
    errors: list[dict] = []
    parsed: dict[str, date] = {}

    for field, label in _DATE_FIELDS:
        raw = getattr(payload, field)
        try:
            parsed[field] = parse_iso_date(raw, label)
        except ValueError as exc:
            errors.append(
                {"type": "value_error", "loc": ("body", field), "msg": str(exc), "input": raw}
            )

    if not errors and parsed["start"] > parsed["end"]:
        errors.append(
            {
                "type": "value_error",
                "loc": ("body", "end"),
                "msg": (
                    "วันสิ้นสุด (end) ต้องไม่มาก่อนวันเริ่มต้น (start) — ได้ "
                    f"start={parsed['start'].isoformat()} end={parsed['end'].isoformat()}"
                ),
                "input": payload.end,
            }
        )

    if errors:
        raise RequestValidationError(errors)

    return parsed["start"].isoformat(), parsed["end"].isoformat()


@router.post(
    "/backtest",
    response_model=BacktestResponse,
    summary="Backtest กลยุทธ์ RSI+MACD บนสัญลักษณ์เดียว",
    description=(
        "`start` และ `end` ต้องเป็นวันที่รูปแบบ **YYYY-MM-DD** และ `start` ต้องไม่มาหลัง `end` "
        "— ผิดรูปจะได้ 422 พร้อมชี้ฟิลด์ (ไม่ใช่ 503 'ดึงราคาไม่สำเร็จ' อย่างที่เคยเป็น)\n\n"
        "หมายเหตุเชิงสัญญา: ชนิดของสองช่องนี้ประกาศเป็น `string` ใน "
        "`backend/models/backtest_models.py` จึงบอก `format: date` ใน openapi ไม่ได้ "
        "คำอธิบายตรงนี้จึงเป็นที่เดียวที่คนอ่าน /docs จะเห็นข้อกำหนด "
        "(AUDIT_ROUND2_2026-08-07)"
    ),
)
def run_backtest(
    payload: BacktestRequest,
    include_ai: bool = Query(False, description="เรียก AI อธิบายผล (มีค่าใช้จ่าย)"),
):
    # ด่านนี้ต้องอยู่ก่อนทุกอย่าง: คำขอที่ใช้ไม่ได้ห้ามกลายเป็นการยิงเน็ต
    start, end = _validated_date_range(payload)

    strategy_params = {
        "rsi_period": payload.rsi_period,
        "rsi_oversold": payload.rsi_oversold,
        "rsi_overbought": payload.rsi_overbought,
        "macd_fast": payload.macd_fast,
        "macd_slow": payload.macd_slow,
        "macd_signal": payload.macd_signal,
    }

    best_params = None
    optimization = None

    if payload.run_optimization:
        try:
            optimization = _engine.optimize(payload.symbol, start, end)
            best_params = optimization["best_params"]
            strategy_params.update(best_params)
        except PriceDataUnavailableError as exc:
            raise _price_unavailable(payload.symbol, exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"optimize ไม่สำเร็จ: {exc}") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"ระบบมีข้อผิดพลาดระหว่าง optimize: {exc}"
            ) from exc

    try:
        result = _engine.run(
            payload.symbol,
            start,
            end,
            strategy_params=strategy_params,
        )
    except PriceDataUnavailableError as exc:
        raise _price_unavailable(payload.symbol, exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"backtest ไม่สำเร็จ: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ระบบมีข้อผิดพลาดระหว่าง backtest: {exc}") from exc

    # คำอธิบายจาก AI = ค่าใช้จ่าย → ต้องขอมาโดยตรงเท่านั้น (?include_ai=true)
    ai_summary = AI_DISABLED_MESSAGE
    if include_ai:
        try:
            ai_summary = generate_summary(result, payload.symbol, user_initiated=True)
        except RuntimeError as exc:
            # ตัวเลขด้านบนคำนวณด้วย Python เสร็จแล้วและยังถูกต้อง — คืนไปพร้อมบอกว่า
            # ทำไมคำอธิบายหาย (``LLMDisabledError`` ก็เป็นลูกของ RuntimeError จึงเข้าทางนี้)
            ai_summary = f"ไม่สามารถสร้างสรุป AI ได้: {exc}"

    return BacktestResponse(
        **result,
        best_params=best_params,
        # ผลการจูนทั้งบล็อก (train/test sharpe + คำเตือน overfit) — เดิมเหลือแต่
        # best_params ผู้ใช้จึงไม่มีทางรู้ว่าชุดที่เลือกทำผลนอกกลุ่มตัวอย่างได้แค่ไหน
        optimization=optimization,
        ai_summary=ai_summary,
    )
