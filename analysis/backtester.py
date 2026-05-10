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
                model.fit(train_df[["ds", "y", "volume", "rsi"]])

                vol_mean = float(train_df["volume"].tail(20).mean())
                rsi_last = float(train_df["rsi"].iloc[-1])

                future = pd.DataFrame({"ds": test_df["ds"].values})
                future["volume"] = vol_mean
                future["rsi"] = rsi_last

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
                "accuracy_pct": None,
            }

        avg_mae = float(np.mean([e["mae"] for e in errors]))
        avg_rmse = float(np.mean([e["rmse"] for e in errors]))
        avg_mape = float(np.mean([e["mape"] for e in errors]))
        accuracy_pct = round(100.0 - avg_mape, 2)

        return {
            "symbol": symbol,
            "mae": round(avg_mae, 4),
            "rmse": round(avg_rmse, 4),
            "mape": round(avg_mape, 4),
            "n_folds": n_folds,
            "accuracy_pct": accuracy_pct,
        }
