import logging
from datetime import datetime

import pandas as pd
import yfinance

from analysis.ta_compat import ta
from backend.screener.crossover_detector import CrossoverDetector
from backend.screener.models import ScreenerPreset, ScreenerRule, ScreenerResult

logger = logging.getLogger(__name__)


class ScreenerEngine:
    def __init__(self):
        self.detector = CrossoverDetector()

    def _fetch_df(self, symbol: str) -> pd.DataFrame:
        # auto_adjust=True ระบุชัด: ใช้ราคา adjusted เป็นมาตรฐานเดียวทั้งระบบ
        # (เดิมไม่ระบุ → พฤติกรรมแกว่งตามเวอร์ชัน yfinance — AUDIT.md M1)
        df = yfinance.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=True)
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        if df.empty or "Close" not in df.columns:
            raise ValueError(f"ดึงข้อมูลราคา {symbol} ไม่สำเร็จ (ผลว่าง)")
        return df

    def _evaluate_rule(self, rule: ScreenerRule, df: pd.DataFrame) -> bool:
        # หมายเหตุ (AUDIT.md C1): ห้ามครอบ try/except คืน False —
        # error ต้องเด้งขึ้นไปให้ run() log เป็น ERROR รายสัญลักษณ์
        # ไม่งั้น "ตรวจไม่ได้" จะแยกไม่ออกจาก "ไม่มีสัญญาณ"
        price = df["Close"].iloc[-1]
        if rule.field == "rsi":
            rsi = ta.rsi(df["Close"], length=14).iloc[-1]
            if pd.isna(rsi):
                raise ValueError("คำนวณ RSI ไม่ได้ (ข้อมูลไม่พอ)")
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
            if pd.isna(ma200):
                raise ValueError("คำนวณ MA200 ไม่ได้ (ข้อมูลไม่พอ)")
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
        return False

    def _compute_signal_strength(self, matched: int, total: int, df: pd.DataFrame) -> float:
        base = (matched / total) * 7
        rsi = ta.rsi(df["Close"], length=14).iloc[-1]
        if pd.isna(rsi):
            return min(round(base, 1), 10.0)
        bonus = 0
        if rsi < 35:
            bonus += 1.5
        if rsi > 65:
            bonus += 1.0
        return min(round(base + bonus, 1), 10.0)

    def run(self, symbols: list[str], preset: ScreenerPreset) -> list[ScreenerResult]:
        results = []
        logger.info("Starting screener run: preset=%s logic=%s symbols=%d", preset.name, preset.logic, len(symbols))
        for symbol in symbols:
            try:
                logger.debug("[%s] fetching data", symbol)
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
                    logger.info("[%s] PASS  price=%.4f strength=%.1f matched=%s", symbol, price, strength, matched)
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
                else:
                    logger.debug("[%s] FAIL  matched=%d/%d rules", symbol, len(matched), len(preset.rules))
            except Exception as e:
                logger.error("[%s] screener error: %s", symbol, e, exc_info=True)
        logger.info("Screener run complete: %d/%d symbols passed", len(results), len(symbols))
        return sorted(results, key=lambda x: x.signal_strength, reverse=True)
