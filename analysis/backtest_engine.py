"""Vectorbt-based backtesting engine with RSI+MACD strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta
import vectorbt as vbt
import yfinance as yf


class BacktestEngine:
    def fetch_data(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        df = yf.download(symbol, start=start, end=end, progress=False)
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
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
    ):
        close = df["Close"]

        rsi = ta.rsi(close, length=rsi_period)
        macd_df = ta.macd(close, fast=macd_fast, slow=macd_slow, signal=macd_signal)

        macd_col = f"MACD_{macd_fast}_{macd_slow}_{macd_signal}"
        sig_col = f"MACDs_{macd_fast}_{macd_slow}_{macd_signal}"

        macd_line = macd_df[macd_col]
        signal_line = macd_df[sig_col]

        # MACD bullish cross: MACD crosses above signal (prev below, curr above)
        macd_cross_up = (macd_line.shift(1) < signal_line.shift(1)) & (macd_line >= signal_line)
        # MACD bearish cross: MACD crosses below signal
        macd_cross_down = (macd_line.shift(1) > signal_line.shift(1)) & (macd_line <= signal_line)

        entries = (rsi < rsi_oversold) & macd_cross_up
        exits = (rsi > rsi_overbought) & macd_cross_down

        return entries.fillna(False), exits.fillna(False)

    def run(
        self,
        symbol: str,
        start: str,
        end: str,
        strategy_params: dict | None = None,
    ) -> dict:
        df = self.fetch_data(symbol, start, end)
        close = df["Close"]

        params = strategy_params or {}
        entries, exits = self.rsi_macd_strategy(df, **params)

        portfolio = vbt.Portfolio.from_signals(
            close,
            entries,
            exits,
            init_cash=10_000,
            fees=0.001,
        )

        num_trades = int(portfolio.trades.count())

        if num_trades > 0:
            total_return = float(portfolio.total_return() * 100)
            sharpe_ratio = float(portfolio.sharpe_ratio())
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
            "total_return": round(total_return, 4),
            "sharpe_ratio": round(sharpe_ratio, 4),
            "max_drawdown": round(max_drawdown, 4),
            "win_rate": round(win_rate, 4),
            "num_trades": num_trades,
            "benchmark_return": round(bh_return, 4),
            "outperformed": total_return > bh_return,
        }

    def optimize(self, symbol: str, start: str, end: str) -> dict:
        df = self.fetch_data(symbol, start, end)

        rsi_periods = [7, 10, 14, 21]
        rsi_oversolds = [25, 30, 35]

        best_sharpe = float("-inf")
        best_params: dict = {}
        all_results: list[dict] = []

        for period in rsi_periods:
            for oversold in rsi_oversolds:
                params = {"rsi_period": period, "rsi_oversold": oversold}
                try:
                    entries, exits = self.rsi_macd_strategy(df, rsi_period=period, rsi_oversold=oversold)
                    close = df["Close"]
                    portfolio = vbt.Portfolio.from_signals(
                        close, entries, exits, init_cash=10_000, fees=0.001
                    )
                    num_trades = int(portfolio.trades.count())
                    sharpe = float(portfolio.sharpe_ratio()) if num_trades > 0 else 0.0
                    if np.isnan(sharpe):
                        sharpe = 0.0
                except Exception:
                    sharpe = 0.0

                all_results.append({**params, "sharpe_ratio": round(sharpe, 4)})

                if sharpe > best_sharpe:
                    best_sharpe = sharpe
                    best_params = params

        return {
            "best_params": best_params,
            "best_sharpe": round(best_sharpe, 4),
            "all_results": all_results,
        }
