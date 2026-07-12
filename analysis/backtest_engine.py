"""Vectorbt-based backtesting engine with RSI+MACD strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import vectorbt as vbt
import yfinance as yf

from analysis.ta_compat import ta


class BacktestEngine:
    def fetch_data(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        # auto_adjust=True ระบุชัด: ราคา adjusted มาตรฐานเดียวทั้งระบบ (AUDIT.md M1)
        df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        if df.empty or "Close" not in df.columns:
            raise ValueError(f"ดึงข้อมูลราคา {symbol} ไม่สำเร็จ (ผลว่าง)")
        return df

    def rsi_macd_strategy(
        self,
        df: pd.DataFrame,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        debug: bool = False,
    ):
        close = df["Close"]

        rsi = ta.rsi(close, length=rsi_period)
        macd_df = ta.macd(close, fast=macd_fast, slow=macd_slow, signal=macd_signal)

        macd_col = f"MACD_{macd_fast}_{macd_slow}_{macd_signal}"
        sig_col = f"MACDs_{macd_fast}_{macd_slow}_{macd_signal}"

        macd_line = macd_df[macd_col]
        signal_line = macd_df[sig_col]

        # MACD bullish cross: MACD crosses above signal line
        macd_cross_up = (macd_line.shift(1) < signal_line.shift(1)) & (macd_line >= signal_line)
        # MACD bearish cross: MACD crosses below signal line
        macd_cross_down = (macd_line.shift(1) > signal_line.shift(1)) & (macd_line <= signal_line)

        rsi_oversold_raw = (rsi < rsi_oversold).fillna(False)
        rsi_overbought_raw = (rsi > rsi_overbought).fillna(False)
        cross_up_raw = macd_cross_up.fillna(False)
        cross_down_raw = macd_cross_down.fillna(False)

        if debug:
            print(f"  RSI oversold (<{rsi_oversold}) signals  : {rsi_oversold_raw.sum()}")
            print(f"  MACD bullish cross signals           : {cross_up_raw.sum()}")

        # 3-day lookback window: signal fires if either condition occurred in the last 3 bars.
        # Use fillna(0) before astype(bool) to prevent NaN -> True misfire on first rows.
        rsi_ov_win = rsi_oversold_raw.rolling(3).max().fillna(0).astype(bool)
        rsi_ob_win = rsi_overbought_raw.rolling(3).max().fillna(0).astype(bool)
        cross_up_win = cross_up_raw.rolling(3).max().fillna(0).astype(bool)
        cross_down_win = cross_down_raw.rolling(3).max().fillna(0).astype(bool)

        entries = (rsi_ov_win & cross_up_win).fillna(False)
        exits = (rsi_ob_win & cross_down_win).fillna(False)

        if debug:
            print(f"  Combined entry signals (3-day window): {entries.sum()}")
            print(f"  Combined exit signals  (3-day window): {exits.sum()}")

        # Fallback: RSI-only when the combined window strategy produces no entries.
        # ต้องรายงาน strategy ที่ใช้จริงกลับไปเสมอ — ห้ามสลับเงียบ (AUDIT.md M2)
        strategy_used = "rsi_macd_3day_window"
        if entries.sum() == 0:
            if debug:
                print("  → 0 combined entries; falling back to RSI-only strategy")
            entries = rsi_oversold_raw.copy()
            exits = rsi_overbought_raw.copy()
            strategy_used = "rsi_only_fallback"
            if debug:
                print(f"  Fallback entry signals (RSI-only): {entries.sum()}")
                print(f"  Fallback exit signals  (RSI-only): {exits.sum()}")

        return entries, exits, strategy_used

    def run(
        self,
        symbol: str,
        start: str,
        end: str,
        strategy_params: dict | None = None,
        debug: bool = False,
    ) -> dict:
        df = self.fetch_data(symbol, start, end)
        close = df["Close"]

        params = {**(strategy_params or {}), "debug": debug}
        entries, exits, strategy_used = self.rsi_macd_strategy(df, **params)

        portfolio = vbt.Portfolio.from_signals(
            close,
            entries,
            exits,
            init_cash=10_000,
            fees=0.001,
            freq="D",
        )

        num_trades = int(portfolio.trades.count())

        if num_trades > 0:
            total_return = float(portfolio.total_return() * 100)
            sharpe_ratio = float(portfolio.sharpe_ratio())
            if np.isnan(sharpe_ratio):
                sharpe_ratio = 0.0
            max_drawdown = float(portfolio.max_drawdown() * 100)
            raw_wr = portfolio.trades.win_rate()
            win_rate = float(raw_wr * 100) if not np.isnan(raw_wr) else 0.0
        else:
            total_return = 0.0
            sharpe_ratio = 0.0
            max_drawdown = 0.0
            win_rate = 0.0

        bh_return = float((close.iloc[-1] / close.iloc[0] - 1) * 100)

        return {
            "symbol": symbol,
            "start": start,
            "end": end,
            "strategy_used": strategy_used,
            "total_return": round(total_return, 4),
            "sharpe_ratio": round(sharpe_ratio, 4),
            "max_drawdown": round(max_drawdown, 4),
            "win_rate": round(win_rate, 4),
            "num_trades": num_trades,
            "benchmark_return": round(bh_return, 4),
            "outperformed": total_return > bh_return,
        }

    def _sharpe_for(self, df, rsi_period: int, rsi_oversold: float) -> float:
        try:
            entries, exits, _ = self.rsi_macd_strategy(
                df, rsi_period=rsi_period, rsi_oversold=rsi_oversold
            )
            portfolio = vbt.Portfolio.from_signals(
                df["Close"], entries, exits, init_cash=10_000, fees=0.001, freq="D"
            )
            if int(portfolio.trades.count()) == 0:
                return 0.0
            sharpe = float(portfolio.sharpe_ratio())
            return 0.0 if np.isnan(sharpe) else sharpe
        except Exception:
            return 0.0

    def optimize(self, symbol: str, start: str, end: str, train_ratio: float = 0.7) -> dict:
        """หาพารามิเตอร์ที่ดีที่สุดบนช่วง train แล้ว **รายงานผลจากช่วง test ที่ไม่เคยเห็น**.

        AUDIT.md M2: เดิม optimize บนข้อมูลทั้งชุดแล้วรายงาน Sharpe จากชุดเดียวกัน
        (in-sample) ซึ่งเป็นการ overfit — ตัวเลขที่ได้สวยเสมอและไม่บอกอะไรเกี่ยวกับอนาคต
        """
        df = self.fetch_data(symbol, start, end)
        split = int(len(df) * train_ratio)
        if split < 60 or len(df) - split < 30:
            raise ValueError("ข้อมูลไม่พอแบ่งช่วง train/test สำหรับการ optimize")

        train_df, test_df = df.iloc[:split], df.iloc[split:]

        rsi_periods = [7, 10, 14, 21]
        rsi_oversolds = [25, 30, 35]

        best_sharpe_train = float("-inf")
        best_params: dict = {}
        all_results: list[dict] = []

        for period in rsi_periods:
            for oversold in rsi_oversolds:
                sharpe = self._sharpe_for(train_df, period, oversold)
                all_results.append(
                    {"rsi_period": period, "rsi_oversold": oversold, "train_sharpe": round(sharpe, 4)}
                )
                if sharpe > best_sharpe_train:
                    best_sharpe_train = sharpe
                    best_params = {"rsi_period": period, "rsi_oversold": oversold}

        if not best_params:
            return {
                "best_params": {},
                "train_sharpe": 0.0,
                "test_sharpe": 0.0,
                "all_results": all_results,
                "note": "ไม่พบพารามิเตอร์ที่ให้สัญญาณเลยในช่วง train",
            }

        test_sharpe = self._sharpe_for(test_df, **best_params)

        return {
            "best_params": best_params,
            "train_period": f"{train_df.index[0]:%Y-%m-%d} – {train_df.index[-1]:%Y-%m-%d}",
            "test_period": f"{test_df.index[0]:%Y-%m-%d} – {test_df.index[-1]:%Y-%m-%d}",
            "train_sharpe": round(max(best_sharpe_train, 0.0), 4),
            # ตัวเลขที่ควรเชื่อ: ผลบนช่วงที่พารามิเตอร์ไม่เคยเห็น
            "test_sharpe": round(test_sharpe, 4),
            "all_results": all_results,
            "note": (
                "train_sharpe คือผลบนข้อมูลที่ใช้จูน (มองโลกในแง่ดีเสมอ) — "
                "ให้ดู test_sharpe ซึ่งเป็นผลบนช่วงที่พารามิเตอร์ไม่เคยเห็น "
                "ถ้า test ต่ำกว่า train มาก แปลว่ากลยุทธ์ overfit "
                "และผลย้อนหลังไม่รับประกันผลในอนาคต"
            ),
        }
