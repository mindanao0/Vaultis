"""Prophet-based price forecaster for ETF symbols."""

from __future__ import annotations

import pandas as pd
import yfinance as yf
import pandas_ta as ta
from prophet import Prophet


class PriceForecaster:
    def fetch_data(self, symbol: str, period: str = "2y") -> pd.DataFrame:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period)
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.reset_index()

        df = df.rename(columns={"Date": "ds", "Close": "y"})
        df["ds"] = pd.to_datetime(df["ds"]).dt.tz_localize(None)

        df["rsi"] = ta.rsi(df["y"], length=14)
        df["volume_ma20"] = df["Volume"].rolling(20).mean()
        df["volume"] = df["Volume"]

        df = df.dropna(subset=["rsi", "volume_ma20"])
        return df[["ds", "y", "volume", "rsi", "volume_ma20"]].reset_index(drop=True)

    def build_model(self) -> Prophet:
        model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=False,
            changepoint_prior_scale=0.05,
        )
        model.add_regressor("volume")
        model.add_regressor("rsi")
        return model

    def forecast(self, symbol: str, days: int = 30) -> dict:
        df = self.fetch_data(symbol)

        model = self.build_model()
        model.fit(df[["ds", "y", "volume", "rsi"]])

        future = model.make_future_dataframe(periods=days)

        vol_mean = float(df["volume"].tail(20).mean())
        rsi_last = float(df["rsi"].iloc[-1])

        future = future.merge(df[["ds", "volume", "rsi"]], on="ds", how="left")
        future["volume"] = future["volume"].fillna(vol_mean)
        future["rsi"] = future["rsi"].fillna(rsi_last)

        raw_forecast = model.predict(future)

        # Store for external callers (e.g. chart generator)
        self._hist_df = df
        self._forecast_df = raw_forecast

        last_price = float(df["y"].iloc[-1])
        future_rows = raw_forecast.tail(days)

        predictions = [
            {
                "date": row["ds"].strftime("%Y-%m-%d"),
                "yhat": round(float(row["yhat"]), 2),
                "yhat_lower": round(float(row["yhat_lower"]), 2),
                "yhat_upper": round(float(row["yhat_upper"]), 2),
            }
            for _, row in future_rows.iterrows()
        ]

        last_pred = predictions[-1]["yhat"]
        trend_pct = round((last_pred - last_price) / last_price * 100, 2)

        if trend_pct > 1:
            trend = "up"
        elif trend_pct < -1:
            trend = "down"
        else:
            trend = "sideways"

        return {
            "symbol": symbol,
            "forecast_days": days,
            "last_price": round(last_price, 2),
            "predictions": predictions,
            "trend": trend,
            "trend_pct": trend_pct,
            "disclaimer": "การพยากรณ์นี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน",
        }
