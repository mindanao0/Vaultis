"""Forecast router: GET /api/forecast/{symbol}"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from analysis.backtester import WalkForwardBacktester
from analysis.forecast_chart import generate_forecast_chart
from analysis.forecaster import PriceForecaster
from backend.services.cache_service import CacheService

router = APIRouter(prefix="/api", tags=["forecast"])

ALLOWED_SYMBOLS = ["VOO", "QQQM", "SCHD", "XLV", "GLDM"]
FORECAST_TTL = 6 * 60 * 60  # 6 hours

_cache = CacheService()


@router.get("/forecast/{symbol}")
async def get_forecast(symbol: str, days: int = 30):
    symbol = symbol.strip().upper()
    if symbol not in ALLOWED_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Symbol must be one of {ALLOWED_SYMBOLS}",
        )

    cache_key = f"forecast:{symbol}:{days}"
    cached = await _cache.get(cache_key)
    if cached:
        return cached

    forecaster = PriceForecaster()
    forecast_result = forecaster.forecast(symbol, days)

    chart_b64 = generate_forecast_chart(
        symbol,
        forecaster._forecast_df,
        forecaster._hist_df,
    )

    backtester = WalkForwardBacktester()
    bt_result = backtester.run(symbol)

    result = {
        "symbol": symbol,
        "forecast_days": days,
        "last_price": forecast_result["last_price"],
        "predictions": forecast_result["predictions"],
        "trend": forecast_result["trend"],
        "trend_pct": forecast_result["trend_pct"],
        "backtest": {
            "mae": bt_result["mae"],
            "rmse": bt_result["rmse"],
            "mape": bt_result["mape"],
            "n_folds": bt_result.get("n_folds", 0),
            # เลิกส่ง accuracy_pct (= 100 - MAPE) ซึ่งสื่อว่า "แม่น 97%" อย่างเข้าใจผิด — AUDIT.md M3
            "note": bt_result.get("note", ""),
        },
        "chart_base64": chart_b64,
        "disclaimer": forecast_result["disclaimer"],
        # Roadmap ข้อ 17: Prophet เป็นภาพประกอบระยะสั้น — ตัวพยากรณ์ทางการคือ Monte Carlo
        "official_forecast_note": (
            "ตัวพยากรณ์เชิงตัวเลขทางการของระบบ = Monte Carlo (goals, ผูก μ/σ พอร์ตจริง) "
            "— Prophet ใช้เป็นกรวยความไม่แน่นอนระยะสั้นประกอบเท่านั้น"
        ),
    }

    await _cache.set(cache_key, result, FORECAST_TTL)
    return result
