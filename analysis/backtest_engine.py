"""Vectorbt-based backtesting engine with RSI+MACD strategy.

นโยบายตัวเลข (C1 — fail loud ห้ามกุตัวเลข): ทุกช่องที่ **ไม่นิยาม** คืน ``None``
ห้ามคืน ``0.0`` เพราะ 0.0 เป็นคำตอบที่อ่านได้ว่า "ผลตอบแทน 0% / Sharpe 0 / ไม่ขาดทุนเลย"
ซึ่งคนละเรื่องกับ "กลยุทธ์ไม่เคยเข้าเทรดจึงไม่มีอะไรให้วัด" (AUDIT_2026-08-06 B3.1–B3.3)
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import vectorbt as vbt
import yfinance as yf

from analysis.ta_compat import ta
from data.fetcher import PriceDataUnavailableError

# นโยบาย retry เดียวกับ ``data/fetcher`` — เทสต์ patch ``_RETRY_SLEEP_SEC`` เป็น 0
_FETCH_ATTEMPTS = 3
_RETRY_SLEEP_SEC = 2


def _round_or_none(value, digits: int = 4) -> float | None:
    """ปัดเศษเฉพาะตัวเลขจริง — ``None`` และ ``NaN`` คงความหมาย "ไม่นิยาม" ไว้.

    NaN ห้ามหลุดออกไปกับ payload: JSON ไม่มี NaN, และ ``float('nan')`` เป็น truthy
    ทำให้ผู้เรียกที่เช็ค ``if value:`` เข้าใจว่ามีค่า
    """
    if value is None:
        return None
    number = float(value)
    if np.isnan(number):
        return None
    return round(number, digits)


class BacktestEngine:
    def fetch_data(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """ดึง OHLCV ของ ``symbol`` — ล้มเหลว = ``PriceDataUnavailableError`` เสมอ.

        B3.5: เดิมยิง ``yf.download`` ครั้งเดียวแล้วโยน ``ValueError`` ซึ่ง router
        แปลเป็น **400 = คำขอของผู้เรียกผิด** ทั้งที่ความจริงคือแหล่งข้อมูลต้นทางล่ม
        (สาขา ``except PriceDataUnavailableError → 503`` จึงเป็นโค้ดตาย) ตอนนี้ retry
        3 ครั้งและใช้ชนิดข้อผิดพลาดเดียวกับ ``data/fetcher`` เพื่อให้ผู้เรียกแยก
        "ต้นทางล่ม (ลองใหม่ได้)" ออกจาก "คำขอใช้ไม่ได้" ได้จริง
        """
        last_error: Exception | None = None
        for attempt in range(_FETCH_ATTEMPTS):
            try:
                # auto_adjust=True ระบุชัด: ราคา adjusted มาตรฐานเดียวทั้งระบบ (AUDIT.md M1)
                df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
                if df.empty or "Close" not in df.columns:
                    # yfinance คืนกรอบว่างเมื่อดึงไม่ได้ (ไม่ raise) — ต้องนับเป็นความล้มเหลว
                    raise ValueError("ผลว่าง")
                return df
            except Exception as exc:
                last_error = exc
                if attempt < _FETCH_ATTEMPTS - 1 and _RETRY_SLEEP_SEC:
                    time.sleep(_RETRY_SLEEP_SEC)

        raise PriceDataUnavailableError(
            f"ดึงข้อมูลราคา {symbol} ({start} – {end}) ไม่สำเร็จหลังลอง "
            f"{_FETCH_ATTEMPTS} ครั้ง: {last_error}"
        ) from last_error

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
        detail: str | None = None

        if num_trades > 0:
            total_return = _round_or_none(float(portfolio.total_return()) * 100)
            sharpe_ratio = _round_or_none(portfolio.sharpe_ratio())
            max_drawdown = _round_or_none(float(portfolio.max_drawdown()) * 100)
            win_rate = _round_or_none(float(portfolio.trades.win_rate()) * 100)
        else:
            # B3.1: เดิมยัด 0.0 ทั้งสี่ช่อง แล้วบรรทัดถัดมาสรุป outperformed จากศูนย์ปลอมนั้น
            # → หน้าต่างตลาดหมีที่กลยุทธ์ไม่เคยเข้าเทรดเลยถูกรายงานว่า "ชนะดัชนี"
            # พร้อม maxDD 0.0 (= ไม่เคยขาดทุน) ซึ่งไม่มีอะไรจริงสักช่อง
            total_return = sharpe_ratio = max_drawdown = win_rate = None
            detail = (
                "กลยุทธ์ไม่ส่งสัญญาณเข้าซื้อเลยในช่วงนี้ (0 เทรด) — "
                "ไม่มีผลตอบแทน / Sharpe / Max Drawdown / Win Rate ให้รายงาน "
                "และเทียบกับ Buy & Hold ไม่ได้ (ไม่ใช่ 'ผลตอบแทน 0%')"
            )

        bh_return = _round_or_none(float((close.iloc[-1] / close.iloc[0] - 1) * 100))
        if bh_return is None and detail is None:
            detail = "คำนวณผลตอบแทน Buy & Hold ไม่ได้ (ราคาต้นช่วง/ปลายช่วงไม่ครบ)"

        if total_return is None or bh_return is None:
            outperformed = None
        else:
            outperformed = total_return > bh_return

        return {
            "symbol": symbol,
            "start": start,
            "end": end,
            "strategy_used": strategy_used,
            "total_return": total_return,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "num_trades": num_trades,
            "benchmark_return": bh_return,
            "outperformed": outperformed,
            "detail": detail,
        }

    def _sharpe_for(self, df, rsi_period: int, rsi_oversold: float) -> float | None:
        """Sharpe ของคอมโบพารามิเตอร์หนึ่ง — ``None`` = ไม่นิยาม (ไม่มีเทรด / คำนวณไม่ได้).

        B3.2: เดิมคืน ``0.0`` ทั้งกรณีไม่มีเทรด, Sharpe เป็น NaN และ ``except Exception``
        ⇒ ``optimize()`` จัดอันดับด้วย ``max()`` จึงยก "ไม่มีข้อมูล" ขึ้นเหนือคอมโบที่
        เทรดจริงแล้วขาดทุน และเพราะ ``0.0 > -inf`` เสมอ ด่าน ``if not best_params:``
        กลายเป็นโค้ดตาย · ไม่จับ exception แล้ว — บั๊กจริงต้องดังถึงผู้เรียก
        """
        entries, exits, _ = self.rsi_macd_strategy(
            df, rsi_period=rsi_period, rsi_oversold=rsi_oversold
        )
        portfolio = vbt.Portfolio.from_signals(
            df["Close"], entries, exits, init_cash=10_000, fees=0.001, freq="D"
        )
        if int(portfolio.trades.count()) == 0:
            return None
        sharpe = float(portfolio.sharpe_ratio())
        return None if np.isnan(sharpe) else sharpe

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

        best_sharpe_train: float | None = None
        best_params: dict = {}
        all_results: list[dict] = []

        for period in rsi_periods:
            for oversold in rsi_oversolds:
                sharpe = self._sharpe_for(train_df, period, oversold)
                all_results.append(
                    {
                        "rsi_period": period,
                        "rsi_oversold": oversold,
                        "train_sharpe": _round_or_none(sharpe),
                    }
                )
                # คอมโบที่ไม่มีเทรดเลย = ไม่มีตัวเลขให้จัดอันดับ ห้ามนับเป็น 0.0
                if sharpe is None:
                    continue
                if best_sharpe_train is None or sharpe > best_sharpe_train:
                    best_sharpe_train = sharpe
                    best_params = {"rsi_period": period, "rsi_oversold": oversold}

        train_period = f"{train_df.index[0]:%Y-%m-%d} – {train_df.index[-1]:%Y-%m-%d}"
        test_period = f"{test_df.index[0]:%Y-%m-%d} – {test_df.index[-1]:%Y-%m-%d}"

        if not best_params:
            return {
                "best_params": {},
                "train_period": train_period,
                "test_period": test_period,
                "train_sharpe": None,
                "test_sharpe": None,
                "all_results": all_results,
                "note": (
                    "ไม่พบพารามิเตอร์ที่ให้สัญญาณเลยในช่วง train — "
                    "ไม่มีตัวเลขให้เทียบ (ไม่ใช่ Sharpe = 0)"
                ),
            }

        test_sharpe = self._sharpe_for(test_df, **best_params)

        note = (
            "train_sharpe คือผลบนข้อมูลที่ใช้จูน (มองโลกในแง่ดีเสมอ) — "
            "ให้ดู test_sharpe ซึ่งเป็นผลบนช่วงที่พารามิเตอร์ไม่เคยเห็น "
            "ถ้า test ต่ำกว่า train มาก แปลว่ากลยุทธ์ overfit "
            "และผลย้อนหลังไม่รับประกันผลในอนาคต"
        )
        if best_sharpe_train < 0:
            # B3.3: เดิม max(best_sharpe_train, 0.0) กลบข้อเท็จจริงว่า "จูนแล้วแพ้ทุกชุด"
            note += " ⚠️ คอมโบที่ดีที่สุดในช่วง train ยังได้ Sharpe ติดลบ — จูนแล้วแพ้ทุกชุด"
        if test_sharpe is None:
            note += (
                " ⚠️ พารามิเตอร์ที่เลือกไม่ส่งสัญญาณเลยในช่วง test → test_sharpe ไม่นิยาม "
                "(ไม่ใช่ 0) จึงไม่มีหลักฐานนอกกลุ่มตัวอย่างสนับสนุนพารามิเตอร์ชุดนี้"
            )

        return {
            "best_params": best_params,
            "train_period": train_period,
            "test_period": test_period,
            "train_sharpe": _round_or_none(best_sharpe_train),
            # ตัวเลขที่ควรเชื่อ: ผลบนช่วงที่พารามิเตอร์ไม่เคยเห็น
            "test_sharpe": _round_or_none(test_sharpe),
            "all_results": all_results,
            "note": note,
        }
