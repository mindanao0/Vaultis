import pandas as pd
import pandas_ta as ta
from typing import Optional


class CrossoverDetector:
    def detect_macd_cross(self, df: pd.DataFrame) -> Optional[str]:
        macd = df.ta.macd(fast=12, slow=26, signal=9)
        if macd is None or len(macd) < 3:
            return None
        macd_col = [c for c in macd.columns if c.startswith("MACD_")][0]
        signal_col = [c for c in macd.columns if c.startswith("MACDs_")][0]
        prev_diff = macd[macd_col].iloc[-2] - macd[signal_col].iloc[-2]
        curr_diff = macd[macd_col].iloc[-1] - macd[signal_col].iloc[-1]
        if prev_diff < 0 and curr_diff > 0:
            return "bullish"
        if prev_diff > 0 and curr_diff < 0:
            return "bearish"
        return None

    def detect_golden_cross(self, df: pd.DataFrame, lookback_days: int = 3) -> bool:
        ma50 = df["Close"].rolling(50).mean()
        ma200 = df["Close"].rolling(200).mean()
        for i in range(1, lookback_days + 1):
            if (ma50.iloc[-i - 1] < ma200.iloc[-i - 1] and
                    ma50.iloc[-i] > ma200.iloc[-i]):
                return True
        return False

    def detect_death_cross(self, df: pd.DataFrame, lookback_days: int = 3) -> bool:
        ma50 = df["Close"].rolling(50).mean()
        ma200 = df["Close"].rolling(200).mean()
        for i in range(1, lookback_days + 1):
            if (ma50.iloc[-i - 1] > ma200.iloc[-i - 1] and
                    ma50.iloc[-i] < ma200.iloc[-i]):
                return True
        return False

    def detect_bb_squeeze(self, df: pd.DataFrame) -> bool:
        bb = df.ta.bbands(length=20, std=2)
        if bb is None:
            return False
        upper_col = [c for c in bb.columns if c.startswith("BBU")][0]
        lower_col = [c for c in bb.columns if c.startswith("BBL")][0]
        mid_col = [c for c in bb.columns if c.startswith("BBM")][0]
        bandwidth = (bb[upper_col] - bb[lower_col]) / bb[mid_col]
        current_bw = bandwidth.iloc[-1]
        avg_bw = bandwidth.rolling(50).mean().iloc[-1]
        return current_bw < avg_bw * 0.5

    def detect_volume_spike(self, df: pd.DataFrame, multiplier: float = 2.0) -> bool:
        vol_ma20 = df["Volume"].rolling(20).mean().iloc[-1]
        current_vol = df["Volume"].iloc[-1]
        return current_vol > vol_ma20 * multiplier

    def detect_price_drop_pct(self, df: pd.DataFrame, pct: float = 5.0, days: int = 10) -> bool:
        price_now = df["Close"].iloc[-1]
        price_before = df["Close"].iloc[-days]
        drop = (price_before - price_now) / price_before * 100
        return drop >= pct
