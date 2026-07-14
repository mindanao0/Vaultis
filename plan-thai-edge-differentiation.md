# แผน: ยกระดับ Vaultis จาก "indicator โหล" → เครื่องมือ DCA ที่มี edge ของคนไทย

> เอกสารแผน (design/roadmap) — ยังไม่ใช่โค้ดที่ merge แล้ว ใช้เป็นแนวทางพัฒนา

## Context (ทำไมถึงทำ)

ปัจจุบันสัญญาณของ Vaultis = RSI + MA + คะแนน (`analysis/financial_model.py`) — ดีและ deterministic
แต่ **เป็นของโหล** ทุกแอปมี ไม่มีเหตุผลให้เลือก Vaultis เจ้าของอยากได้ "ขอบที่ลอกยาก" และรับความเสี่ยงเพิ่มได้

แนวทาง: ต่อยอด tilt pipeline ที่มีอยู่ (`calculate_allocation`: `น้ำหนัก = เป้าหมาย × score_tilt(0.6–1.4)`)
เป็น **scorecard หลายชั้นที่โปร่งใส** + เพิ่ม 4 edge โดยไม่ทิ้งหลัก data integrity เดิม

## หลักการตัดสินใจ (สำคัญที่สุด — กำหนดทุกอย่าง)

**"ทุกเลขที่ขยับเงินต้องอธิบายซ้ำได้ 100%"**
- **คะแนนแกน** (`score_from_prices`, ราคา/เทคนิคอล) = สมอเรือ deterministic → **ไม่แก้**
- **ตัวคูณที่ขยับเงิน** = ปัจจัยที่คำนวณซ้ำได้ (score, regime) โชว์เป็นบรรทัดแยก ไม่มัดรวมเป็นก้อนดำ
- **ข่าว/sentiment** = การตีความที่ปั่นได้/ทำซ้ำไม่ได้ → เป็น **context ข้าง ๆ ไม่เคยเข้าในเลข**
- ข้อค้นพบ: FX เป็นตัวคูณเท่ากันทุก ETF (ทุกตัวเป็น USD) → ใส่ per-ticker แล้ว **หายตอน normalize**
  ดังนั้น FX ทำงานที่ **ระดับงบ/จังหวะ**; regime ต่างกันรายตัว → อยู่รอด normalize เอียงพอร์ตได้จริง

---

## Phase 1 — moat หลัก (ความเสี่ยงต่ำ, คำนวณได้จากราคา/ค่าเงินล้วน)

### Edge 2: ระบอบตลาด (regime) จากความสัมพันธ์ 5 ETF — **ตัวคูณ per-ticker**
**ไฟล์ใหม่ `analysis/regime.py`** — deterministic ทั้งหมด:
- แมป ticker เป็นกลุ่มความเสี่ยง: QQQM=aggressive, VOO=broad, SCHD=income, XLV=defensive, GLDM=hedge
- คำนวณ regime จากราคา 5 ตัว (ดึงผ่าน `_yf_close_series` ที่มีอยู่):
  - spread โมเมนตัม: return 3 เดือน QQQM − GLDM
  - breadth: กี่ตัวจาก 5 ที่อยู่เหนือ MA200 (ใช้ `ta.sma` ผ่าน `ta_compat`)
  - GLDM+XLV outperform = สัญญาณ risk-off
- คืน `regime` (risk_on/neutral/risk_off) + `regime_tilt` ต่อกลุ่ม (bounded **0.8–1.2**); fail-soft → 1.0

**แก้ `analysis/financial_model.py` `calculate_allocation`:**
- `weights[ticker] = base × _score_tilt(...) × regime_tilt(ticker)`
- เพิ่ม field `regime_tilt`, `regime` ใน allocation dict เพื่อโชว์แยก
- **ไม่แตะ `score_from_prices`/`_score_tilt`** — คะแนนแกนคงเดิม

### Edge 1: จังหวะค่าเงินบาท — **สัญญาณระดับงบ (ไม่ใช่ per-ticker)**
**ไฟล์ใหม่ `analysis/fx_timing.py`:**
- ดึง **ประวัติ USDTHB** (yfinance `THB=X` period 1-2y ผ่าน `normalize_close_series` จาก `data.fetcher`)
- คำนวณ **fx_zone** จาก percentile ของค่าปัจจุบันเทียบ 1 ปีตัวเอง:
  บาทแข็งผิดปกติ (percentile ต่ำ) → "จังหวะเติมเงินคุ้ม"; บาทอ่อน → "เติมปกติ/รอ"
- คืน `{fx_rate, is_live, fx_zone, budget_multiplier}` (multiplier bounded **0.9–1.1**, แนะนำเชิงงบ)
- **ต้อง fail-soft:** ถ้า `is_live=False` (ค่าสำรอง) หรือดึงประวัติไม่ได้ → zone="ไม่ทราบ", multiplier=1.0
- **แสดงผล:** บรรทัดแยกใน advisor/Discord ("💱 โซนบาท: แข็ง — เดือนนี้เติมเงินคุ้มกว่าปกติ") ไม่ยัดเข้า per-ticker weight

**Verification Phase 1:**
- `analysis/regime.py`: ป้อนราคา risk-off จำลอง (QQQM ร่วง, GLDM พุ่ง) → regime="risk_off", regime_tilt GLDM/XLV > 1, QQQM < 1
- `calculate_allocation`: assert ว่า regime เอียงสัดส่วน **หลัง normalize** จริง และ `regime_tilt` โผล่ใน dict; ยืนยัน `total_pct`/คะแนนแกนไม่เปลี่ยน
- `fx_timing`: mock ค่าสำรอง (`is_live=False`) → multiplier=1.0, zone="ไม่ทราบ" (fail-soft)
- `pytest` ผ่านทั้งหมด (โดยเฉพาะ allocation tests เดิม + cost-guard)

---

## Phase 2 — personalization + ข่าวเป็น context (ความเสี่ยงต่ำ-กลาง)

### Edge 3: คำแนะนำจากพอร์ตจริง (advisory เท่านั้น — ไม่สร้างตัวคูณเงินใหม่)
- ใช้ `portfolio.tracker.get_portfolio_summary()` + `get_target_weights()`
- คำนวณ **drift**: สัดส่วนปัจจุบัน vs เป้าหมาย, ตัวไหน overweight/underweight, ต้นทุนเฉลี่ย vs ราคาปัจจุบัน
- ป้อนเป็นบล็อก "=== พอร์ตของคุณ (drift/ต้นทุน) ===" ใน `_build_user_message` (`ai_advisor.py`)
- **ทำไมแค่ advisory:** ระบบมี rebalance service แยกอยู่แล้ว (CLAUDE.md แยก DCA กับ rebalance) → ไม่เพิ่ม money-moving tilt จาก drift

### ข่าว/Sentiment เป็น context
- เพิ่ม `get_latest_sentiment_summaries(symbols)` ใน `db/sentiment_models.py` (query pattern เดียวกับ `backend/routers/sentiment.py`; `SessionLocal is None` → `{}`; try/except fail-soft)
- ต่อเข้า `_build_user_message`/`get_ai_advice`/`get_monthly_advice` เป็นบล็อก "=== ข่าว/Sentiment ===" โชว์ score/avg_confidence/total_articles/as_of พร้อมหมายเหตุ "แยกจากคะแนนเทคนิคอล ห้ามรวม"
- อัปเดต `VAULTIS_ADVISOR_SYSTEM_PROMPT`: เมื่อเทคนิคอล vs ข่าว **ขัดแย้ง** ให้ชี้ชัด + ยึดเทคนิคอลเป็นหลัก, เตือนโซเชียลปั่นง่าย; confidence/จำนวนข่าวน้อย = สัญญาณอ่อน

**Verification Phase 2:**
- drift: holdings ที่ overweight QQQM → บล็อกพอร์ตระบุ drift ถูกต้อง
- sentiment reader fail-soft เมื่อไม่มี `DATABASE_URL` → `{}` ไม่ crash
- `get_monthly_advice(user_initiated=False, send_discord=False)` ทำงานจบ ไม่มี LLM call แฝง

---

## Phase 3 — Edge 4: AI นักวิเคราะห์ (ความเสี่ยงสูง, ทำท้ายสุด)

- ใช้ Prophet forecaster ที่มีอยู่ (`analysis/forecast*`) สร้าง **scenario band** (เช่น percentile 20/50/80)
- pre-commit dip-buy: ต่อกับ `_suggest_alert_levels` (deterministic, มีอยู่แล้ว)
- AI เขียน **narrative จาก band ที่คำนวณแล้ว** + บริบท fund flow/holdings — **เลขยังมาจากโค้ด**
- Gated: `user_initiated=True` เท่านั้น (มีค่า token), ผ่าน `analysis/llm.py` เดิม
- ความเสี่ยงที่ต้องคุม: hallucinate (prompt ย้ำห้ามแต่งเลข), ต้นทุน token (จำกัด scope/แคช)

**Verification Phase 3:** forecast band ออกมาเป็นตัวเลขก่อนเข้า prompt; รันด้วย `user_initiated=True` แล้วตรวจว่า narrative ไม่ขัดกับเลขที่ป้อน

---

## ไฟล์ที่อ้างอิง/ใช้ซ้ำ (ไม่สร้างซ้ำ)
- `analysis/financial_model.py` `calculate_allocation` + `_score_tilt` — จุดต่อ regime tilt
- `analysis/financial_model.py` `_yf_close_series` — ดึงราคา 5 ETF สำหรับ regime
- `utils/fx.py` `get_usdthb()` (มี `is_live`) + `data.fetcher.normalize_close_series` — ฐาน FX
- `portfolio/targets.py` `get_target_weights()` — เป้าหมาย/ฐาน drift
- `portfolio/tracker.py` `get_portfolio_summary()` — พอร์ตจริง (Edge 3)
- `analysis/ta_compat.py` `ta.sma` — breadth/MA200 ของ regime
- `backend/routers/sentiment.py` — pattern query sentiment ล่าสุด
- `analysis/llm.py` `chat_text(..., user_initiated=...)` — gate เดิม ไม่แตะ

## หลักที่ห้ามละเมิด (ตลอดทุก phase)
- ❌ ห้าม sentiment/ข่าวแตะ `score_from_prices`, `total_pct`, หรือกลายเป็นตัวคูณเงิน
- ❌ ห้ามจับจังหวะ FX จากค่าสำรอง (`is_live=False`) — ต้อง fail-soft เป็น neutral
- ❌ ทุก tilt ใหม่ต้อง **bounded** (ไม่มีวันเป็น 0 หรือพุ่งเกินคุม) + โชว์เป็นบรรทัดแยก
- ❌ ข้อมูลหาย = fail-soft เป็น neutral/NO DATA ไม่ใช่เลขปลอม (regime/fx/sentiment เป็นข้อมูลเสริม, ต่างจาก `data/fetcher` ที่ราคาต้อง fail loud)
- ✅ ทุก edge เพิ่มเฉพาะ AI Advisor/allocation — daily check ยัง numbers-only
