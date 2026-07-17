"""Prophet-based price forecaster for ETF symbols."""

from __future__ import annotations

import pandas as pd
import yfinance as yf
from prophet import Prophet

from analysis.ta_compat import ta


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
        # univariate โดยตั้งใจ: regressor เดิม (volume/rsi) ต้องเดาค่าอนาคตด้วยค่าคงที่
        # = ไม่ได้ให้ข้อมูลจริง และเส้นทาง extra-regressor ของ prophet 1.1.6 พังกับ pandas 2.2
        # (Prophet เป็นแค่กรวยประกอบระยะสั้น — ตัวพยากรณ์ทางการคือ Monte Carlo, Roadmap ข้อ 17)
        return Prophet(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=False,
            changepoint_prior_scale=0.05,
        )

    def forecast(self, symbol: str, days: int = 30) -> dict:
        df = self.fetch_data(symbol)

        model = self.build_model()
        model.fit(df[["ds", "y"]])

        future = model.make_future_dataframe(periods=days)
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
            "disclaimer": (
                "พยากรณ์ระยะสั้นอ่านเป็น 'กรวยความไม่แน่นอน' (yhat_lower–yhat_upper) เท่านั้น "
                "ห้ามใช้ yhat เป็นราคาเป้าจุดเดียว — การพยากรณ์เชิงตัวเลขทางการของระบบคือ "
                "Monte Carlo ที่ระบบ Goals (horizon ระยะยาวของ DCA) · เพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน"
            ),
        }
