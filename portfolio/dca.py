"""โมดูลจำลองการลงทุนแบบ DCA รายเดือน."""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf


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
    """ดึงราคา Adj Close สำหรับ tickers ตั้งแต่ start_date ถึงปัจจุบัน."""
    try:
        downloaded = yf.download(
            tickers=tickers,
            start=start_date,
            progress=False,
            auto_adjust=False,
            group_by="ticker",
        )
    except Exception:
        st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
        return pd.DataFrame()
    if downloaded.empty:
        st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
        return pd.DataFrame()

    if isinstance(downloaded.columns, pd.MultiIndex):
        prices = downloaded.xs("Adj Close", axis=1, level=1)
    else:
        if "Adj Close" not in downloaded.columns:
            st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
            return pd.DataFrame()
        prices = downloaded[["Adj Close"]].rename(columns={"Adj Close": tickers[0]})

    cleaned = prices.sort_index().ffill().dropna(how="all")
    if cleaned.empty:
        st.warning("ไม่สามารถดึงข้อมูลได้ กรุณารอสักครู่")
        return pd.DataFrame()
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
    """
    try:
        if monthly_amount <= 0:
            raise ValueError("monthly_amount ต้องมากกว่า 0")

        pd.Timestamp(start_date)  # validate รูปแบบวันที่
        normalized_weights = _normalize_weights(weights)
        tickers = list(normalized_weights.index)

        prices = _download_adj_close(tickers=tickers, start_date=start_date)
        if prices.empty:
            return {
                "total_invested": 0.0,
                "current_value": 0.0,
                "profit_loss": 0.0,
                "profit_loss_pct": 0.0,
                "history": pd.DataFrame(),
                "figure": go.Figure(),
            }
        prices = prices[tickers].dropna(how="all")

        # ใช้ราคาวันเทรดแรกของแต่ละเดือน เทียบเท่าการซื้อวันที่ 1
        monthly_prices = prices.resample("MS").first().dropna(how="any")
        if monthly_prices.empty:
            raise ValueError("ไม่พบข้อมูลรายเดือนสำหรับช่วงเวลาที่ระบุ")

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
        }
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการจำลอง DCA: {exc}") from exc


def simulate_monthly_dca(
    price_df: pd.DataFrame,
    weights: Dict[str, float],
    monthly_investment: float = 1000.0,
) -> pd.DataFrame:
    """คงไว้เพื่อรองรับโค้ดเดิมที่ส่ง price_df เข้ามาโดยตรง."""
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
        monthly_prices = prices.resample("MS").first().dropna(how="any")

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

        return pd.DataFrame(records).set_index("Date")
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการจำลอง DCA: {exc}") from exc


if __name__ == "__main__":
    test_weights = {"VOO": 0.40, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.05}
    result = simulate_dca(monthly_amount=500.0, weights=test_weights, start_date="2020-01-01")

    print(f"Total Invested: ${result['total_invested']:,.2f}")
    print(f"Current Value: ${result['current_value']:,.2f}")
    print(f"Profit/Loss: ${result['profit_loss']:,.2f} ({result['profit_loss_pct']:.2f}%)")
