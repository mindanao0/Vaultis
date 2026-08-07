import logging
from datetime import UTC, datetime

import pandas as pd
import yfinance

from analysis.ta_compat import ta
from backend.screener.crossover_detector import CrossoverDetector
from backend.screener.models import ScreenerPreset, ScreenerRule, ScreenerResult
from technical import signal_rules

logger = logging.getLogger(__name__)


class ScreenerRunResults(list):
    """ลิสต์ ``ScreenerResult`` + ช่อง ``errors`` สำหรับสัญลักษณ์ที่ "ตรวจไม่ได้".

    "ดึงไม่สำเร็จ" ≠ "ไม่มีสัญญาณ" (C1) — เดิม ``run()`` กลืนความล้มเหลวไว้ใน
    ``logger.error`` อย่างเดียว ผู้เรียก (scheduler / router / หน้าจอ) จึงแยกสองกรณีนี้
    ไม่ออกเลย

    ที่เป็น subclass ของ ``list`` เพราะผู้เรียกเดิมใช้ค่านี้เป็นลิสต์ตรง ๆ
    (``if results:`` · ``len()`` · วนลูป · ``r.__dict__``) — เพิ่มช่องใหม่ได้โดยไม่ต้อง
    ไล่แก้ทุกจุดและไม่เปลี่ยนรูป response เดิม
    """

    def __init__(self, results=(), errors: list[str] | None = None):
        super().__init__(results)
        self.errors: list[str] = list(errors or [])


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

    def fetch_frames(self, symbols: list[str]) -> tuple[dict[str, pd.DataFrame], list[str]]:
        """ดึงราคา **ครั้งเดียวต่อสัญลักษณ์** แล้วให้ผู้เรียกเอาไปใช้ซ้ำทุกพรีเซ็ต.

        AUDIT_2026-08-06 ข้อ B6.2: งานเช้าวน 4 พรีเซ็ต × 5 สัญลักษณ์ = 20 คำขอต่อวัน
        ทั้งที่ต้องการจริง 5 — เป็นตัวคูณความเสี่ยงโดน rate limit ของ Yahoo ซึ่งผลลัพธ์
        ปลายทางคือ "ไม่มีสัญญาณวันนี้"

        คืน ``(เฟรมที่ดึงได้, ข้อความข้อผิดพลาดรายสัญลักษณ์)`` — ตัวที่ดึงไม่ได้ต้อง
        ไม่หายเงียบ (C1)
        """
        frames: dict[str, pd.DataFrame] = {}
        errors: list[str] = []
        for symbol in symbols:
            try:
                frames[symbol] = self._fetch_df(symbol)
            except Exception as e:
                logger.error("[%s] ดึงราคาไม่สำเร็จ: %s", symbol, e, exc_info=True)
                errors.append(f"{symbol}: {e}")
        return frames, errors

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

    def _compute_signal_strength(
        self, matched: int, total: int, df: pd.DataFrame, preset: ScreenerPreset
    ) -> float:
        """ความแรง 0–10 = สัดส่วนกฎที่ผ่าน × 7 + โบนัส RSI **ตามทิศของพรีเซ็ต**.

        AUDIT_2026-08-06 ข้อ B6.3 — เดิมบวกโบนัสทั้งสองฝั่งเสมอ (RSI<35 ได้ +1.5,
        RSI>65 ได้ +1.0) โดยฟังก์ชันไม่รู้ว่าพรีเซ็ตนั้นมองหาอะไร ผลคือในพรีเซ็ตฝั่งซื้อ
        สัญลักษณ์ที่ overbought (RSI 70.3 → 8.0) ได้คะแนนสูงกว่าตัวที่กลาง ๆ (RSI 48 → 7.0)
        และเกณฑ์ 35/65 เป็นตัวเลขชุดที่สองซ้อนกับนิยามกลางของระบบ
        (``technical/signal_rules`` = 30/70) ซึ่งผิดกฎ "นิยามมีที่เดียว"
        """
        base = (matched / total) * 7
        rsi = ta.rsi(df["Close"], length=14).iloc[-1]
        if pd.isna(rsi):
            return min(round(base, 1), 10.0)

        bonus = 0.0
        direction = (preset.direction or "").strip().lower()
        if direction == "buy" and float(rsi) < signal_rules.RSI_OVERSOLD:
            bonus = 1.5
        elif direction == "sell" and float(rsi) > signal_rules.RSI_OVERBOUGHT:
            bonus = 1.5
        return min(round(base + bonus, 1), 10.0)

    def run(
        self,
        symbols: list[str],
        preset: ScreenerPreset,
        frames: dict[str, pd.DataFrame] | None = None,
    ) -> ScreenerRunResults:
        """รันพรีเซ็ตกับรายสัญลักษณ์ — ส่ง ``frames`` มาได้เพื่อไม่ต้องดึงราคาซ้ำ (B6.2).

        คืน ``ScreenerRunResults`` (ลิสต์ผลลัพธ์ + ``.errors`` ของตัวที่ตรวจไม่ได้)
        """
        results = []
        errors: list[str] = []
        logger.info("Starting screener run: preset=%s logic=%s symbols=%d", preset.name, preset.logic, len(symbols))
        for symbol in symbols:
            try:
                if frames is None:
                    logger.debug("[%s] fetching data", symbol)
                    df = self._fetch_df(symbol)
                else:
                    # ผู้เรียกดึงมาให้แล้ว — ตัวที่ไม่อยู่ในชุด = ดึงไม่สำเร็จ
                    # ห้ามยิงใหม่เงียบ ๆ (จะกลายเป็นคำขอซ้ำที่เพิ่งแก้ไป)
                    df = frames.get(symbol)
                    if df is None:
                        raise ValueError(f"ไม่มีข้อมูลราคา {symbol} ในชุดที่ส่งเข้ามา (ดึงไม่สำเร็จ)")
                rule_results = [(r, self._evaluate_rule(r, df)) for r in preset.rules]
                matched = [r.description for r, passed in rule_results if passed]
                if preset.logic == "AND":
                    passed = all(p for _, p in rule_results)
                else:
                    passed = any(p for _, p in rule_results)
                if passed:
                    price = float(df["Close"].iloc[-1])
                    strength = self._compute_signal_strength(len(matched), len(preset.rules), df, preset)
                    logger.info("[%s] PASS  price=%.4f strength=%.1f matched=%s", symbol, price, strength, matched)
                    results.append(
                        ScreenerResult(
                            symbol=symbol,
                            matched_rules=matched,
                            price=price,
                            signal_strength=strength,
                            preset_name=preset.name,
                            timestamp=datetime.now(UTC).isoformat(),
                        )
                    )
                else:
                    logger.debug("[%s] FAIL  matched=%d/%d rules", symbol, len(matched), len(preset.rules))
            except Exception as e:
                logger.error("[%s] screener error: %s", symbol, e, exc_info=True)
                errors.append(f"{symbol}: {e}")
        logger.info(
            "Screener run complete: %d/%d symbols passed, %d ตรวจไม่ได้",
            len(results),
            len(symbols),
            len(errors),
        )
        return ScreenerRunResults(
            sorted(results, key=lambda x: x.signal_strength, reverse=True), errors=errors
        )
