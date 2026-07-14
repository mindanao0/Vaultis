# ROADMAP: ยกระดับ Vaultis เป็นเครื่องมือ DCA ที่มี edge ของคนไทย (ฉบับเต็ม ~19 ฟีเจอร์)

> เอกสารนี้คือ roadmap รวมทุกฟีเจอร์ที่ตกลงกันไว้ จัดเป็นเฟสตาม dependency + ความเสี่ยง + ความคุ้ม
> **หลักจัดลำดับ:** (1) พิสูจน์ก่อนสร้าง — backtest เป็นด่านกั้น (2) วางฐานความจริง/ต้นทุนก่อนต่อยอด
> (3) low-risk high-value ก่อน (4) money-moving/เสี่ยงสูงไว้ท้าย
> แผน Phase 1 เดิม (regime+FX) ฉบับละเอียดอยู่ท้ายเอกสาร (คงไว้เป็นสเปกอ้างอิง)

## หลักที่ห้ามละเมิด (invariant — ทุกเฟส)
- **ทุกเลขที่ขยับเงินต้องอธิบายซ้ำได้ 100%** — คะแนนแกน (`score_from_prices`) ไม่แก้; ตัวคูณที่ขยับเงินโชว์แยกทุกชั้น
- **ข่าว/sentiment = context ข้าง ๆ ไม่เคยเข้าในเลข**
- **ข้อมูลหาย = fail-soft เป็น neutral/NO DATA ไม่ใช่เลขปลอม** (ยกเว้นราคา = fail loud ตาม `data/fetcher`)
- ทุก tilt ใหม่ต้อง **bounded** + โชว์เป็นบรรทัดแยก
- **money-moving items (ทำระวังเป็นพิเศษ):** regime tilt, FX budget signal, rebalance-with-cashflow (H)

---

## Phase 0 — ฐาน + พิสูจน์ (ทำก่อนสุด, de-risk ทั้ง roadmap)
1. **Backtest A/B พิสูจน์ edge** — harness เทียบ DCA เป้าหมายปกติ vs DCA ที่ tilt (regime/FX) ย้อนหลัง
   - ใช้ซ้ำ: `portfolio/dca.py` `simulate_dca(weights,...)` + `portfolio/backtest.py` (Sharpe/max DD/เทียบ VOO)
   - หมายเหตุ: ทั้งคู่ใช้ fixed weights → ต้องย้ายการคำนวณ weight เข้าไปใน loop รายเดือนเพื่อรองรับ tilt ที่แปรตามเวลา
   - **เป็นด่านกั้น:** ถ้า tilt ไม่ชนะ plain DCA → ทบทวน Edge 1/2 ก่อนลงมือ
2. **แก้บั๊กค่าธรรมเนียมไม่ตรงกัน** — `portfolio/tracker.py:207` (เดือนแรกฟรี) vs `backend/services/rebalance_service.py:93` (คิดทุกครั้ง) → รวมสูตรเดียว
3. **แหล่งราคาสำรอง (J)** — เพิ่ม fallback (Stooq/Alpha Vantage) รอบ `data/fetcher.py` ให้ yfinance ล่มแล้วยังทำงาน (คง fail-loud เมื่อทุกแหล่งล่ม)

## Phase 1 — moat edges (deterministic จากราคา/ค่าเงิน) — *gated ด้วย Phase 0*
- **Edge 2: regime tilt** (per-ticker, bounded 0.8–1.2) — ไฟล์ใหม่ `analysis/regime.py`, ต่อ `calculate_allocation`
- **Edge 1: FX timing** (สัญญาณระดับงบ) — ไฟล์ใหม่ `analysis/fx_timing.py`
- *(สเปกละเอียดทั้งสองอยู่ท้ายเอกสาร)*

## Phase 2 — ชั้นความจริง & ต้นทุน (moat ไทยที่แข็งสุด)
4. **ภาษี + ต้นทุนจริง** — ภาษีหัก ณ ที่จ่าย 15% ปันผล US (กระทบ SCHD หนักสุด), FX spread, ค่าคอมขั้นต่ำ
   - ที่มีอยู่: ค่าคอม Dime 0.15% (`tracker.py:38`), FX mid-rate (`utils/fx.py`) — เพิ่มชั้นภาษี+spread ทับ
   - dashboard มี disclaimer "ไม่คิดภาษี" (`dashboard/app.py:1213`) → แทนที่ด้วยตัวเลขจริง
5. **ติดตามเงินปันผลจริง + DRIP** — ledger ปัจจุบัน buy-only (`tracker.py:23-33`) → เพิ่ม tx type "รับปันผล", คำนวณ income หลังภาษี, จำลอง DRIP
6. **ผลตอบแทนจริงหลังภาษี+FX+เงินเฟ้อ (B)** — real return เป็นบาท ใช้ Thai CPI (`analysis/macro.py`) — *ต่อยอดจากข้อ 4-5*

## Phase 3 — personalization & ความโปร่งใส
7. **Edge 3: drift personalization** (advisory) — `portfolio/tracker.get_portfolio_summary()` + `get_target_weights()`
8. **ข่าว/Sentiment เป็น context** — `get_latest_sentiment_summaries()` ใหม่ใน `db/sentiment_models.py`
9. **Audit trail "ทำไมได้เท่านี้" (D)** — แตกคะแนน trend/timing/momentum + regime ทุกชั้น (มี field อยู่ใน `score_from_prices` แล้ว แค่ surface)
10. **เตือน overlap + factor/sector exposure (F)** — VOO∩QQQM big tech, XLV=healthcare, GLDM=ทอง (มี `analysis/correlation.py` เสริม)

## Phase 4 — ลงมือได้จริง & วินัย
11. **"รายการซื้อเดือนนี้" พร้อม execute (G)** — แปลง allocation → จำนวนหุ้น/เงินจริง + fee+FX copy ไปวางในโบรก
12. **Rebalance ด้วยเงินใหม่ tax-smart (H)** *(money-moving)* — เท DCA เข้าตัว underweight แทนการขาย (ไม่มีภาษี) — ต่อ `rebalance_service` (drift 5%) กับแผน DCA
13. **โค้ชกันแพนิก + stress test (I)** — ตรวจ drawdown (`analysis/risk.py:52`) → บริบทประวัติศาสตร์ "2008/2020 ลง X% ฟื้น Y เดือน" + ติดตามการทำตามแผน
14. **เทียบ benchmark ต่อเนื่อง (E)** — โชว์ "ชนะ VOO ไหม" กับพอร์ตจริง (`portfolio/backtest.py` เทียบ VOO อยู่แล้ว)
15. **Monte Carlo ผูกพอร์ตจริง (C)** — ต่อ `goal_service.calculate_probability()` (1000 sims มีอยู่แล้ว) เข้าพอร์ต+จังหวะ DCA จริง

## Phase 5 — reach & AI ขั้นสูง (เสี่ยง/แพงสุด — ท้ายสุด)
16. **แจ้งเตือน LINE (A)** — เพิ่มช่องทาง LINE Messaging API ข้าง Discord/Telegram (localization)
17. **Edge 4: AI นักวิเคราะห์ + scenario** — Prophet band + narrative (gated `user_initiated=True`)

---

# ภาคผนวก: สเปกละเอียด Phase 1 (regime + FX) — คงไว้อ้างอิงตอนลงมือ

# แผน: ยกระดับ Vaultis จาก "indicator โหล" → เครื่องมือ DCA ที่มี edge ของคนไทย

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
**แก้ `analysis/financial_model.py` `calculate_allocation` (บรรทัด 406-411):**
- `weights[ticker] = base × _score_tilt(...) × regime_tilt(ticker)`
- เพิ่ม field `regime_tilt`, `regime` ใน allocation dict (บรรทัด 436-445) เพื่อโชว์แยก
- **ไม่แตะ `score_from_prices`/`_score_tilt`** — คะแนนแกนคงเดิม

### Edge 1: จังหวะค่าเงินบาท — **สัญญาณระดับงบ (ไม่ใช่ per-ticker)**
**ไฟล์ใหม่ `analysis/fx_timing.py`:**
- เพิ่ม helper ดึง **ประวัติ USDTHB** (yfinance `THB=X` period 1-2y ผ่าน `normalize_close_series`
  จาก `data.fetcher` — pattern เดียวกับ `utils/fx.py:_fetch_live`)
- คำนวณ **fx_zone** จาก percentile/z-score ของค่าปัจจุบันเทียบ 1 ปีตัวเอง:
  บาทแข็งผิดปกติ (percentile ต่ำ) → "จังหวะเติมเงินคุ้ม"; บาทอ่อน → "เติมปกติ/รอ"
- คืน `{fx_rate, is_live, fx_zone, budget_multiplier}` (multiplier bounded **0.9–1.1**, แนะนำเชิงงบ)
- **ต้อง fail-soft:** ถ้า `is_live=False` (ค่าสำรอง) หรือดึงประวัติไม่ได้ → zone="ไม่ทราบ", multiplier=1.0
  (สอดคล้อง `utils/fx.py` ที่รายงาน `is_live` อยู่แล้ว — ห้ามจับจังหวะจากค่าสำรอง)
**แสดงผล:** เป็นบรรทัดแยกใน advisor/Discord ("💱 โซนบาท: แข็ง — เดือนนี้เติมเงินคุ้มกว่าปกติ")
ไม่ยัดเข้า per-ticker weight

**Verification Phase 1:**
- `analysis/regime.py`: ป้อนราคา risk-off จำลอง (QQQM ร่วง, GLDM พุ่ง) → regime="risk_off",
  regime_tilt GLDM/XLV > 1, QQQM < 1
- `calculate_allocation`: assert ว่า regime เอียงสัดส่วน **หลัง normalize** จริง (GLDM ได้ % เพิ่มใน risk-off)
  และ `regime_tilt` โผล่ใน dict; ยืนยัน `total_pct`/คะแนนแกนไม่เปลี่ยน
- `fx_timing`: mock ค่าสำรอง (`is_live=False`) → multiplier=1.0, zone="ไม่ทราบ" (fail-soft)
- `pytest` ผ่านทั้งหมด (โดยเฉพาะ allocation tests เดิม + cost-guard)

---

## Phase 2 — personalization + ข่าวเป็น context (ความเสี่ยงต่ำ-กลาง)

### Edge 3: คำแนะนำจากพอร์ตจริง (advisory เท่านั้น — ไม่สร้างตัวคูณเงินใหม่)
- ใช้ `portfolio.tracker.get_portfolio_summary()` (advisor ใช้อยู่แล้ว บรรทัด 328) + `get_target_weights()`
- คำนวณ **drift**: สัดส่วนปัจจุบัน vs เป้าหมาย, ตัวไหน overweight/underweight, ต้นทุนเฉลี่ย vs ราคาปัจจุบัน
- ป้อนเป็นบล็อก "=== พอร์ตของคุณ (drift/ต้นทุน) ===" ใน `_build_user_message` (`ai_advisor.py`)
- **ทำไมแค่ advisory:** ระบบมี rebalance service แยกอยู่แล้ว (CLAUDE.md แยก DCA กับ rebalance) →
  ไม่เพิ่ม money-moving tilt จาก drift เพื่อเลี่ยงสองระบบตีกัน AI แค่ "เฟรมคำแนะนำให้ส่วนตัว"

### ข่าว/Sentiment เป็น context (fold แผนเดิมเข้ามา)
- เพิ่ม `get_latest_sentiment_summaries(symbols)` ใน `db/sentiment_models.py` (query pattern
  เดียวกับ `backend/routers/sentiment.py:55-60`; `SessionLocal is None` → `{}`; try/except fail-soft)
- ต่อเข้า `_build_user_message`/`get_ai_advice`/`get_monthly_advice` เป็นบล็อก "=== ข่าว/Sentiment ==="
  โชว์ score/avg_confidence/total_articles/as_of พร้อมหมายเหตุ "แยกจากคะแนนเทคนิคอล ห้ามรวม"
- อัปเดต `VAULTIS_ADVISOR_SYSTEM_PROMPT`: เมื่อเทคนิคอล vs ข่าว **ขัดแย้ง** ให้ชี้ชัด + ยึดเทคนิคอล
  เป็นหลัก, เตือนโซเชียลปั่นง่าย; confidence/จำนวนข่าวน้อย = สัญญาณอ่อน

**Verification Phase 2:**
- drift: holdings ที่ overweight QQQM → บล็อกพอร์ตระบุ drift ถูกต้อง
- sentiment reader fail-soft เมื่อไม่มี `DATABASE_URL` → `{}` ไม่ crash
- `get_monthly_advice(user_initiated=False, send_discord=False)` ทำงานจบ ไม่มี LLM call แฝง

---

## Phase 3 — Edge 4: AI นักวิเคราะห์ (ความเสี่ยงสูง, ทำท้ายสุด)

- ใช้ Prophet forecaster ที่มีอยู่ (`analysis/forecast*`) สร้าง **scenario band** (เช่น percentile 20/50/80)
- pre-commit dip-buy: ต่อกับ `_suggest_alert_levels` (deterministic, มีอยู่แล้ว `ai_advisor.py:209`)
- AI เขียน **narrative จาก band ที่คำนวณแล้ว** + บริบท fund flow/holdings — **เลขยังมาจากโค้ด**
- Gated: `user_initiated=True` เท่านั้น (มีค่า token), ผ่าน `analysis/llm.py` เดิม
- ความเสี่ยงที่ต้องคุม: hallucinate (prompt ย้ำห้ามแต่งเลข), ต้นทุน token (จำกัด scope/แคช)

**Verification Phase 3:** forecast band ออกมาเป็นตัวเลขก่อนเข้า prompt; รันด้วย `user_initiated=True`
(ต้องมี `ANTHROPIC_API_KEY`) แล้วตรวจว่า narrative ไม่ขัดกับเลขที่ป้อน

---

## ไฟล์ที่อ้างอิง/ใช้ซ้ำ (ไม่สร้างซ้ำ)
- `analysis/financial_model.py:373-447` `calculate_allocation` + `_score_tilt` — จุดต่อ regime tilt
- `analysis/financial_model.py:486-507` `_yf_close_series` — ดึงราคา 5 ETF สำหรับ regime
- `utils/fx.py` `get_usdthb()` (มี `is_live`) + `data.fetcher.normalize_close_series` — ฐาน FX
- `portfolio/targets.py` `get_target_weights()` — เป้าหมาย/ฐาน drift
- `portfolio/tracker.py` `get_portfolio_summary()` — พอร์ตจริง (Edge 3)
- `analysis/ta_compat.py` `ta.sma` — breadth/MA200 ของ regime
- `backend/routers/sentiment.py:55-60` — pattern query sentiment ล่าสุด
- `analysis/llm.py` `chat_text(..., user_initiated=...)` — gate เดิม ไม่แตะ

## หลักที่ห้ามละเมิด (ตลอดทุก phase)
- ❌ ห้าม sentiment/ข่าวแตะ `score_from_prices`, `total_pct`, หรือกลายเป็นตัวคูณเงิน
- ❌ ห้ามจับจังหวะ FX จากค่าสำรอง (`is_live=False`) — ต้อง fail-soft เป็น neutral
- ❌ ทุก tilt ใหม่ต้อง **bounded** (ไม่มีวันเป็น 0 หรือพุ่งเกินคุม) + โชว์เป็นบรรทัดแยก
- ❌ ข้อมูลหาย = fail-soft เป็น neutral/NO DATA ไม่ใช่เลขปลอม (regime/fx/sentiment เป็นข้อมูลเสริม,
  ต่างจาก `data/fetcher` ที่ราคาต้อง fail loud)
- ✅ ทุก edge เพิ่มเฉพาะ AI Advisor/allocation — daily check ยัง numbers-only
