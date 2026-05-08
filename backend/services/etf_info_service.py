from __future__ import annotations

import asyncio
from typing import Any

import yfinance as yf

from ..models.etf_models import ETFInfo

ETF_PROFILES: dict[str, str] = {
    "VOO": "S&P 500 Index ETF (Vanguard) — broad market",
    "QQQM": "Nasdaq 100 ETF (Invesco) — tech heavy",
    "SCHD": "Dividend ETF (Schwab) — income focused",
    "XLV": "Healthcare Sector ETF (SPDR) — sector",
    "GLDM": "Gold ETF (SPDR) — commodity / safe haven",
}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


class ETFInfoService:
    async def get_info(self, symbol: str) -> ETFInfo:
        sym = symbol.strip().upper()
        try:
            raw = await asyncio.to_thread(lambda: yf.Ticker(sym).info)
            if not isinstance(raw, dict):
                return ETFInfo(symbol=sym)

            price = _to_float(
                raw.get("currentPrice")
                or raw.get("regularMarketPrice")
                or raw.get("navPrice")
            )
            nav = _to_float(raw.get("navPrice"))
            total_assets = _to_float(raw.get("totalAssets"))
            expense_ratio = _to_float(raw.get("annualReportExpenseRatio"))
            dividend_yield = _to_float(raw.get("dividendYield") or raw.get("yield"))
            trailing_dividend = _to_float(raw.get("trailingAnnualDividendRate"))
            ytd_return = _to_float(raw.get("ytdReturn"))
            three_year_return = _to_float(raw.get("threeYearAverageReturn"))
            five_year_return = _to_float(raw.get("fiveYearAverageReturn"))
            beta = _to_float(raw.get("beta3Year") or raw.get("beta"))
            category = _optional_str(raw.get("category"))
            name = _optional_str(raw.get("longName") or raw.get("shortName"))
            profile = ETF_PROFILES.get(sym)

            return ETFInfo(
                symbol=sym,
                name=name,
                price=price,
                nav=nav,
                total_assets=total_assets,
                expense_ratio=expense_ratio,
                dividend_yield=dividend_yield,
                trailing_dividend=trailing_dividend,
                ytd_return=ytd_return,
                three_year_return=three_year_return,
                five_year_return=five_year_return,
                beta=beta,
                category=category,
                profile=profile,
            )
        except Exception:
            return ETFInfo(symbol=sym)
