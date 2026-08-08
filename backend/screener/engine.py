import logging
from datetime import UTC, datetime

import pandas as pd
import yfinance

from analysis.ta_compat import ta
from backend.screener.crossover_detector import CrossoverDetector
from backend.screener.models import ScreenerPreset, ScreenerRule, ScreenerResult
from technical import signal_rules

logger = logging.getLogger(__name__)


# --- นิยามชุดเดียวของ "กฎที่เอนจินรู้จัก" (AUDIT_ROUND2_2026-08-07) -----------------
# ฟิลด์/ตัวดำเนินการที่ไม่อยู่ในตารางนี้ = **พรีเซ็ตพิมพ์ผิด** ไม่ใช่ "ไม่ผ่านกฎ"
#
# เดิม ``_evaluate_rule`` จบฟังก์ชันด้วย ``return False`` ⇒ ชื่อฟิลด์ที่สะกดผิดครั้งเดียว
# (เช่น ``price_vs_ma_200`` แทน ``price_vs_ma200``) ทำให้พรีเซ็ต AND นั้น "ไม่มีสัญญาณ"
# ตลอดกาลอย่างเงียบ ๆ และช่อง ``errors`` ที่เพิ่มมาก็ว่างเปล่าด้วย — ผู้ใช้จึงอ่านผลได้
# อย่างเดียวว่า "วันนี้ไม่มีอะไรต้องทำ" ทั้งที่ความจริงคือ "ตรวจไม่ได้"
# ("ดึงไม่สำเร็จ"/"ตรวจไม่ได้" ≠ "ไม่มีสัญญาณ" — กฎ C1 ของโครงการ)
_ALLOWED_OPERATORS: dict[str, frozenset[str]] = {
    "rsi": frozenset({"lt", "gt"}),
    "macd_cross": frozenset({"cross_up", "cross_down"}),
    "price_vs_ma200": frozenset({"gt", "lt"}),
    "golden_cross": frozenset({"cross_up"}),
    "death_cross": frozenset({"cross_down"}),
    "bb_squeeze": frozenset({"squeeze"}),
    "volume_spike": frozenset({"spike"}),
    "price_drop_pct": frozenset({"drop_pct"}),
}

_VALID_LOGIC = frozenset({"AND", "OR"})


def _normalize_logic(logic: str | None) -> str:
    """คืน ``"AND"``/``"OR"`` — อย่างอื่นโยน ``ValueError`` ทันที ห้ามเดา.

    AUDIT_ROUND2_2026-08-07: เดิม ``run()`` เขียนว่า
    ``if preset.logic == "AND": ... else: passed = any(...)`` ⇒ **อะไรก็ตามที่ไม่ใช่
    "AND" เป๊ะ ๆ แปลว่า OR** สะกดผิดครั้งเดียว (``"XOR"``, ``"and"``, ค่าว่าง)
    พรีเซ็ตกลับความหมายทั้งใบจาก "ทุกกฎต้องผ่าน" เป็น "ผ่านข้อเดียวก็พอ"
    แล้วยิงสัญญาณซื้อเข้า Telegram ตอน 07:00 จากกฎที่ผ่านแค่ข้อเดียว

    ตัวพิมพ์เล็ก/ช่องว่างเกินยังรับได้ (สะกดถูก แค่คนละรูปแบบ — และ
    ``/api/screener/custom`` ก็ ``.upper()`` ให้อยู่แล้ว) แต่คำที่ไม่รู้จักต้องดัง
    """
    normalized = (logic or "").strip().upper()
    if normalized not in _VALID_LOGIC:
        raise ValueError(
            f"logic ของพรีเซ็ตต้องเป็น 'AND' หรือ 'OR' เท่านั้น (ได้ {logic!r}) — "
            "ห้ามตีความเป็น OR เอง เพราะพรีเซ็ตจะกลับความหมายทั้งใบ"
        )
    return normalized


def _rule_label(rule: ScreenerRule) -> str:
    """ข้อความที่จะไปอยู่ใน ``matched_rules`` — ต้องมีเนื้อหาเสมอ ห้ามเป็นสตริงว่าง.

    AUDIT_ROUND2_2026-08-07: ``/api/screener/custom`` ประกอบกฎจาก dict ของผู้เรียก
    ซึ่งไม่บังคับให้มี ``description`` ⇒ response คืน ``"matched_rules": [""]``
    คือบอกว่า "มีกฎผ่าน 1 ข้อ" แต่บอกไม่ได้ว่าข้อไหน หน้าจอที่วนแสดงจะได้บุลเล็ตเปล่า
    ถ้าผู้เรียกไม่ได้ใส่คำอธิบาย ให้ประกอบข้อความจากตัวกฎเอง (เช่น ``rsi lt 70``)
    """
    description = (rule.description or "").strip()
    if description:
        return description
    parts = [str(rule.field or "").strip(), str(rule.operator or "").strip()]
    if rule.value is not None:
        parts.append(str(rule.value))
    return " ".join(p for p in parts if p) or "กฎที่ไม่มีคำอธิบาย"


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


def _decided(value: bool | None, what: str) -> bool:
    """แปลง "คำนวณไม่ได้" (``None``) ของตัวตรวจจับให้ดังเป็น ``ValueError``.

    ตัวตรวจจับใน ``crossover_detector.py`` คืน ``None`` เมื่อข้อมูลไม่พอตัดสิน
    (FIX_PLAN ข้อ 2.1) — ถ้าปล่อยให้ ``None`` ไหลต่อ ``all()``/``any()`` จะอ่านมันเป็น
    **เท็จ** = "กฎข้อนี้ไม่ผ่าน" ซึ่งคือบั๊กเดิมทั้งดุ้น (ETF ประวัติสั้นกว่า 200 วัน
    ได้ "ไม่มีสัญญาณ" ตลอดกาลอย่างเงียบ ๆ) · ``run()`` เก็บ ``ValueError`` นี้ลง
    ``.errors`` รายสัญลักษณ์ ซึ่งงาน 07:00 และหน้าจอรายงานต่อ
    """
    if value is None:
        raise ValueError(f"ตัดสิน {what} ไม่ได้ (ประวัติราคาไม่พอ) — ไม่ใช่ 'ไม่มีสัญญาณ'")
    return bool(value)


class ScreenerEngine:
    def __init__(self):
        self.detector = CrossoverDetector()

    def _fetch_df(self, symbol: str) -> pd.DataFrame:
        # auto_adjust=True ระบุชัด: ใช้ราคา adjusted เป็นมาตรฐานเดียวทั้งระบบ
        # (เดิมไม่ระบุ → พฤติกรรมแกว่งตามเวอร์ชัน yfinance — AUDIT.md M1)
        #
        # ``period="2y"`` ไม่ใช่ ``"1y"`` (FIX_PLAN ข้อ 2.1): กฎที่ต้องใช้ MA200 กิน 200 แท่ง
        # ไปแล้ว 1 ปี (~250 แท่ง) จึงเหลือ margin แค่ ~50 แท่ง — พรีเซ็ต golden/death cross
        # มองย้อน lookback อีก 3 แท่ง และค่าเฉลี่ย bandwidth 50 แท่งของ BB ก็กินเพิ่ม
        # ⇒ วันที่ผู้ให้ข้อมูลส่งแท่งขาดไปไม่กี่วัน สัญญาณจะกลายเป็น "ตัดสินไม่ได้" ทั้งชุด
        df = yfinance.download(symbol, period="2y", interval="1d", progress=False, auto_adjust=True)
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
        #
        # AUDIT_ROUND2_2026-08-07: ตรวจ "นิยาม" ของกฎก่อนแตะข้อมูลราคา — ฟิลด์หรือ
        # ตัวดำเนินการที่เอนจินไม่รู้จักคือพรีเซ็ตพัง ต้องดังทันที ไม่ใช่ตกไปที่
        # ``return False`` ท้ายฟังก์ชันแล้วกลายเป็น "กฎข้อนี้ไม่ผ่าน" อย่างเงียบ ๆ
        allowed = _ALLOWED_OPERATORS.get(rule.field)
        if allowed is None:
            raise ValueError(
                f"กฎ screener อ้างฟิลด์ที่ไม่รู้จัก: {rule.field!r} "
                f"(ที่รองรับ: {', '.join(sorted(_ALLOWED_OPERATORS))}) — "
                "พรีเซ็ตพิมพ์ผิด หรือยังไม่ได้เพิ่มตัวประเมินให้ฟิลด์นี้"
            )
        if rule.operator not in allowed:
            raise ValueError(
                f"ฟิลด์ {rule.field!r} ไม่รองรับตัวดำเนินการ {rule.operator!r} "
                f"(ที่รองรับ: {', '.join(sorted(allowed))})"
            )

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
            if cross is None:
                raise ValueError("คำนวณ MACD ไม่ได้ (ข้อมูลไม่พอ) — ไม่ใช่ 'ไม่มีการตัด'")
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
            return _decided(
                self.detector.detect_golden_cross(df, int(rule.value or 3)), "golden cross (MA50/MA200)"
            )
        elif rule.field == "death_cross":
            return _decided(
                self.detector.detect_death_cross(df, int(rule.value or 3)), "death cross (MA50/MA200)"
            )
        elif rule.field == "bb_squeeze":
            return _decided(self.detector.detect_bb_squeeze(df), "Bollinger squeeze")
        elif rule.field == "volume_spike":
            return _decided(
                self.detector.detect_volume_spike(df, rule.value or 2.0), "volume spike"
            )
        elif rule.field == "price_drop_pct":
            return _decided(
                self.detector.detect_price_drop_pct(df, rule.value or 5.0), "price drop %"
            )
        # มาถึงบรรทัดนี้ไม่ได้ถ้า ``_ALLOWED_OPERATORS`` กับสาขาข้างบนตรงกัน — กันไว้
        # ไม่ให้ฟิลด์ที่เพิ่มในตารางแต่ลืมเขียนตัวประเมินกลับไปเงียบเป็น "ไม่ผ่านกฎ" อีก
        raise ValueError(
            f"เอนจินยังไม่มีตัวประเมินสำหรับ {rule.field!r} {rule.operator!r} "
            "(อยู่ใน _ALLOWED_OPERATORS แต่ไม่มีสาขาใน _evaluate_rule)"
        )

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

        นิยามของพรีเซ็ตถูกตรวจ **ก่อน** วนสัญลักษณ์ และโยน ``ValueError`` ออกไปเลย
        (AUDIT_ROUND2_2026-08-07) — พรีเซ็ตที่นิยามผิดไม่ใช่ "สัญลักษณ์นี้ตรวจไม่ได้"
        แต่คือทั้งใบใช้ไม่ได้ จึงต้องดังที่ผู้เรียก ไม่ใช่ลงไปนอนใน ``.errors`` เงียบ ๆ
        ส่วนกฎที่ประเมินไม่ได้รายสัญลักษณ์ยังคงถูกเก็บลง ``.errors`` เหมือนเดิม
        """
        logic = _normalize_logic(preset.logic)
        if not preset.rules:
            raise ValueError(
                f"พรีเซ็ต {preset.name!r} ไม่มีกฎสักข้อ — logic AND จะ 'ผ่าน' ทุกสัญลักษณ์"
                "โดยไม่ได้ตรวจอะไรเลย (สัญญาณที่ไม่มีเหตุผลรองรับ)"
            )
        results = []
        errors: list[str] = []
        logger.info("Starting screener run: preset=%s logic=%s symbols=%d", preset.name, logic, len(symbols))
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
                # ``_rule_label``: ถ้าผู้เรียกไม่ได้ใส่ ``description`` ให้ประกอบข้อความ
                # จากตัวกฎแทน — รายการเหตุผลต้องไม่มีสมาชิกที่เป็นสตริงว่าง
                matched = [_rule_label(r) for r, passed in rule_results if passed]
                if logic == "AND":
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
