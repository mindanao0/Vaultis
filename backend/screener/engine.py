from datetime import datetime

import pandas as pd
import pandas_ta  # noqa: F401
import yfinance

from backend.screener.crossover_detector import CrossoverDetector
from backend.screener.models import ScreenerPreset, ScreenerRule, ScreenerResult


class ScreenerEngine:
    def __init__(self):
        self.detector = CrossoverDetector()

    def _fetch_df(self, symbol: str) -> pd.DataFrame:
        df = yfinance.download(symbol, period="1y", interval="1d", progress=False)
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df

    def _evaluate_rule(self, rule: ScreenerRule, df: pd.DataFrame) -> bool:
        try:
            price = df["Close"].iloc[-1]
            if rule.field == "rsi":
                rsi = df.ta.rsi(length=14).iloc[-1]
                if rule.operator == "lt":
                    return rsi < rule.value
                if rule.operator == "gt":
                    return rsi > rule.value
            elif rule.field == "macd_cross":
                cross = self.detector.detect_macd_cross(df)
                if rule.operator == "cross_up":
                    return cross == "bullish"
                if rule.operator == "cross_down":
                    return cross == "bearish"
            elif rule.field == "price_vs_ma200":
                ma200 = df["Close"].rolling(200).mean().iloc[-1]
                if rule.operator == "gt":
                    return price > ma200
                if rule.operator == "lt":
                    return price < ma200
            elif rule.field == "golden_cross":
                return self.detector.detect_golden_cross(df, int(rule.value or 3))
            elif rule.field == "death_cross":
                return self.detector.detect_death_cross(df, int(rule.value or 3))
            elif rule.field == "bb_squeeze":
                return self.detector.detect_bb_squeeze(df)
            elif rule.field == "volume_spike":
                return self.detector.detect_volume_spike(df, rule.value or 2.0)
            elif rule.field == "price_drop_pct":
                return self.detector.detect_price_drop_pct(df, rule.value or 5.0)
        except Exception:
            return False
        return False

    def _compute_signal_strength(self, matched: int, total: int, df: pd.DataFrame) -> float:
        base = (matched / total) * 7
        rsi = df.ta.rsi(length=14).iloc[-1]
        bonus = 0
        if rsi < 35:
            bonus += 1.5
        if rsi > 65:
            bonus += 1.0
        return min(round(base + bonus, 1), 10.0)

    def run(self, symbols: list[str], preset: ScreenerPreset) -> list[ScreenerResult]:
        results = []
        for symbol in symbols:
            try:
                df = self._fetch_df(symbol)
                rule_results = [(r, self._evaluate_rule(r, df)) for r in preset.rules]
                matched = [r.description for r, passed in rule_results if passed]
                if preset.logic == "AND":
                    passed = all(p for _, p in rule_results)
                else:
                    passed = any(p for _, p in rule_results)
                if passed:
                    price = float(df["Close"].iloc[-1])
                    strength = self._compute_signal_strength(len(matched), len(preset.rules), df)
                    results.append(
                        ScreenerResult(
                            symbol=symbol,
                            matched_rules=matched,
                            price=price,
                            signal_strength=strength,
                            preset_name=preset.name,
                            timestamp=datetime.utcnow().isoformat(),
                        )
                    )
            except Exception as e:
                print(f"[{symbol}] screener error: {e}")
        return sorted(results, key=lambda x: x.signal_strength, reverse=True)
