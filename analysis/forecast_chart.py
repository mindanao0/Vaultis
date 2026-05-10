"""Chart generator for Prophet forecasts."""

from __future__ import annotations

import base64
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def generate_forecast_chart(
    symbol: str,
    forecast_df: pd.DataFrame,
    historical_df: pd.DataFrame,
) -> str:
    fig, ax = plt.subplots(figsize=(12, 6))

    ax.plot(
        historical_df["ds"],
        historical_df["y"],
        color="blue",
        linewidth=1.5,
        label="Historical Price",
    )

    cutoff = historical_df["ds"].max()
    future_mask = forecast_df["ds"] > cutoff
    future_df = forecast_df[future_mask]

    ax.plot(
        future_df["ds"],
        future_df["yhat"],
        color="orange",
        linewidth=1.5,
        label="Forecast",
    )

    ax.fill_between(
        future_df["ds"],
        future_df["yhat_lower"],
        future_df["yhat_upper"],
        color="orange",
        alpha=0.2,
        label="Confidence Interval",
    )

    ax.set_title(f"{symbol} Price Forecast")
    ax.set_xlabel("Date")
    ax.set_ylabel("Price (USD)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100)
    buf.seek(0)
    chart_b64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)

    return chart_b64
