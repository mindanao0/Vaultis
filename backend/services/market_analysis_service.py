"""Service layer สำหรับ backtest / DCA sim / macro / DCF (AUDIT.md L5).

เดิม ``routers/analysis.py`` import จาก ``analysis/`` และ ``portfolio/`` ตรง ๆ
ซึ่งขัดกฎของโปรเจกต์เอง (Routers → Services → Analysis) — router ไม่ควรรู้จัก
โครงสร้างของชั้น analysis และไม่ควรแปลง DataFrame เป็น JSON เอง
"""

from __future__ import annotations

from typing import Any

from analysis.financial_model import dcf_valuation, run_full_analysis
from analysis.macro import get_macro_data
from data.fetcher import fetch_adjusted_close_data
from portfolio.backtest import run_portfolio_backtest
from portfolio.dca import COVERAGE_ATTR, describe_coverage, simulate_monthly_dca
from utils.config import get_tickers

from .cache_service import PRICE_HISTORY_TTL, shared_cache
from .json_safe import frame_to_records


def _prices():
    tickers = get_tickers()
    key = "prices_10y:" + ",".join(sorted(tickers))
    return shared_cache.get_or_compute(
        key,
        PRICE_HISTORY_TTL,
        lambda: fetch_adjusted_close_data(tickers=tickers, years=10).ffill(),
        expect_keys=tickers,
    )


def run_backtest(weights: dict[str, float], initial_capital: float) -> dict[str, Any]:
    """ผล backtest + รายชื่อกองที่ **ไม่ได้อยู่ในผล** (สำนวนเดียวกับ :func:`simulate_dca`).

    เดิมบรรทัดสุดท้ายเป็น ``return frame_to_records(result)`` เฉย ๆ ซึ่ง **ทิ้ง**
    ``result.attrs[COVERAGE_ATTR]`` ที่ ``portfolio/backtest.py`` อุตส่าห์คำนวณไว้
    (``.attrs`` ไม่ติดไปกับ ``to_dict(orient="records")``) ⇒ ยิงจริงเจอว่า
    ``POST /api/analysis/backtest`` ด้วย ``{"VOO": 0.5, "SCHD": 0.5}`` ตอนที่ SCHD
    ไม่มีคอลัมน์ราคา ตอบ 200 พร้อมเส้นมูลค่าของ **VOO 100%** และคำว่า SCHD ไม่โผล่
    ที่ไหนเลยในคำตอบ = ตอบคำถามที่ผู้เรียกไม่ได้ถาม โดยไม่บอกว่าเปลี่ยนคำถามให้
    (AUDIT_ROUND2_2026-08-07 T6 — ชั้นไลบรารีแก้แล้ว แต่ผู้บริโภคยังโยนทิ้ง)

    รูปคำตอบจึงเป็น dict ชุดคีย์เดียวกับ ``/api/dca/simulate`` เป๊ะ ๆ
    (``history`` / ``coverage`` / ``warning``) เพื่อให้ผู้เรียกอ่านทั้งสอง endpoint
    ด้วยโค้ดชุดเดียว ไม่ใช่สองสำนวนที่สำนวนหนึ่งจะถูกลืม
    """
    result = run_portfolio_backtest(
        price_df=_prices(), weights=weights, initial_capital=initial_capital
    )
    coverage = dict(result.attrs.get(COVERAGE_ATTR) or {})
    return {
        "history": frame_to_records(result),
        "coverage": coverage,
        # ``None`` = ไม่มีกองไหนหายไปจากพอร์ตที่ส่งมา (คีย์มีเสมอ ค่าถึงจะว่างได้)
        "warning": describe_coverage(coverage),
    }


def simulate_dca(weights: dict[str, float], monthly_investment: float) -> dict[str, Any]:
    """ผลจำลอง DCA + ช่วงเวลาที่จำลองได้จริง.

    เดิมคืนเฉพาะ ``history`` เป็นลิสต์ ผู้เรียกจึงไม่มีทางรู้ว่ากี่เดือนถูกตัดทิ้ง
    เพราะ ETF บางกองยังไม่เกิด — ``Total Invested`` ที่ต่ำกว่าจริงดูเหมือนตัวเลข
    ปกติ (AUDIT_2026-08-06 B8) ตอนนี้ ``coverage``/``warning`` เดินทางไปกับผล
    """
    result = simulate_monthly_dca(
        price_df=_prices(), weights=weights, monthly_investment=monthly_investment
    )
    coverage = dict(result.attrs.get(COVERAGE_ATTR) or {})
    return {
        "history": frame_to_records(result),
        "coverage": coverage,
        "warning": describe_coverage(coverage),
    }


def macro_snapshot() -> dict[str, Any]:
    return get_macro_data()


def dcf_for_ticker(ticker: str) -> dict[str, Any]:
    symbol = str(ticker).strip().upper()
    if not symbol:
        raise ValueError("ต้องระบุ ticker")
    return dcf_valuation(symbol)


def full_analysis(budget_thb: float) -> dict[str, Any]:
    return run_full_analysis(budget_thb=budget_thb)
