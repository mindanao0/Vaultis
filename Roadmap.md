# ROADMAP: ยกระดับ Vaultis เป็นเครื่องมือ DCA ที่มี edge ของคนไทย (ฉบับเต็ม ~19 ฟีเจอร์)

> เอกสารนี้คือ roadmap รวมทุกฟีเจอร์ที่ตกลงกันไว้ จัดเป็นเฟสตาม dependency + ความเสี่ยง + ความคุ้ม
> **หลักจัดลำดับ:** (1) พิสูจน์ก่อนสร้าง — backtest เป็นด่านกั้น (2) วางฐานความจริง/ต้นทุนก่อนต่อยอด
> (3) low-risk high-value ก่อน (4) money-moving/เสี่ยงสูงไว้ท้าย
> แผน Phase 1 เดิม (regime+FX) ฉบับละเอียดอยู่ท้ายเอกสาร (คงไว้เป็นสเปกอ้างอิง)

## บันทึกการตัดสินใจ (ยืนยันกับผู้ใช้ 2026-07-16)
1. **ลำดับลงมือจริง: Phase 0 ก่อน** → แกน A/B (B1→A1→A3→ที่เหลือ) → Phase 1 → Phase 2+ (สลับจากลำดับเดิมที่ให้แกนมาก่อน)
2. **ค่าธรรมเนียม Dime = 0.15% ทุก transaction** (ยืนยันจากบัญชีจริง — ไม่มีเทรดแรกของเดือนฟรี) → ฝั่ง `tracker.py` คือตัวผิด แก้ให้ตรงกับ `rebalance_service`; สูตรเดียวใน module กลาง
3. **Backtest A/B รันสองช่วงเสมอ:** (ก) ช่วง proxy — QQQ แทน QQQM, GLD แทน GLDM ตั้งแต่ 2011-10 (จุดเกิด SCHD, ~14 ปี) (ข) ช่วงข้อมูลจริงล้วนตั้งแต่ 2020-11 (~5.7 ปี) — ติด label ชัดว่าช่วงไหนใช้ proxy; ด่านกั้นตัดสินจากภาพรวมทั้งสองช่วง
4. **B2/B4 ทำตามสเปกเดิม** (B2 ป้อน tilt ได้เลย, B4 แก้ `_timing_score` ได้) — เป็น**ข้อยกเว้นที่อนุมัติแล้ว**ของ invariant "คะแนนแกนไม่แก้" เงื่อนไข: ถึงคิวแล้วต้องรันผ่าน harness Phase 0 ดูผลก่อน merge

## หลักที่ห้ามละเมิด (invariant — ทุกเฟส)
- **ทุกเลขที่ขยับเงินต้องอธิบายซ้ำได้ 100%** — คะแนนแกน (`score_from_prices`) ไม่แก้; ตัวคูณที่ขยับเงินโชว์แยกทุกชั้น *(ข้อยกเว้นเดียวที่อนุมัติ: B4 — ดูบันทึกการตัดสินใจข้อ 4)*
- **ข่าว/sentiment = context ข้าง ๆ ไม่เคยเข้าในเลข**
- **ข้อมูลหาย = fail-soft เป็น neutral/NO DATA ไม่ใช่เลขปลอม** (ยกเว้นราคา = fail loud ตาม `data/fetcher`)
- ทุก tilt ใหม่ต้อง **bounded** + โชว์เป็นบรรทัดแยก
- **money-moving items (ทำระวังเป็นพิเศษ):** regime tilt, FX budget signal, rebalance-with-cashflow (H)
- **การพยากรณ์ — ยึด horizon:** สั้น (วัน/สัปดาห์) = ทำนายไม่ได้ ห้ามโชว์ราคาเป้าเป็นจุดเดียว;
  ระยะยาว (สิบปี = horizon ของ DCA) = คาดได้เชิงความน่าจะเป็น → **นำด้วย Monte Carlo (ช่วง %/ความน่าจะเป็น)**
  regime/MA/scorecard = "อธิบายปัจจุบัน" (horizon 0) ไม่ใช่เครื่องทำนาย

---

# ⭐⭐ แกนหลัก — มองกราฟออก + วิเคราะห์เก่งขึ้น (ทำก่อน 19 ข้อด้านล่าง)

> **ลำดับที่ยืนยันกับผู้ใช้ (อัปเดต 2026-07-16):** Phase 0 (ฐาน+พิสูจน์) ก่อน → แล้วแกนนี้ → ค่อย "ชั้นเสริม 19 ข้อ" ที่เหลือ
> เหตุผล: 19 ข้อเป็น "เปลือก" (แจ้งเตือน/ภาษี/tracking) ไม่ได้ทำให้เข้าใจตลาดหรือเลือกซื้อดีขึ้น
> แกนที่แท้จริง = "เห็นกราฟแล้วเข้าใจ + รู้ว่าเดือนนี้ซื้ออะไรเพราะอะไร"
> หลัก: **ไม่มีเลขใหม่ ไม่มี AI** — แค่เอา trend/timing/score/regime ที่คำนวณอยู่แล้วมา "วาด" และ "ประกอบ"

## ตัดทิ้ง (ไม่เหมาะ DCA — เป็น "จับจังหวะ" ไม่ใช่ "ปรับขนาด")
เส้นแบ่ง: ตอบ "ซื้อเท่าไร/เอียงตัวไหน" = เข้ากับ DCA | ตอบ "ซื้อตอนนี้หรือรอ" = market timing ปลอมตัว
- ❌ **Candlestick pattern** (doji/hammer/engulfing) — สัญญาณ 1-3 แท่ง หมดอายุใน 1 สัปดาห์; บน ETF วงกว้าง = noise สร้างความมั่นใจปลอม
- ❌ **Chart geometry** (H&S, cup&handle, double bottom) — subjective, confirmation bias, ทำนาย swing = จับจังหวะ
- ❌ **Breakout / ATH alert** — ยั่วให้ "รอย่อ"; แต่ในอดีตซื้อที่ ATH ของ index ให้ผลพอ ๆ/ดีกว่าซื้อวันสุ่ม → "รอ" = ถือเงินสดเปล่า ผิดตาราง DCA
- ⏸️ **Volume analysis** — ต้อง plumb OHLCV ใหม่ (ตอนนี้ close-only) + คุณค่าต่ำสำหรับคนถือ 10 ปี → เลื่อน

## (A) "มองกราฟออก" — chart/visual (ช่องว่างใหญ่สุด)
- **A1. ⭐ Candlestick วาดเหตุผลลงบนกราฟ** — พื้นหลัง shade เขียวเมื่อ price≥MA200, ★golden/✕death cross บนบาร์จริง, สามเหลี่ยมวันที่เป็น ACCUMULATE, default **weekly**. ย้าย signal ขึ้นมาบนราคา เห็นแวบเดียว. *reuse: `app.py:1080-1148`, `crossover_detector.py` (backend/screener/), `fetch_ohlc_data`(app.py:997). เสี่ยง: `fetch_ohlc_data` คืน `{}` ไม่ fail-loud → ต้อง guard; `crossover_detector` คืนแค่ bool "เพิ่ง cross ใน 3 วัน" → ต้องเขียน helper สกัดวันที่ cross ทั้งหมดย้อนหลังจาก MA series; วาดเฉพาะ ACCUMULATE/BULLISH ไม่มีลูกศรขาย. effort M*
- **A2. Trend channel** (linear regression log ±2σ) — เส้นเทรนด์หลายปี + แถบ σ + badge "+1.8σ = เติมพิเศษน้อยลง". ตอบ "ซื้อใกล้ยอดหรือก้น" เชิงสถิติ. *reuse: `numpy.polyfit` (ไม่มี dep ใหม่). effort M เสี่ยงต่ำ*
- **A3. ⭐ Underwater/drawdown chart** — % ต่ำกว่า ATH ตามเวลา + การฟื้นรอบก่อน. กัน panic-selling ตรง ๆ. *reuse: ต่อ cumulative-max ใน `analysis/risk.py:52` (`calculate_max_drawdown` ปัจจุบันคืน scalar ต่อ ticker — ต้อง expose ซีรีส์ underwater ที่เป็นค่ากลางในฟังก์ชันอยู่แล้ว). effort S-M, close-only = fail-loud ฟรี*
- **A4. จุดซื้อของคุณ + เส้น cost-basis** บนกราฟ — เห็นการเฉลี่ยต้นทุนจริง. *reuse: `portfolio/tracker.py`, `utils/fx.py`. เสี่ยง: โชว์ `is_live=False` ตรง ๆ; dashboard-only (ledger gitignored). effort M*

## (B) "วิเคราะห์/เลือกเก่งขึ้น" — analytical depth
- **B1. ⭐ Scorecard 5 ETF + การ์ด "คำตัดสินเดือนนี้"** — เรียง 5 ETF, stacked bar 4 องค์ประกอบ (Trend/Timing/Momentum/Dividend), reason chips ("เหนือ MA200 ✓", "RSI 34 ถูก"), THB ที่จะซื้อ. รวมคำตอบจาก 4 หน้าเป็นหน้าเดียว. *reuse: `build_etf_scores`(fm:536), `calculate_allocation`, score bar app.py:1435. effort M. **เสี่ยง market-timing:** label "น้ำหนัก/tilt เดือนนี้" ห้าม "เลือกตัวเดียว/ข้าม GLDM"; `data_ok=False` = "ไม่มีข้อมูล" ไม่ใช่ 0*
- **B2. Relative-strength ranking → ป้อน tilt** — momentum ปรับความเสี่ยง (3M/6M/12M ÷ vol). RS cross-sectional ยัง ABSENT. *reuse: `analysis/returns.py`, `risk.py`, `TILT_MIN/MAX`. เสี่ยง: ป้อน tilt เท่านั้น ทุกตัวยังซื้อทุกเดือน*
- **B3. Multi-timeframe confluence** (Daily+Weekly ตรงกัน = มั่นใจสูง) — รัน `dca_signal` เดิมบนแท่ง weekly ด้วย RSI14 weekly + **MA10w/MA40w** (เทียบเท่า MA50d/MA200d — ห้ามใช้ MA50/MA200 บนแท่ง week ตรง ๆ เพราะ MA200w = ค่าเฉลี่ย ~4 ปี, QQQM ข้อมูลไม่พอ). effort M เสี่ยงต่ำ
- **B4. Stretch gauge** (distance-from-MA200 percentile) — เปลี่ยน trend gate จาก on/off เป็น dimmer. *reuse: `ta_compat.sma`, ต่อ `_timing_score`(fm:213). effort S-M*
- **B5. Seasonality** (เดือนไหนเคยอ่อน) — **เชิงบรรยายเท่านั้น ห้าม override score**. *reuse: `analysis/returns.py`. เสี่ยง: noisy บน 10y*
- *ของแถมถูก:* score **waterfall** แทน flat bar (app.py:1435) + **donut annotated tilt** (app.py:1576, "SCHD ×1.3 เพราะ RSI ถูก") — effort S ปิดลูป score→tilt→THB

## เริ่มจาก 3 อันนี้ (หลังจบ Phase 0 — ตามมติ 2026-07-16)
1. **B1 Scorecard 5 ETF** — ประกอบเลขที่มีอยู่ล้วน ๆ, leverage การตัดสินใจสูงสุด
2. **A1 Candlestick วาดเหตุผล** — ปิดช่องว่าง chart-form ที่ใหญ่สุด
3. **A3 Underwater chart** — ถูกสุด, กัน panic-selling, fail-loud ฟรี

**Verification (แกน):** แต่ละ chart render ด้วยข้อมูลจริง 5 ETF โดยไม่ crash เมื่อ `data_ok=False` (โชว์ "ไม่มีข้อมูล"); B1 ตัวเลขตรงกับ `build_etf_scores`/`calculate_allocation` เป๊ะ (ไม่ re-compute ใน UI); A1 วาด cross ตรงวันจาก `crossover_detector`

---

# ชั้นเสริม (เปลือก) — 19 ข้อ ทำ "หลัง" แกนกราฟ

> ยังมีคุณค่า (ภาษี/ต้นทุน/robustness เป็น moat จริง) แต่ทำหลังแกน ตามที่ผู้ใช้เลือก

## Phase 0 — ฐาน + พิสูจน์ (ทำก่อนสุด, de-risk ทั้ง roadmap)
1. ✅ **Backtest A/B พิสูจน์ edge** (harness เสร็จ+รันแล้ว 2026-07-17 — `portfolio/ab_backtest.py`) — เทียบ DCA เป้าหมายปกติ vs DCA ที่ tilt ย้อนหลัง
   - ใช้ซ้ำ: `portfolio/dca.py` `simulate_dca(weights,...)` + `portfolio/backtest.py` (Sharpe/max DD/เทียบ VOO)
   - หมายเหตุ: ทั้งคู่ใช้ fixed weights → ต้องย้ายการคำนวณ weight เข้าไปใน loop รายเดือนเพื่อรองรับ tilt ที่แปรตามเวลา (point-in-time เท่านั้น — ห้าม look-ahead)
   - **สองช่วงทดสอบ (มติข้อ 3):** ช่วง proxy (VOO/SCHD/QQQ/XLV/GLD) ตั้งแต่ 2011-10 + ช่วงข้อมูลจริงล้วนตั้งแต่ 2020-11; inception จริง: XLV 1998-12, VOO 2010-09, SCHD 2011-10, GLDM 2018-06, QQQM 2020-10
   - ค่าธรรมเนียมไม่กระทบผล A/B (0.15% ของงบเดือนเท่ากันทั้งสองแขน) → v1 ไม่คิด fee ทั้งคู่ และระบุไว้ใน docstring
   - **เป็นด่านกั้น:** ถ้า tilt ไม่ชนะ plain DCA → ทบทวน Edge 1/2 ก่อนลงมือ
   - **⛔ ผลรัน (2026-07-17, `python -m portfolio.ab_backtest`): ไม่ผ่านด่าน**
     - proxy 2011-10→2026-07 (178 ด., ลงทุน 1.78M): plain 5.976M (CAGR 15.03%, Sharpe 1.11, DD −21.0%) | tilt 6.005M (15.12%, 1.10, −21.2%; SCHD กลาง 11 ด.แรก) | VOO เดี่ยว 5.918M (15.93%, 1.06, −23.9%) → tilt ชนะมูลค่า +0.49% แต่แพ้ Sharpe 0.01
     - real 2020-11→2026-07 (69 ด., ลงทุน 690k): plain 1,100k (16.14%, 1.23, −19.4%) | tilt 1,097k (16.13%, 1.22, −19.4%; QQQM กลาง 9 ด.แรก) | VOO เดี่ยว 1,122k (17.15%, 1.14, −22.4%) → tilt แพ้ทั้งมูลค่า (−0.33%) และ Sharpe
     - **อ่านผล:** score-tilt ≈ plain ทุกมิติ (ต่าง <0.5% ในช่วง 5–15 ปี = ระดับ noise) — tilt 0.6–1.4× บนเงินเติมรายเดือนเป็นคันโยกเล็ก ไม่มี edge ที่พิสูจน์ได้ แต่ก็ไม่ทำร้ายพอร์ต; ส่วนพอร์ต 5 ตัวชนะ VOO เดี่ยวเชิง risk-adjusted (Sharpe/DD ดีกว่า) ทั้งสองช่วง = การกระจายทำงานจริง
     - **นัยต่อแผน:** Phase 1 (regime/FX tilt) ถูกด่านนี้ห้ามไว้จนกว่าจะทบทวน Edge 1/2; B2/B4 (ป้อน tilt) คุณค่าลดลงตามหลักฐานนี้ — ต้องผ่าน harness ก่อน merge ตามมติข้อ 4; แกนหลักหมวด A/B (B1/A1/A3 — งาน "วาด" ไม่มีเลขใหม่) **ไม่ถูก gate** เดินต่อได้เลย
2. ✅ **แก้บั๊กค่าธรรมเนียมไม่ตรงกัน** (เสร็จ 2026-07-17 — `portfolio/fees.py` สูตรกลาง, tracker/rebalance ใช้ร่วม, เทสต์คุม M12) — `portfolio/tracker.py:207` (เทรดแรกของเดือนฟรี — **ผิด**) vs `backend/services/rebalance_service.py:93` (คิดทุกครั้ง — **ถูกตามบัญชีจริง**, มติข้อ 2) → สูตรเดียว 0.15% ทุก transaction ใน module กลาง; **ห้าม rewrite ค่า fee ที่บันทึกแล้วใน ledger** (AUDIT M12 — เติมค่าประมาณเฉพาะแถวที่ไม่มีค่า)
3. ✅ **แหล่งราคาสำรอง (J)** (เสร็จ 2026-07-17 — `data/fallback.py` ต่อเข้า `get_current_prices` ใช้ทั้ง cron/dashboard/rebalance) — ให้ yfinance ล่มแล้วยังทำงาน (คง fail-loud เมื่อทุกแหล่งล่ม)
   - **นโยบาย:** fallback ใช้กับ "ราคาล่าสุด/สัญญาณวันนี้" เท่านั้น — **ห้ามผสมเข้า series ประวัติที่ใช้คำนวณ score** (Stooq ไม่ adjust ปันผล ≠ Adj Close → คะแนนเพี้ยนเงียบ ๆ)
   - ลำดับ: yfinance → Stooq (ฟรี ไม่ใช้ key) → Alpha Vantage (optional ผ่าน env, free tier 25 req/วัน); ที่มาแจ้งผ่าน log warning แทน field `source` ต่อค่า — คง contract เดิม `dict[str, float]` ของ `get_current_prices` เพื่อไม่แตะ caller ทุกจุด

## Phase 1 — moat edges (deterministic จากราคา/ค่าเงิน) — *gated ด้วย Phase 0* **⛔ ด่านไม่ผ่าน (2026-07-17) — ทบทวน Edge 1/2 ก่อนลงมือ (ผลอยู่ใน Phase 0 ข้อ 1)**
- **Edge 2: regime tilt** (per-ticker, bounded 0.8–1.2) — ไฟล์ใหม่ `analysis/regime.py`, ต่อ `calculate_allocation`
- **Edge 1: FX timing** (สัญญาณระดับงบ) — ไฟล์ใหม่ `analysis/fx_timing.py`
- *(สเปกละเอียดทั้งสองอยู่ท้ายเอกสาร)*

## Phase 2 — ชั้นความจริง & ต้นทุน (moat ไทยที่แข็งสุด)
4. **ภาษี + ต้นทุนจริง** — ภาษีหัก ณ ที่จ่าย 15% ปันผล US (กระทบ SCHD หนักสุด), FX spread, ค่าคอมขั้นต่ำ
   - ที่มีอยู่: ค่าคอม Dime 0.15% (`tracker.py:38`), FX mid-rate (`utils/fx.py`) — เพิ่มชั้นภาษี+spread ทับ
   - dashboard มี disclaimer "ไม่คิดภาษี" (`dashboard/app.py:1213`) → แทนที่ด้วยตัวเลขจริง
   - เพิ่ม disclaimer แยกบรรทัด: ภาษีเงินได้ไทยกรณีนำเงินกลับประเทศ (ปอ.161/2566 มีผลตั้งแต่ปีภาษี 2567) — **ไม่เข้าเลขคำนวณ** แค่แจ้งว่ามีประเด็นนี้อยู่
5. **ติดตามเงินปันผลจริง + DRIP** — ledger ปัจจุบัน buy-only (`tracker.py:23-33`) → เพิ่ม tx type "รับปันผล", คำนวณ income หลังภาษี, จำลอง DRIP
6. **ผลตอบแทนจริงหลังภาษี+FX+เงินเฟ้อ (B)** — real return เป็นบาท ด้วย Thai CPI — **`analysis/macro.py` ยังไม่มี Thai CPI (มีแต่ US `CPIAUCSL`)** → ต้องเพิ่ม source ใหม่ (ประเมิน FRED series ไทยที่หลายตัวถูก discontinue / BOT API / World Bank) + fail-soft เป็น "ไม่ทราบเงินเฟ้อ" — *ต่อยอดจากข้อ 4-5*

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
15. **Monte Carlo ผูกพอร์ตจริง (C) — ⭐ เครื่องพยากรณ์หลักของระบบ** — ต่อ `goal_service.calculate_probability()`
    (1000 sims มีอยู่แล้ว) เข้าพอร์ต+จังหวะ DCA จริง → "ด้วยจังหวะนี้ ถึงเป้าด้วยความน่าจะเป็น X%"
    นี่คือ horizon ที่ถูกต้องของ DCA (สิบปี, เชิงความน่าจะเป็น) — เป็นตัวเน้น
    (หมายเหตุ: `calculate_probability` ปัจจุบันใช้ normal dist + `EXPECTED_RETURNS` คงที่ต่อ risk profile → ต้องคำนวณ μ/σ จากน้ำหนักพอร์ตจริงแทน)

## Phase 5 — reach & AI ขั้นสูง (เสี่ยง/แพงสุด — ท้ายสุด)
16. **แจ้งเตือน LINE (A)** — เพิ่มช่องทาง LINE Messaging API ข้าง Discord/Telegram (localization)
17. **Edge 4: AI นักวิเคราะห์** — narrative จากเลขที่คำนวณแล้ว (gated `user_initiated=True`)
    - **ลดน้ำหนัก Prophet:** พยากรณ์ราคาหุ้นระยะสั้นเป็นจุด ๆ ห่วยและหลอกมือใหม่ →
      ถ้าใช้ Prophet ให้โชว์เป็น **"กรวยความไม่แน่นอน" เท่านั้น ห้ามเป็นราคาเป้า** และไม่ใช่ตัวเน้น
    - การพยากรณ์เชิงตัวเลขที่เป็นทางการของระบบ = Monte Carlo (ข้อ 15) ไม่ใช่ Prophet

---

# ภาคผนวก: สเปกละเอียด Phase 1 (regime + FX) — คงไว้อ้างอิงตอนลงมือ

## Context
ปัจจุบันสัญญาณ = RSI + MA + คะแนน (`analysis/financial_model.py`) — ดีและ deterministic แต่เป็นของโหล
แนวทาง: ต่อยอด tilt pipeline (`calculate_allocation`: `น้ำหนัก = เป้าหมาย × score_tilt(0.6–1.4)`)

## หลักการตัดสินใจ
- **คะแนนแกน** (`score_from_prices`) = สมอเรือ deterministic → **ไม่แก้**
- **ตัวคูณที่ขยับเงิน** = ปัจจัยที่คำนวณซ้ำได้ โชว์แยกทุกชั้น ไม่มัดรวมเป็นก้อนดำ
- **ข่าว/sentiment** = context ข้าง ๆ ไม่เข้าในเลข
- FX เท่ากันทุก ETF → หายตอน normalize → ทำงานระดับงบ; regime ต่างรายตัว → เอียงพอร์ตได้จริง

### Edge 2: regime (ไฟล์ใหม่ `analysis/regime.py` — deterministic)
- แมปกลุ่ม: QQQM=aggressive, VOO=broad, SCHD=income, XLV=defensive, GLDM=hedge
- คำนวณจากราคา 5 ตัว (`_yf_close_series`): spread 3เดือน QQQM−GLDM; breadth เหนือ MA200; GLDM+XLV outperform = risk-off
- คืน `regime` + `regime_tilt` ต่อกลุ่ม (bounded 0.8–1.2); fail-soft → 1.0
- แก้ `calculate_allocation`: `weights = base × _score_tilt × regime_tilt`; เพิ่ม field `regime_tilt`/`regime`; ไม่แตะ `score_from_prices`

### Edge 1: FX timing (ไฟล์ใหม่ `analysis/fx_timing.py` — สัญญาณระดับงบ)
- ดึงประวัติ USDTHB (`THB=X` 1-2y ผ่าน `normalize_close_series`)
- fx_zone จาก percentile: บาทแข็ง(ต่ำ)→เติมคุ้ม; บาทอ่อน→เติมปกติ
- คืน `{fx_rate, is_live, fx_zone, budget_multiplier}` (mult 0.9–1.1)
- fail-soft: `is_live=False`/ดึงไม่ได้ → zone="ไม่ทราบ", mult=1.0
- แสดงเป็นบรรทัดแยกใน advisor/Discord ไม่ยัดเข้า per-ticker weight

**Verification Phase 1:** regime risk-off จำลอง → GLDM/XLV tilt>1, QQQM<1; allocation เอียงหลัง normalize จริง; fx ค่าสำรอง→mult 1.0; `pytest` ผ่านหมด

## หลักที่ห้ามละเมิด (Phase 1)
- ❌ ห้าม sentiment แตะ `score_from_prices`/`total_pct`
- ❌ ห้ามจับจังหวะ FX จากค่าสำรอง — fail-soft neutral
- ❌ ทุก tilt bounded + โชว์แยก
- ❌ ข้อมูลหาย = fail-soft neutral/NO DATA (ราคา = fail loud)
- ✅ ทุก edge เพิ่มเฉพาะ advisor/allocation — daily check ยัง numbers-only
