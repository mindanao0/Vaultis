"""Per-symbol ETF analysis with Redis cache and Groq summary."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from ..models.etf_models import ETFAnalysis, ETFCompareResponse, ETFInfo, TechnicalIndicators
from ..services.analysis_service import AnalysisService
from ..services.cache_service import (
    ETF_INFO_TTL,
    TECHNICAL_TTL,
    CacheService,
    etf_info_cache_key,
    etf_technical_cache_key,
)
from ..services.etf_info_service import ETFInfoService
from ..services.technical_service import TechnicalService

ALLOWED_SYMBOLS = frozenset({"VOO", "QQQM", "SCHD", "XLV", "GLDM"})

router = APIRouter(prefix="/api", tags=["ETF Analysis"])

_cache = CacheService()
_info_service = ETFInfoService()
_technical_service = TechnicalService()
_analysis_service = AnalysisService()


def _normalize_allowed_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if sym not in ALLOWED_SYMBOLS:
        raise HTTPException(
            status_code=404,
            detail=f"ไม่รองรับสัญลักษณ์ {symbol!r} (ใช้ได้เฉพาะ {sorted(ALLOWED_SYMBOLS)})",
        )
    return sym


def _technical_fetch_failed(technical: TechnicalIndicators) -> bool:
    return technical.price == 0.0


def _parse_compare_symbols(symbols: str) -> list[str]:
    parts = [p.strip().upper() for p in symbols.split(",")]
    ordered: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not p:
            continue
        if p not in ALLOWED_SYMBOLS:
            raise HTTPException(
                status_code=404,
                detail=f"ไม่รองรับสัญลักษณ์ {p!r} (ใช้ได้เฉพาะ {sorted(ALLOWED_SYMBOLS)})",
            )
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    if not ordered:
        raise HTTPException(
            status_code=400,
            detail='ระบุ query พารามิเตอร์ symbols เช่น symbols="VOO,QQQM,SCHD"',
        )
    return ordered


async def _ensure_info_and_technical(sym: str) -> tuple[ETFInfo, TechnicalIndicators]:
    ikey = etf_info_cache_key(sym)
    tkey = etf_technical_cache_key(sym)

    info_raw = await _cache.get(ikey)
    info: ETFInfo | None = None
    if info_raw is not None:
        try:
            info = ETFInfo.model_validate(info_raw)
        except Exception:
            info = None
    if info is None:
        info = await _info_service.get_info(sym)
        await _cache.set(ikey, info.model_dump(mode="json"), ETF_INFO_TTL)

    tech_raw = await _cache.get(tkey)
    technical: TechnicalIndicators | None = None
    if tech_raw is not None:
        try:
            technical = TechnicalIndicators.model_validate(tech_raw)
        except Exception:
            technical = None
    if technical is None:
        technical = await _technical_service.get_technical(sym)
        await _cache.set(tkey, technical.model_dump(mode="json"), TECHNICAL_TTL)

    return info, technical


async def _build_core_analysis(sym: str) -> ETFAnalysis:
    info, technical = await _ensure_info_and_technical(sym)
    if _technical_fetch_failed(technical):
        raise HTTPException(
            status_code=500,
            detail="ไม่สามารถดึงข้อมูล technical ได้ กรุณาลองใหม่ภายหลัง",
        )
    overall = _analysis_service.compute_overall_signal(technical)
    now = datetime.now(UTC)
    return ETFAnalysis(
        symbol=sym,
        info=info,
        technical=technical,
        overall_signal=overall,
        ai_summary=None,
        updated_at=now,
    )


@router.get("/etf/compare", response_model=ETFCompareResponse)
async def compare_etfs(symbols: str | None = None) -> ETFCompareResponse:
    if symbols is None:
        raise HTTPException(
            status_code=400,
            detail='ระบุ query พารามิเตอร์ symbols เช่น ?symbols=VOO,QQQM,SCHD',
        )
    try:
        parsed = _parse_compare_symbols(symbols)
        analyses: list[ETFAnalysis] = []
        for sym in parsed:
            analyses.append(await _build_core_analysis(sym))

        additional = analyses[1:] if len(analyses) > 1 else None
        combined = await _analysis_service.get_ai_summary(
            analyses[0],
            compare_mode=True,
            additional_analyses=additional,
        )
        return ETFCompareResponse(analyses=analyses, ai_summary=combined)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"การดึงข้อมูลหรือวิเคราะห์ล้มเหลว: {exc}",
        ) from exc


@router.get("/etf/{symbol}", response_model=ETFAnalysis)
async def get_etf_analysis(symbol: str) -> ETFAnalysis:
    try:
        sym = _normalize_allowed_symbol(symbol)
        core = await _build_core_analysis(sym)
        ai_summary = await _analysis_service.get_ai_summary(core, compare_mode=False)
        return core.model_copy(update={"ai_summary": ai_summary})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"การดึงข้อมูลหรือวิเคราะห์ล้มเหลว: {exc}",
        ) from exc
