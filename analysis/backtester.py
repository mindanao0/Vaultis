"""Walk-forward backtester for Prophet forecasts."""

from __future__ import annotations

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from analysis.forecaster import PriceForecaster


class WalkForwardBacktester:
    def run(self, symbol: str, window_months: int = 6, test_days: int = 30) -> dict:
        forecaster = PriceForecaster()
        df = forecaster.fetch_data(symbol, period="2y")
        df = df.sort_values("ds").reset_index(drop=True)

        errors: list[dict] = []
        n_folds = 0

        min_date = df["ds"].min()
        max_date = df["ds"].max()
        current_start = min_date

        while True:
            train_end = current_start + relativedelta(months=window_months)
            test_end = train_end + pd.Timedelta(days=test_days)

            if test_end > max_date:
                break

            train_df = df[df["ds"] < train_end].copy()
            test_df = df[(df["ds"] >= train_end) & (df["ds"] < test_end)].copy()

            if len(train_df) < 50 or len(test_df) < 5:
                current_start += relativedelta(months=1)
                continue

            try:
                model = forecaster.build_model()
                model.fit(train_df[["ds", "y"]])  # univariate — เหตุผลใน build_model

                future = pd.DataFrame({"ds": test_df["ds"].values})
                pred_df = model.predict(future)

                actuals = test_df["y"].values
                preds = pred_df["yhat"].values[: len(actuals)]

                mae = float(np.mean(np.abs(actuals - preds)))
                rmse = float(np.sqrt(np.mean((actuals - preds) ** 2)))
                mape = float(np.mean(np.abs((actuals - preds) / actuals)) * 100)

                errors.append({"mae": mae, "rmse": rmse, "mape": mape})
                n_folds += 1
            except Exception:
                pass

            current_start += relativedelta(months=1)

        if not errors:
            return {
                "symbol": symbol,
                "mae": None,
                "rmse": None,
                "mape": None,
                "n_folds": 0,
                "note": "ไม่มี fold ที่ทดสอบได้",
            }

        avg_mae = float(np.mean([e["mae"] for e in errors]))
        avg_rmse = float(np.mean([e["rmse"] for e in errors]))
        avg_mape = float(np.mean([e["mape"] for e in errors]))

        return {
            "symbol": symbol,
            "mae": round(avg_mae, 4),
            "rmse": round(avg_rmse, 4),
            "mape": round(avg_mape, 4),
            "n_folds": n_folds,
            # AUDIT.md M3: เดิมมี ``accuracy_pct = 100 - MAPE`` ซึ่งทำให้ MAPE 3%
            # กลายเป็น "แม่นยำ 97%" — เป็นการสื่อสารที่ทำให้เข้าใจผิดอย่างมากกับคนที่ใช้เงินจริง
            # (พยากรณ์ราคาแบบ naive "พรุ่งนี้เท่าวันนี้" ก็ได้ MAPE ต่ำเช่นกัน)
            "note": (
                f"MAPE เฉลี่ย {avg_mape:.2f}% จาก {n_folds} ช่วงทดสอบ — "
                "ค่านี้ไม่ใช่ 'ความแม่นยำ' และไม่ได้แปลว่าทำนายทิศทางถูก "
                "ใช้ประกอบการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน"
            ),
        }
