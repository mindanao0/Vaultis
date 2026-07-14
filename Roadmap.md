# ROADMAP: ยกระดับ Vaultis เป็นเครื่องมือ DCA ที่มี edge ของคนไทย (ฉบับเต็ม ~19 ฟีเจอร์)

> เอกสาร authoritative แหล่งเดียวของทิศทางพัฒนา Vaultis
> **หลักจัดลำดับ:** (1) พิสูจน์ก่อนสร้าง — backtest เป็นด่านกั้น (2) วางฐานความจริง/ต้นทุนก่อนต่อยอด
> (3) low-risk high-value ก่อน (4) money-moving/เสี่ยงสูงไว้ท้าย

## หลักที่ห้ามละเมิด (invariant — ทุกเฟส)
- **ทุกเลขที่ขยับเงินต้องอธิบายซ้ำได้ 100%** — คะแนนแกน (`score_from_prices`) ไม่แก้; ตัวคูณที่ขยับเงินโชว์แยกทุกชั้น
- **ข่าว/sentiment = context ข้าง ๆ ไม่เคยเข้าในเลข**
- **ข้อมูลหาย = fail-soft เป็น neutral/NO DATA ไม่ใช่เลขปลอม** (ยกเว้นราคา = fail loud ตาม `data/fetcher`)
- ทุก tilt ใหม่ต้อง **bounded** + มี **kill-switch (env flag)** + โชว์เป็นบรรทัดแยก
- **money-moving (ระวังเป็นพิเศษ):** regime tilt, FX budget signal, rebalance-with-cashflow (#12)

## Ops & rollout (บังคับกับทุก money-moving feature)
- Feature flag: `VAULTIS_REGIME_TILT`, `VAULTIS_FX_TIMING` (ค่าเริ่มต้น = **ปิด**); เปิดต่อเมื่อ Phase 0 ผ่านเกณฑ์ตัวเลข
- Backward-compatible output: field ใหม่ใน allocation dict ต้องอ่านด้วย `.get(...)` ฝั่ง consumer (`dashboard/app.py`, PDF, `alerts/notifier.py`) — ไม่ index ตรง

---

## Phase 0 — ฐาน + พิสูจน์ (ทำก่อนสุด, de-risk ทั้ง roadmap)

### 1. Backtest A/B พิสูจน์ edge — **ด่านกั้น**
- **ทำอะไร:** harness เทียบ 3 เส้น: plain DCA (เป้าหมายคงที่) vs DCA+regime tilt vs DCA+regime+FX ย้อนหลัง ≥5 ปี
- **reuse:** `portfolio/dca.py::simulate_dca(monthly_amount, weights, start_date)` + `portfolio/backtest.py::run_portfolio_backtest` (Sharpe/max DD/เทียบ VOO)
- **ต้องแก้:** ทั้งคู่รับ `weights` คงที่ → ย้ายการคำนวณ weight เข้า loop รายเดือน เพื่อให้ tilt แปรตามเวลาได้
- **เกณฑ์ผ่าน (ตัวเลข):** บนช่วง ≥5 ปี tilted ต้อง (ก) Sharpe ≥ plain, (ข) max DD ไม่แย่ลงเกิน 1 จุด %, (ค) มูลค่าปลายทางไม่ต่ำกว่า plain อย่างมีนัย → ไม่ครบ 3 ข้อ = **ไม่เปิด flag**
- **verify:** รัน harness ออกตาราง 3 เส้น + assert เกณฑ์; ยืนยันว่าปิด flag แล้วเส้น tilted = เส้น plain เป๊ะ

### 2. แก้บั๊กค่าธรรมเนียมไม่ตรงกัน
- **บั๊กจริง:** `portfolio/tracker.py::_calculate_dime_fee_info` คิดค่าคอมเฉพาะ **เทรดที่ 2+ ของเดือน** (`trade_number_in_month > 1` → เทรดแรกฟรี) แต่ `backend/services/rebalance_service.py:93` คิด `DIME_FEE_RATE` **ทุก action ที่ไม่ใช่ hold** → ประมาณต้นทุนไม่ตรงกัน
- **ทำอะไร:** ดึงสูตรค่าคอมเป็นฟังก์ชันเดียว (เช่น `portfolio/fees.py::estimate_dime_fee`) ให้ทั้ง tracker + rebalance เรียกร่วม; นิยาม "เทรดแรกของเดือนฟรี" ให้ชัดว่ารวม rebalance ด้วยหรือไม่
- **verify:** unit test: เทรดแรกของเดือน fee=0, เทรดที่สอง fee=0.15%; rebalance ใช้กติกาเดียวกัน

### 3. แหล่งราคาสำรอง (J)
- **ทำอะไร:** เพิ่ม fallback (Stooq / Alpha Vantage) หุ้ม `data/fetcher.py` — yfinance ล่ม → ลองแหล่งสำรองก่อน raise
- **คงหลักเดิม:** ทุกแหล่งล่มค่อย `raise PriceDataUnavailableError` (fail loud — ราคาเป็นข้อมูลหลัก)
- **verify:** mock yfinance ล่ม → ได้ราคาจากสำรอง; mock ทุกแหล่งล่ม → raise (ไม่คืน 0/frame ว่าง)

---

## Phase 1 — moat edges (deterministic จากราคา/ค่าเงิน) — *gated ด้วย Phase 0 + flag*

### Edge 2 (#R) regime tilt — ตัวคูณ per-ticker
- **ไฟล์ใหม่ `analysis/regime.py`** (deterministic): แมปกลุ่มความเสี่ยง (QQQM=aggressive, VOO=broad, SCHD=income, XLV=defensive, GLDM=hedge); คำนวณจากราคา 5 ตัว (`_yf_close_series`): spread โมเมนตัม 3 เดือน QQQM−GLDM, breadth เหนือ MA200 (`ta.sma` ผ่าน `ta_compat`), GLDM+XLV outperform = risk-off
- คืน `regime`(risk_on/neutral/risk_off) + `regime_tilt` ต่อกลุ่ม **bounded 0.8–1.2**; fail-soft→1.0
- **แก้ `analysis/financial_model.py::calculate_allocation`:** `weights[t] = base × _score_tilt(...) × regime_tilt(t)`; เพิ่ม field `regime_tilt`,`regime`; **ไม่แตะ `score_from_prices`/`_score_tilt`**; gate ด้วย `VAULTIS_REGIME_TILT`
- **verify:** ราคา risk-off จำลอง → regime="risk_off", GLDM/XLV tilt>1, QQQM<1; assert เอียง **หลัง normalize** จริง + `total_pct` ไม่เปลี่ยน; ปิด flag → allocation เท่าเดิมเป๊ะ

### Edge 1 (#F) FX timing — สัญญาณระดับงบ (ไม่ใช่ per-ticker)
- **ไฟล์ใหม่ `analysis/fx_timing.py`:** ดึงประวัติ USDTHB (`THB=X` 1–2y ผ่าน `data.fetcher.normalize_close_series`); คำนวณ `fx_zone` จาก percentile เทียบ 1 ปีตัวเอง — บาทแข็ง(percentile ต่ำ)=เติมคุ้ม, บาทอ่อน=เติมปกติ
- คืน `{fx_rate,is_live,fx_zone,budget_multiplier}` multiplier **0.9–1.1**; ถ้า `is_live=False` หรือดึงประวัติไม่ได้ → zone="ไม่ทราบ", multiplier=1.0; gate `VAULTIS_FX_TIMING`
- **แสดงผล:** บรรทัดแยกใน advisor/Discord ("💱 โซนบาท: แข็ง — เดือนนี้เติมคุ้ม") ไม่ยัดเข้า per-ticker weight
- **เหตุผลออกแบบ:** FX เท่ากันทุก ETF (ทุกตัว USD) → per-ticker หายตอน normalize จึงต้องอยู่ระดับงบ
- **verify:** mock ค่าสำรอง → multiplier=1.0 zone="ไม่ทราบ" (fail-soft); `pytest` allocation+cost-guard ผ่าน

---

## Phase 2 — ชั้นความจริง & ต้นทุน (moat ไทยที่แข็งสุด)

### 4. ภาษี + ต้นทุนจริง
- **ทำอะไร:** ชั้นภาษีหัก ณ ที่จ่าย **15% ปันผล US** (กระทบ SCHD หนักสุด) + FX spread + ค่าคอมขั้นต่ำ ทับต้นทุนปัจจุบัน
- **reuse:** ค่าคอม `FEE_RATE=0.0015` (`tracker.py`), FX mid-rate (`utils/fx.py::get_usdthb`) — เพิ่มพารามิเตอร์ spread ทับ mid
- **ที่ต้องแทน:** disclaimer "ไม่คิดภาษี" ในdashboard → ตัวเลขจริง
- **verify:** ปันผล SCHD $100 → บันทึกรับสุทธิ $85; ผลตอบแทน SCHD ลดลงเทียบ pre-tax

### 5. ติดตามเงินปันผลจริง + DRIP
- **ทำอะไร:** ledger ปัจจุบัน buy-only → เพิ่ม tx type "รับปันผล" (`portfolio/tracker.py` schema), คำนวณ income หลังภาษี(ข้อ 4), จำลอง DRIP (นำปันผลซื้อคืน)
- **ระวัง:** CSV เป็น single source (CLAUDE.md) — เพิ่มคอลัมน์/ประเภท ไม่สร้าง store ใหม่
- **verify:** เพิ่มแถวปันผล → summary แยก income vs capital gain; DRIP on → จำนวนหุ้นเพิ่ม

### 6. ผลตอบแทนจริงหลังภาษี+FX+เงินเฟ้อ (B) — *ต่อยอดข้อ 4-5*
- **ช่องว่างจริง:** `analysis/macro.py` ใช้ **CPI สหรัฐ** (`CPIAUCSL`) → ต้องเพิ่ม **Thai CPI** (FRED series ไทย หรือแหล่งอื่น) เป็นแหล่งใหม่ ก่อนคิด real return เป็นบาท
- **ทำอะไร:** real return บาท = nominal − ภาษี − FX − Thai inflation (ต่อ `_cpi_yoy_percent` เดิมแต่ป้อน series ไทย)
- **verify:** ป้อน CPI ไทย mock → real return < nominal ตามคาด; ไม่มีข้อมูล → NO DATA ไม่ใช่ 0

---

## Phase 3 — personalization & ความโปร่งใส
> **⚠️ Blocker:** `DATABASE_URL`/Postgres ต่อไม่ติด (AUDIT ค้าง) → #7(อัตโนมัติ)+#8 รันบน GH Actions ไม่ได้ (CI มองไม่เห็น ledger + อ่าน sentiment_results ไม่ได้) → ทำได้เฉพาะบนเครื่อง user จนกว่าจะแก้ Postgres ก่อน

### 7. Edge 3 drift personalization (advisory เท่านั้น)
- **reuse:** `portfolio/tracker.get_portfolio_summary()` + `portfolio/targets.get_target_weights()`
- **ทำอะไร:** คำนวณ drift (สัดส่วนจริง vs เป้าหมาย, over/underweight, ต้นทุนเฉลี่ย vs ราคาปัจจุบัน) → บล็อก "=== พอร์ตของคุณ ===" ใน `analysis/ai_advisor.py::_build_user_message`
- **ทำไมแค่ advisory:** rebalance service แยกอยู่แล้ว (CLAUDE.md แยก DCA/rebalance) → ไม่เพิ่ม money-moving tilt จาก drift
- **verify:** holdings overweight QQQM → บล็อกระบุ drift ถูก; ไม่มีพอร์ต → บอกตรงว่าไม่มีบริบท

### 8. ข่าว/Sentiment เป็น context
- **ทำอะไร:** เพิ่ม `get_latest_sentiment_summaries(symbols)` ใน `db/sentiment_models.py` (query `SentimentSummary`; `SessionLocal is None`→`{}`; try/except fail-soft) → ต่อเข้า `_build_user_message` เป็นบล็อก "=== ข่าว/Sentiment ===" (score/confidence/จำนวนข่าว/as_of + หมายเหตุ "แยกจากเทคนิคอล ห้ามรวม")
- อัปเดต `VAULTIS_ADVISOR_SYSTEM_PROMPT`: เทคนิคอล vs ข่าวขัดกัน → ยึดเทคนิคอล, เตือนโซเชียลปั่นง่าย, ข่าวน้อย=สัญญาณอ่อน
- **verify:** ไม่มี `DATABASE_URL` → `{}` ไม่ crash; `get_monthly_advice(user_initiated=False)` จบไม่มี LLM call แฝง

### 9. Audit trail "ทำไมได้เท่านี้" (D)
- **ทำอะไร:** surface การแตกคะแนน trend/timing/momentum + regime ทุกชั้น (field มีใน `score_from_prices` แล้ว แค่โชว์)
- **verify:** ทุกตัวเลขที่โชว์บวกกลับได้เป็นคะแนนรวม (reproducible)

### 10. เตือน overlap + factor/sector exposure (F)
- **reuse:** `analysis/correlation.py::calculate_correlation` + `get_correlation_insight`
- **ทำอะไร:** เตือน VOO∩QQQM (big tech ทับ), XLV=healthcare กระจุก, GLDM=ทอง → แสดง exposure รวมจริง
- **verify:** พอร์ต QQQM+VOO หนัก → เตือน tech overlap สูง

---

## Phase 4 — ลงมือได้จริง & วินัย

### 11. "รายการซื้อเดือนนี้" พร้อม execute (G)
- **ทำอะไร:** แปลง allocation (บาท) → จำนวนหุ้นจริง + fee + FX พร้อมข้อความ copy ไปวางในโบรก (Dime)
- **reuse:** allocation จาก `calculate_allocation`, fee จากข้อ 2, FX จาก `get_usdthb`
- **verify:** งบ 5,000 → รายการหุ้นรวม fee ไม่เกินงบ; เศษหุ้นจัดการถูก

### 12. Rebalance ด้วยเงินใหม่ tax-smart (H) — *money-moving*
- **ทำอะไร:** เท DCA เดือนนั้นเข้าตัว underweight แทนการขาย (เลี่ยงภาษี capital gain) — ต่อ `rebalance_service` (drift 5%) เข้าแผน DCA
- **ระวัง:** ยังต้องซื้อทุก ETF ที่มีข้อมูล (CLAUDE.md DCA policy) — rebalance แค่ tilt สัดส่วน ไม่ drop ตัวใด
- **verify:** ตัว underweight ได้สัดส่วนมากขึ้นแต่ไม่มีตัวไหนเป็น 0; ไม่มีคำสั่งขาย

### 13. โค้ชกันแพนิก + stress test (I)
- **reuse:** `analysis/risk.py::calculate_max_drawdown` + `_suggest_alert_levels` (`ai_advisor.py:209`, deterministic)
- **ทำอะไร:** ตรวจ drawdown ปัจจุบัน → บริบทประวัติศาสตร์ "2008/2020 ลง X% ฟื้น Y เดือน" + ติดตามว่าทำตามแผนไหม
- **verify:** drawdown −20% → ข้อความให้กำลังใจ + สถิติฟื้นตัวจริง ไม่ใช่คำแต่ง

### 14. เทียบ benchmark ต่อเนื่อง (E)
- **reuse:** `portfolio/backtest.py::run_portfolio_backtest` (เทียบ VOO อยู่แล้ว)
- **ทำอะไร:** โชว์ "พอร์ตจริงชนะ VOO ไหม" ต่อเนื่อง (alpha สะสม)
- **verify:** พอร์ต mock → alpha vs VOO ถูกต้องตามช่วง

### 15. Monte Carlo ผูกพอร์ตจริง (C)
- **reuse:** `backend/services/goal_service.py::calculate_probability(n_simulations=1000)`
- **ทำอะไร:** ป้อนพอร์ตจริง + จังหวะ DCA จริง (แทนสมมติฐานคงที่) → ความน่าจะถึงเป้า
- **verify:** เพิ่มงบรายเดือน → prob ถึงเป้าเพิ่มตามคาด

---

## Phase 5 — reach & AI ขั้นสูง (เสี่ยง/แพงสุด — ท้ายสุด)

### 16. แจ้งเตือน LINE (A)
- **ทำอะไร:** เพิ่มช่อง LINE Messaging API ข้าง Discord (`alerts/notifier.py`) / Telegram (`backend/screener/notifier.py`)
- **ระวัง:** token LINE เป็น secret env-only (เหมือน `DISCORD_WEBHOOK_URL` — ห้ามลง config.json)
- **verify:** ยิงข้อความทดสอบเข้า LINE; ไม่มี token → ข้ามเงียบ ไม่ crash

### 17. Edge 4 AI นักวิเคราะห์ + scenario
- **reuse:** `analysis/forecaster.py::Forecaster.forecast(symbol, days)` (Prophet) + `_suggest_alert_levels`
- **ทำอะไร:** สร้าง scenario band (percentile 20/50/80) จากโค้ด → AI เขียน **narrative จาก band ที่คำนวณแล้ว** (เลขมาจากโค้ด); pre-commit dip-buy ต่อ `_suggest_alert_levels`
- **gate:** `user_initiated=True` เท่านั้น (มีค่า token) ผ่าน `analysis/llm.py`
- **ระวัง:** hallucinate (prompt ย้ำห้ามแต่งเลข), token cost (จำกัด scope/แคช)
- **verify:** band เป็นตัวเลขก่อนเข้า prompt; รัน `user_initiated=True` → narrative ไม่ขัดกับเลขที่ป้อน

---

## หลักที่ห้ามละเมิด (ย้ำ — ทุก phase)
- ❌ ห้าม sentiment/ข่าวแตะ `score_from_prices`, `total_pct`, หรือกลายเป็นตัวคูณเงิน
- ❌ ห้ามจับจังหวะ FX จากค่าสำรอง (`is_live=False`) — fail-soft เป็น neutral
- ❌ ทุก tilt ใหม่ต้อง bounded + มี kill-switch + โชว์บรรทัดแยก
- ❌ ข้อมูลหาย = fail-soft เป็น neutral/NO DATA ไม่ใช่เลขปลอม (regime/fx/sentiment เป็นข้อมูลเสริม ต่างจากราคาที่ fail loud)
- ✅ ทุก edge เพิ่มเฉพาะ AI Advisor/allocation — daily check ยัง numbers-only
