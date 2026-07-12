"""Portfolio service — บาง ๆ ครอบ ledger เดียวของระบบ (portfolio/tracker.py, CSV).

AUDIT.md H2/H8: เดิมมี ledger 2 ชุดที่ไม่ sync กัน — CSV (dashboard + AI advisor ใช้)
กับตาราง SQLite ``transactions`` (API ใช้) — และฝั่ง SQLite **พังมาตลอด**:
``Transaction(**payload.model_dump(), ticker=...)`` โยน TypeError ทุกครั้ง
ทำให้ ``POST /api/portfolio/add`` ไม่เคยบันทึกอะไรได้เลย (ตาราง 0 แถว)

ตอนนี้ทุกช่องทางอ่าน/เขียน ledger เดียวกัน และคืน dict ที่ serialize เป็น JSON ได้
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from portfolio import tracker

from ..schemas import TransactionCreate


def _clean(value: Any) -> Any:
    """NaN → None เพื่อให้ JSONResponse serialize ได้ และไม่หลอกว่าเป็น 0."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y-%m-%d")
    return value


def add_transaction(payload: TransactionCreate) -> dict[str, Any]:
    row = tracker.add_transaction(
        date=str(payload.date),
        ticker=payload.ticker.upper(),
        shares=float(payload.shares),
        price_usd=float(payload.price_usd),
        fx_rate_thb=float(payload.fx_rate),
        amount_thb=float(payload.amount_thb),
        note=str(payload.note or ""),
    )
    return {k: _clean(v) for k, v in row.items()}


def delete_transaction(tx_id: str) -> bool:
    return tracker.delete_transaction(tx_id)


def get_history() -> list[dict[str, Any]]:
    df = tracker.get_transactions()
    if df.empty:
        return []
    records = df.to_dict(orient="records")
    return [{k: _clean(v) for k, v in row.items()} for row in records]


def get_holdings() -> list[dict[str, Any]]:
    """สรุปรายสินทรัพย์ — ``price_ok=False`` แปลว่าราคาปัจจุบันดึงไม่ได้ (ค่าเป็น None)."""
    df = tracker.get_portfolio_summary()
    if df.empty:
        return []
    result: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        result.append(
            {
                "ticker": row["Ticker"],
                "shares": _clean(row["Shares"]),
                "avg_cost_usd": _clean(row["Avg Cost (USD)"]),
                "invested_usd": _clean(row["Invested (USD)"]),
                "invested_thb": _clean(row["Invested (THB)"]),
                "current_price_usd": _clean(row["Current Price (USD)"]),
                "current_value_usd": _clean(row["Current Value (USD)"]),
                "current_value_thb": _clean(row["Current Value (THB)"]),
                "pnl_usd": _clean(row["P&L (USD)"]),
                "pnl_thb": _clean(row["P&L (THB)"]),
                "return_pct": _clean(row["Return (%)"]),
                "fee": _clean(row["Fee (THB)"]),
                "price_ok": bool(row["Price OK"]),
            }
        )
    return result


def get_portfolio_summary() -> dict[str, Any]:
    holdings = get_holdings()
    totals = tracker.get_total_summary()
    return {
        "holdings_count": len(holdings),
        "invested_usd": sum(h["invested_usd"] or 0 for h in holdings),
        "invested_thb": _clean(totals["total_invested_thb"]),
        "current_value_usd": sum(h["current_value_usd"] or 0 for h in holdings if h["price_ok"]),
        "current_value_thb": _clean(totals["current_value_thb"]),
        "pnl_thb": _clean(totals["total_pnl_thb"]),
        "pnl_usd": sum(h["pnl_usd"] or 0 for h in holdings if h["price_ok"]),
        "return_pct": _clean(totals["total_return_pct"]),
        "total_fee": _clean(totals["total_fee_thb"]),
        # ราคาที่ดึงไม่ได้ต้องบอกผู้ใช้ — ห้ามซ่อนแล้วให้ตัวเลขดูสมบูรณ์ (AUDIT.md C1)
        "missing_prices": list(totals.get("missing_prices") or []),
    }
