# -*- coding: utf-8 -*-
"""โมดูลจำลองการลงทุนแบบ DCA รายเดือน."""

from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd
import plotly.graph_objects as go
import yfinance as yf

logger = logging.getLogger(__name__)

#: คีย์ใน ``DataFrame.attrs`` ที่พกรายงานช่วงเวลาที่จำลองได้จริงติดไปกับผลลัพธ์
COVERAGE_ATTR = "coverage"


def _fmt_month(stamp: pd.Timestamp) -> str:
    return pd.Timestamp(stamp).strftime("%Y-%m")


def _limited_by_phrase(limited_by: Dict[str, str | None]) -> str:
    """วลีบอกว่ากองไหนเป็นตัวบีบช่วงเวลา และมีราคาแรกวันไหน."""
    if not limited_by:
        return ""
    items = [
        f"{ticker} เพิ่งมีราคาแรก {first_date}" if first_date else f"{ticker} ไม่มีราคาเลย"
        for ticker, first_date in limited_by.items()
    ]
    return "เพราะ " + ", ".join(items)


def _coverage_report(
    prices: pd.DataFrame, monthly_all: pd.DataFrame, monthly: pd.DataFrame
) -> Dict[str, Any]:
    """สรุปว่าจำลองได้กี่เดือนจากข้อมูลกี่เดือน และกองไหนเป็นตัวตัดช่วง."""
    dropped_index = monthly_all.index.difference(monthly.index)

    limited_by: Dict[str, str | None] = {}
    for ticker in monthly_all.columns:
        if len(dropped_index) == 0:
            break
        if not bool(monthly_all.loc[dropped_index, ticker].isna().any()):
            continue
        first_valid = prices[ticker].first_valid_index()
        limited_by[str(ticker)] = (
            None if first_valid is None else pd.Timestamp(first_valid).date().isoformat()
        )

    # กองที่มีราคาช้าที่สุด (หรือไม่มีเลย) คือตัวที่บีบช่วงมากที่สุด — เอาขึ้นก่อน
    ordered = dict(sorted(limited_by.items(), key=lambda kv: kv[1] or "9999-12-31", reverse=True))

    return {
        "available_from": _fmt_month(monthly_all.index[0]) if len(monthly_all) else None,
        "available_to": _fmt_month(monthly_all.index[-1]) if len(monthly_all) else None,
        "months_available": int(len(monthly_all)),
        "simulated_from": _fmt_month(monthly.index[0]) if len(monthly) else None,
        "simulated_to": _fmt_month(monthly.index[-1]) if len(monthly) else None,
        "months_simulated": int(len(monthly)),
        "months_dropped": int(len(monthly_all) - len(monthly)),
        "limited_by": ordered,
    }


def _monthly_first_prices(prices: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """ราคาต้นเดือนของทุกกอง + รายงานว่าตัดเดือนไหนทิ้งไปบ้าง (AUDIT_2026-08-06 B8).

    การจำลองพอร์ตตามน้ำหนักคงที่ต้องมีราคาครบทุกกองในเดือนนั้น ``dropna(how="any")``
    จึงตัด**ทั้งเดือน**ถ้ามีกองใดยังไม่เกิด — ETF ที่เพิ่งเข้าตลาดตัดช่วงต้นของการ
    จำลองทิ้ง ทำให้ ``Total Invested`` ต่ำกว่าการซื้อครบทุกเดือนมาก การตัดยังทำ
    เหมือนเดิม แต่ **ต้องรายงานออกไปให้ผู้ใช้เห็น** ห้ามตัดเงียบ
    (``portfolio/backtest.py::run_portfolio_backtest`` ตัดแบบเดียวกันและอธิบายไว้แล้ว)
    """
    monthly_all = prices.resample("MS").first()
    monthly = monthly_all.dropna(how="any")
    coverage = _coverage_report(prices, monthly_all, monthly)

    if monthly.empty:
        reason = _limited_by_phrase(coverage["limited_by"])
        raise ValueError(
            "ไม่มีเดือนไหนที่ทุกกองมีราคาพร้อมกัน จึงจำลอง DCA ไม่ได้"
            + (f" ({reason})" if reason else "")
        )
    return monthly, coverage


def describe_coverage(coverage: Dict[str, Any] | None) -> str | None:
    """คำเตือนภาษาไทยหนึ่งบรรทัดสำหรับช่วงที่ถูกตัด — ``None`` เมื่อไม่มีเดือนไหนหาย.

    นิยามข้อความมีที่เดียว หน้าจอกับ API อ่านจากฟังก์ชันนี้ตัวเดียวกัน
    """
    if not coverage or not coverage.get("months_dropped"):
        return None

    parts = [
        f"จำลองได้ตั้งแต่ {coverage.get('simulated_from')} ถึง {coverage.get('simulated_to')} "
        f"เท่านั้น ({coverage.get('months_simulated')} เดือน) — "
        f"ตัดไป {int(coverage['months_dropped'])} เดือนจากช่วงข้อมูลที่เริ่ม "
        f"{coverage.get('available_from')}"
    ]
    reason = _limited_by_phrase(coverage.get("limited_by") or {})
    if reason:
        parts.append(reason)
    parts.append("ตัวเลข Total Invested จึงต่ำกว่าการซื้อครบทุกเดือน")
    return " · ".join(parts)


def _normalize_weights(weights: dict[str, float]) -> pd.Series:
    """ตรวจสอบและ normalize weights ให้รวมเป็น 1."""
    if not weights:
        raise ValueError("weights ต้องไม่ว่าง")

    weight_series = pd.Series(weights, dtype=float)
    if (weight_series < 0).any():
        raise ValueError("weights ต้องไม่มีค่าน้อยกว่า 0")

    total_weight = float(weight_series.sum())
    if total_weight <= 0:
        raise ValueError("ผลรวมของ weights ต้องมากกว่า 0")
    return weight_series / total_weight


def _download_adj_close(tickers: list[str], start_date: str) -> pd.DataFrame:
    """ดึงราคา Adj Close สำหรับ tickers ตั้งแต่ start_date; ล้มเหลว → raise (AUDIT.md C1)."""
    from data.fetcher import PriceDataUnavailableError

    try:
        downloaded = yf.download(
            tickers=tickers,
            start=start_date,
            progress=False,
            auto_adjust=False,
            group_by="ticker",
        )
    except Exception as exc:
        raise PriceDataUnavailableError(f"ดึงราคา {tickers} ไม่สำเร็จ: {exc}") from exc

    if downloaded.empty:
        raise PriceDataUnavailableError(f"ดึงราคา {tickers} ได้ผลว่างเปล่า")

    if isinstance(downloaded.columns, pd.MultiIndex):
        prices = downloaded.xs("Adj Close", axis=1, level=1)
    else:
        if "Adj Close" not in downloaded.columns:
            raise PriceDataUnavailableError("ไม่พบคอลัมน์ Adj Close ในข้อมูลที่ดึงมา")
        prices = downloaded[["Adj Close"]].rename(columns={"Adj Close": tickers[0]})

    cleaned = prices.sort_index().ffill().dropna(how="all")
    if cleaned.empty:
        raise PriceDataUnavailableError("ข้อมูลราคาหลังทำความสะอาดว่างเปล่า")
    return cleaned


def simulate_dca(monthly_amount: float, weights: dict, start_date: str) -> dict[str, Any]:
    """
    จำลอง DCA รายเดือน โดยซื้อทุกวันที่ 1 ของเดือน (หรือวันเทรดแรกของเดือน).

    Returns:
        dict ที่มีคีย์:
        - total_invested
        - current_value
        - profit_loss
        - profit_loss_pct
        - history (DataFrame)
        - figure (Plotly Figure)
        - coverage (dict) + coverage_warning (str|None) — ช่วงที่จำลองได้จริง
          และเดือนที่ถูกตัดทิ้งเพราะกองใดกองหนึ่งยังไม่มีราคา (B8)
    """
    try:
        if monthly_amount <= 0:
            raise ValueError("monthly_amount ต้องมากกว่า 0")

        pd.Timestamp(start_date)  # validate รูปแบบวันที่
        normalized_weights = _normalize_weights(weights)
        tickers = list(normalized_weights.index)

        prices = _download_adj_close(tickers=tickers, start_date=start_date)
        prices = prices[tickers].dropna(how="all")

        # ใช้ราคาวันเทรดแรกของแต่ละเดือน เทียบเท่าการซื้อวันที่ 1
        monthly_prices, coverage = _monthly_first_prices(prices)

        shares = pd.Series(0.0, index=tickers)
        total_invested = 0.0
        records: list[dict[str, float | pd.Timestamp]] = []

        for date, row in monthly_prices.iterrows():
            allocation = normalized_weights * monthly_amount
            purchased_shares = allocation / row
            shares += purchased_shares
            total_invested += monthly_amount

            portfolio_value = float((shares * row).sum())
            profit_loss = portfolio_value - total_invested
            profit_loss_pct = (profit_loss / total_invested * 100.0) if total_invested else 0.0

            records.append(
                {
                    "Date": date,
                    "Total Invested": total_invested,
                    "Portfolio Value": portfolio_value,
                    "Profit/Loss": profit_loss,
                    "Profit/Loss %": profit_loss_pct,
                }
            )

        history = pd.DataFrame(records).set_index("Date")
        history.attrs[COVERAGE_ATTR] = coverage
        current_value = float(history["Portfolio Value"].iloc[-1])
        profit_loss = float(history["Profit/Loss"].iloc[-1])
        profit_loss_pct = float(history["Profit/Loss %"].iloc[-1])

        figure = go.Figure()
        figure.add_trace(go.Scatter(x=history.index, y=history["Total Invested"], mode="lines", name="Total Invested"))
        figure.add_trace(go.Scatter(x=history.index, y=history["Portfolio Value"], mode="lines", name="Portfolio Value"))
        figure.update_layout(
            title="DCA: Cumulative Invested vs Portfolio Value",
            xaxis_title="Date",
            yaxis_title="USD",
            template="plotly_white",
        )

        return {
            "total_invested": float(total_invested),
            "current_value": current_value,
            "profit_loss": profit_loss,
            "profit_loss_pct": profit_loss_pct,
            "history": history,
            "figure": figure,
            "coverage": coverage,
            "coverage_warning": describe_coverage(coverage),
        }
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการจำลอง DCA: {exc}") from exc


def simulate_monthly_dca(
    price_df: pd.DataFrame,
    weights: Dict[str, float],
    monthly_investment: float = 1000.0,
) -> pd.DataFrame:
    """คงไว้เพื่อรองรับโค้ดเดิมที่ส่ง price_df เข้ามาโดยตรง.

    ผลลัพธ์พกรายงานช่วงเวลาไว้ที่ ``df.attrs[COVERAGE_ATTR]`` — เดือนที่ถูกตัดทิ้ง
    เพราะกองใดกองหนึ่งยังไม่มีราคา **ต้องถูกแสดงต่อผู้ใช้** ใช้ ``describe_coverage()``
    แปลงเป็นข้อความ (AUDIT_2026-08-06 B8)
    """
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถจำลอง DCA ได้")
        if monthly_investment <= 0:
            raise ValueError("monthly_investment ต้องมากกว่า 0")

        valid_assets = [ticker for ticker in weights if ticker in price_df.columns]
        if not valid_assets:
            raise ValueError("ไม่พบ ticker ใน weights ที่ตรงกับข้อมูลราคา")

        normalized_weights = _normalize_weights({k: weights[k] for k in valid_assets})
        prices = price_df[valid_assets].ffill().dropna(how="all")
        monthly_prices, coverage = _monthly_first_prices(prices)

        shares = pd.Series(0.0, index=valid_assets)
        records: list[dict[str, float | pd.Timestamp]] = []
        total_invested = 0.0

        for date, row in monthly_prices.iterrows():
            allocation = normalized_weights * monthly_investment
            purchased_shares = allocation / row
            shares += purchased_shares
            total_invested += monthly_investment
            current_value = float((shares * row).sum())
            profit_loss = current_value - total_invested
            profit_loss_pct = (profit_loss / total_invested * 100.0) if total_invested else 0.0

            records.append(
                {
                    "Date": date,
                    "Total Invested": total_invested,
                    "Portfolio Value": current_value,
                    "Profit/Loss": profit_loss,
                    "Profit/Loss %": profit_loss_pct,
                }
            )

        history = pd.DataFrame(records).set_index("Date")
        history.attrs[COVERAGE_ATTR] = coverage
        return history
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการจำลอง DCA: {exc}") from exc


if __name__ == "__main__":
    test_weights = {"VOO": 0.40, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.05}
    result = simulate_dca(monthly_amount=500.0, weights=test_weights, start_date="2020-01-01")

    print(f"Total Invested: ${result['total_invested']:,.2f}")
    print(f"Current Value: ${result['current_value']:,.2f}")
    print(f"Profit/Loss: ${result['profit_loss']:,.2f} ({result['profit_loss_pct']:.2f}%)")
    if result["coverage_warning"]:
        print(f"⚠️  {result['coverage_warning']}")
