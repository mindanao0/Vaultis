# Vaultis — รายงานตรวจสอบทั้งระบบ (Audit Report)

**วันที่:** 12 กรกฎาคม 2026
**ขอบเขต:** อ่านโค้ดทุกโมดูลที่เกี่ยวข้องกับการวิเคราะห์ การเงิน สัญญาณซื้อขาย AI และโครงสร้างพื้นฐาน (~11,600 บรรทัด) + ตรวจ git history, CI workflow, docker, config
**บริบทสำคัญ:** ผู้ใช้ตัดสินใจลงทุนด้วยเงินจริงจากผลของระบบนี้ → จัดลำดับความรุนแรงตาม "โอกาสทำให้ตัดสินใจผิดด้วยเงินจริง"

**สถานะการแก้ (อัปเดต 12 ก.ค. 2026): เฟส 1 และ เฟส 2 เสร็จแล้ว** — ดูรายละเอียดท้ายไฟล์ (§ บันทึกการแก้ไข)
เฟส 3 ยังไม่ได้ทำ

> 🚨 **เรื่องด่วนที่ผู้ใช้ต้องทำเอง: repo นี้เป็น PUBLIC** (`github.com/mindanao0/Vaultis`)
> Discord webhook **2 ตัว** อยู่ใน git history ที่ใครก็อ่านได้ → **ต้องสร้างใหม่ทันที**
> (ข่าวดี: `.env` ไม่เคยถูก commit → API keys ทั้งหมดปลอดภัย)

---

## สรุปผู้บริหาร

ปัญหาของระบบนี้**ไม่ใช่**ว่า AI (Groq/llama) ไม่เก่งพอเป็นหลัก — ปัญหาใหญ่กว่าคือ:

1. **ความล้มเหลวของข้อมูลถูกแปลงเป็น "สัญญาณปลอม" ทั้งระบบ** — ดึงราคาไม่ได้ = สัญญาณ "Avoid", ราคา = 0, พอร์ตขาดทุน -100% ปลอม โดยแยกไม่ออกจากผลวิเคราะห์จริง
2. **ระบบให้สัญญาณขัดแย้งกันเองกับข้อมูลเดียวกัน** — RSI 28 หน้าหนึ่งบอก "Strong Buy" อีกหน้าบอก "strong_sell"
3. **AI ถูกให้คิดเลขเองในจุดที่เงินจริงขึ้นกับผลลัพธ์** — กำหนดราคา alert เอง แบ่งงบ DCA เองในข้อความ ทั้งที่มีโค้ดคำนวณอยู่แล้วแต่ไม่ได้ต่อสายเข้าไป
4. **Dependencies ไม่ล็อกเวอร์ชันเลย + CI ติดตั้งใหม่ทุกสัปดาห์** — ระบบ production พร้อมพังหรือเพี้ยนเงียบ ๆ ทุกครั้งที่ library ออกเวอร์ชันใหม่
5. **Price alerts ที่ตั้งไว้ ไม่มี job ไหนตรวจจริงใน production** — ตั้งแล้วเงียบ อาจพลาดจังหวะซื้อโดยไม่มีทางรู้
6. **API สาธารณะไม่มี auth + ข้อมูลส่วนตัว/webhook ถูก commit ลง git**

---

## ระดับ CRITICAL — ทำให้ตัดสินใจผิดด้วยเงินจริงได้โดยตรง

### C1. ความล้มเหลวเงียบกลายเป็นสัญญาณปลอม (systemic)

พฤติกรรมปัจจุบันเมื่อ yfinance ล้มเหลว/โดน rate limit/เน็ตสะดุด:

| จุด | พฤติกรรมเมื่อข้อมูลพัง | ผู้ใช้เห็นเป็น |
|---|---|---|
| `data/fetcher.py:57-63` | กลืน exception ทุกชนิด คืน DataFrame ว่าง | หน้าวิเคราะห์ว่าง/ค่าเพี้ยนโดยไม่รู้สาเหตุ |
| `analysis/financial_model.py:394-395, 439-450` | series ว่าง → `total_score 0` → signal **"Avoid"** | **"AI บอกให้เลี่ยง VOO"** ทั้งที่แค่ดึงข้อมูลไม่ได้ |
| `backend/services/technical_service.py:158-161` | exception ใด ๆ → `price=0.0, signal="neutral"` | บทวิเคราะห์รายตัวจากตัวเลขขยะ |
| `portfolio/tracker.py:242` | ราคาที่หาไม่เจอ → `fillna(0.0)` | **พอร์ตโชว์ขาดทุน -100%** และค่านี้ถูกส่งให้ AI advisor ต่อ |
| `backend/screener/engine.py:54-55` | rule ที่ error → `False` | "วันนี้ไม่มีสัญญาณ" ทั้งที่จริงคือ "ตรวจไม่ได้" |
| `backend/routers/websocket.py:59` | ดึงไม่ได้ → broadcast ราคา 0.0 | ticker บนหน้าเว็บโชว์ $0.00 |

**วิธีแก้:** สร้างสถานะ "NO DATA / ข้อมูลไม่พอ" เป็น first-class ทั่วระบบ — ห้ามผลิตค่า 0, "Avoid", "neutral" จากความล้มเหลวเด็ดขาด และแสดงสถานะข้อมูล (as-of date + ครบ/ไม่ครบ) ในทุกหน้าจอ/ข้อความ Discord/prompt ที่ส่งให้ AI

### C2. สัญญาณขัดแย้งกันเองระหว่าง subsystem (ข้อมูลเดียวกัน → คำแนะนำตรงข้าม)

กรณี RSI = 28 (oversold):

- `analysis/financial_model.py:186-187` → technical score **สูงสุด 30 แต้ม** (เชิงซื้อ)
- `backend/screener/presets.py` preset `oversold_momentum` → **สัญญาณฝั่งซื้อ**
- `dashboard/app.py:979-984` → เข้าเกณฑ์ **"Buy Zone"** ได้
- แต่ `backend/services/technical_service.py:60-61` → RSI < 35 = **"bearish"** และ `analysis_service.py:54-55` ต่อยอดเป็น **"strong_sell"** ในหน้า ETF analysis

นอกจากนี้เกณฑ์ signal ยังมี 3 มาตรฐาน: `calculate_signal_score` (Strong Buy ≥ 80/110), `_pipeline_signal_label` (Strong Buy ≥ 60/~90), `calculate_allocation` (จัดกลุ่ม Strong Buy ที่ ≥ 60 ทั้งที่ label บอก 80) — คนละสเกล คนละความหมาย ใช้ชื่อเดียวกัน

**วิธีแก้:** สร้างโมดูล signal กลางหนึ่งเดียว (นิยาม RSI zone, MA state, เกณฑ์คะแนน, ป้าย signal) แล้วให้ dashboard / screener / per-symbol analysis / advisor import จากที่เดียว ตัดสินใจเชิงนโยบายให้ชัด: สำหรับนักลงทุน DCA ระยะยาว oversold = จังหวะสะสม ไม่ใช่ strong_sell

### C3. AI คิดเลขเองในจุดที่เงินขึ้นกับผลลัพธ์ (ทั้งที่มีโค้ดคำนวณอยู่แล้ว)

- `analysis/ai_advisor.py:184-279 (ai_suggest_alerts)` — ให้ llama **กำหนดราคา** buy/warning alert เอง ไม่มี sanity check ว่า `buy_alert < ราคาปัจจุบัน < warning_alert` และไม่ตั้ง `temperature`/`max_tokens` (ใช้ default temp=1.0 กับงานตัวเลข!)
- `analysis/ai_advisor.py:21-38` — system prompt สั่งให้ AI แบ่งงบ DCA 60/30/10 **ในข้อความ** (llama คิดเลขผิดง่าย) ทั้งที่ `financial_model.calculate_allocation()` มีอยู่แล้วแต่ `get_monthly_advice()` **ไม่เคยเรียกใช้**
- `dashboard/app.py:1272-1337 (_extract_allocation_df)` — dashboard **parse ตัวเลขจากข้อความ AI ด้วย regex** มาแสดงเป็นตารางการจัดสรรเงิน
- ย้อนแย้งกับ system prompt ตัวเอง: "You NEVER calculate numbers yourself"

**วิธีแก้:** โค้ดคำนวณทุกตัวเลข (alert levels จาก support/resistance/MA ที่คำนวณแล้ว, allocation จาก `calculate_allocation`) → ส่งผลลัพธ์สำเร็จรูปให้ AI **อธิบายอย่างเดียว** → dashboard แสดงตัวเลขจากโค้ดโดยตรง ไม่ parse ข้อความ AI

### C4. DCF กับ ETF โดยเฉพาะ GLDM (กองทองคำ) = ตัวเลขแต่งขึ้น

`analysis/financial_model.py:101-179` ทำ DCF โดยดึง `trailingPE` ของกอง — GLDM ไม่มีกำไร/ปันผล/PE → fallback PE = 20 → เสก "cash flow" = ราคา × 5% ให้ทองคำ → intrinsic value / margin of safety ของ GLDM **ไม่มีความหมายเลย** แต่ถูกให้น้ำหนัก **30/110 คะแนน** และโชว์เป็น Strong Buy/Avoid บนหน้า DCF Analysis + ส่งเข้า AI

เพิ่มเติม: `_DCF_SCORE_PLACEHOLDER = 15` ใน pipeline scores (`financial_model.py:371`) — advisor รายเดือนแจก DCF 15 แต้มฟรีให้ทุกตัวเท่ากัน โดยผู้ใช้ไม่รู้ว่าคะแนนส่วนนี้ไม่ได้คำนวณจริง

**วิธีแก้:** ตัด DCF ออกจาก GLDM (ใช้ momentum/trend แทน) และติดป้ายว่า DCF ของ ETF เป็น earnings-yield proxy ไม่ใช่ DCF จริง หรือตัดออกจากคะแนนรวมไปเลย; แสดงในหน้าจอว่าคะแนน DCF ใน advisor เป็น placeholder

### C5. requirements.txt ไม่ล็อกเวอร์ชัน + CI ติดตั้งใหม่ทุกสัปดาห์

`requirements.txt` ไม่มี `==` แม้แต่ตัวเดียว และ `.github/workflows/scheduler.yml` รัน `pip install -r requirements.txt` **ใหม่ทุกครั้ง** (จันทร์/วันที่ 1/ทุกวันทำการ):

- `pandas-ta` (เลิกพัฒนา) พังกับ `numpy>=2.0` (`from numpy import NaN` → ImportError) → screener, backtest engine, forecaster, technical service **ตายทั้งแถบ**ทันทีที่ CI ติดตั้ง numpy 2
- yfinance เปลี่ยน behavior บ่อย: default `auto_adjust` เคยพลิก, `dividendYield` เปลี่ยนจากสัดส่วน (0.035) เป็นเปอร์เซ็นต์ (3.5) → เกณฑ์ `div_yield > 0.04` ใน `financial_model.py:243` แตกทันที (ทุกกองได้ 10 แต้มหมด)
- prophet/vectorbt ผูก numpy/pandas เวอร์ชันแคบ

**วิธีแก้:** pin ทุกตัว (`pip freeze` จาก environment ที่ใช้งานได้จริง) + ตัด `pandas-ta` ทิ้ง (ใช้ `ta` ที่มี compat layer อยู่แล้ว) + ตัด `google-genai` (ไม่มีโค้ดใช้เลย — ที่ grep เจอมีแค่ URL ฟอนต์ Google ใน dashboard)

### C6. Price alerts ไม่มีอะไรตรวจจริงใน production

- GH Actions job ชื่อ "Run Price Alert" รัน `jobs/daily_check.py` ซึ่ง**ไม่ได้เช็ค alert** — และข้อความ Discord hardcode `"⚠️ Price Alerts: 0 items"` (`jobs/daily_check.py:115`)
- `check_alerts()` ตัวจริง (อ่าน `alerts/data/price_alerts.json`) ถูกตั้งเวลาเฉพาะใน `main.py --job all` (ต้องรันค้างบนเครื่องตัวเอง 09:00/21:00) หรือกดปุ่มใน dashboard เอง
- `main.py --job price_alert` ก็เรียก `run()` ของ daily_check ไม่ใช่ `check_alerts()` (`main.py:267-268`) — ชื่อ job หลอก
- แปลว่า alert ที่ AI แนะนำแล้วกด "ตั้ง Alert" ไว้ **อาจไม่มีวันแจ้งเตือน** ถ้าไม่ได้รัน scheduler เครื่องตัวเองค้างไว้

**วิธีแก้:** เพิ่ม step ใน GH Actions daily job ให้เรียก `check_alerts()` จริง (หรือย้ายไป APScheduler ใน backend) + แก้ตัวเลข alerts ใน daily message ให้มาจากข้อมูลจริง + แก้ชื่อ `--job price_alert`

---

## ระดับ HIGH

### H1. Backend สาธารณะไม่มี auth + ข้อมูลส่วนตัวใน git

- `backend/main.py:21-26` CORS `allow_origins=["*"]`, ทุก endpoint ไม่มี authentication — ใครรู้ URL (Render สาธารณะ) ทำได้: เพิ่ม/ลบธุรกรรม (SQLite), เผา Anthropic credits ผ่าน `/api/transactions/upload-slip`, ยิง Groq ผ่าน `/api/ai/*`
- ไฟล์ที่ **ถูก commit ลง git**: `vaultis.db`, `portfolio/data/transactions.csv` (ธุรกรรมจริง), `alerts/data/price_alerts.json`, และ `config.json` **พร้อม Discord webhook URL** — webhook ที่หลุดเปิดทางให้คนอื่นส่งข้อความปลอม ("สัญญาณซื้อ!") เข้า channel ที่คุณเชื่อถือ
- Render ฟรี = ephemeral disk → ข้อมูลที่เขียนผ่าน backend ที่ deploy ไว้ (SQLite/CSV/JSON) **หายทุกครั้งที่ redeploy**

**วิธีแก้:** (1) หมุน (regenerate) Discord webhook ทันที (2) เอาไฟล์ข้อมูลออกจาก git + .gitignore (3) ใส่ API key header ง่าย ๆ หรือจำกัด origin (4) ตัดสินใจให้ชัดว่า source of truth อยู่ local หรือ hosted — ถ้า hosted ต้องมี persistent volume/Postgres

### H2. ระบบเก็บข้อมูลซ้ำซ้อนไม่ sync กัน

- **ธุรกรรม 2 ระบบ:** CSV (`portfolio/tracker.py` — dashboard และ AI advisor ใช้) กับ SQLite `Transaction` (`/api/portfolio` ใช้) — ไม่มีโค้ด sync แม้ CLAUDE.md จะอ้างว่า "SQLite is the API layer on top"
- **Price alerts 2 ระบบ:** JSON file (dashboard + Discord + AI suggest) กับ SQLite `PriceAlert` (`/api/alerts`) — คนละชุดข้อมูลกันเลย
- ผล: มุมมองพอร์ต/alert แต่ละช่องทางไม่ตรงกัน และ AI advisor เห็นพอร์ตคนละชุดกับ API

**วิธีแก้:** เลือก 1 storage ต่อ 1 ประเภทข้อมูล (แนะนำ: SQLite เป็นหลัก + export CSV เป็น backup) แล้วให้ทุกช่องทางเรียกผ่าน service เดียว

### H3. ระบบ cache เป็น no-op ทั้งที่เอกสารอ้างว่ามี

`utils/cache.py:11-13` — `cache_data_1h` คืน function เดิมเฉย ๆ (คอมเมนต์บอกเองว่า no-op) แต่ CLAUDE.md อ้างว่า "Expensive calls use @cache_data_1h" ผลคือ:

- dashboard **ทุกครั้งที่กด widget ใด ๆ** (Streamlit rerun ทั้งสคริปต์) ดาวน์โหลดราคา 10 ปี × 5 ตัวใหม่
- `run_full_analysis` ยิง yfinance ~20+ ครั้ง/คำขอ + `time.sleep(1)` ต่อ ticker ใน request path (`/api/analysis/full`)
- โหลดหนัก → โดน yfinance rate limit → เข้าเงื่อนไข C1 (สัญญาณปลอม) บ่อยขึ้น

**วิธีแก้:** dashboard ใช้ `@st.cache_data(ttl=3600)` ครอบ fetch (มีตัวอย่างแล้วที่ `fetch_ohlc_data`) / backend ใช้ TTL cache ใน process (มี `CacheService` อยู่แล้วแต่แทบไม่ได้ใช้) — Redis ใน docker-compose ไม่มีโค้ดเรียกใช้เลย จะตัดหรือใช้จริงก็ได้

### H4. หน้า Overview การ์ด "Best/Worst ETF (1Y)" คำนวณผิดคอลัมน์

`dashboard/app.py:413-415` — `calculate_period_returns` คืน DataFrame ที่ **แถว = Period, คอลัมน์ = ticker** แต่โค้ดหา `"1Y (%)"` ในคอลัมน์ (ไม่มีวันเจอ — index ชื่อ "1Y") → fallback ไปคอลัมน์สุดท้าย = **ticker ตัวสุดท้าย (GLDM)** → `idxmax()` ได้**ชื่อช่วงเวลา** ("10Y") มาแสดงเป็น "Best ETF" พร้อมตัวเลขของ GLDM
รวมถึงการ์ด "Total Return (Basket) — 10Y" จริง ๆ เริ่มนับจากวันที่ทุกกองมีข้อมูลครบ (QQQM เกิด ต.ค. 2020) → เป็นผลตอบแทน ~5 ปีที่ติดป้าย 10Y

### H5. หน้า AI Advisor: ส่วนแสดงคะแนน/DCF ไม่เคยทำงาน + ตาราง allocation มาจากข้อความ AI

`dashboard/app.py:1449-1541 (show_result)` คาดหวัง key `full_analysis` / `analysis` / `allocation` จากผล `get_monthly_advice()` — แต่ฟังก์ชันนั้นคืนแค่ `budget_thb, etf_scores, macro, advice_text, discord_result` (`analysis/ai_advisor.py:320-326`) → ตาราง score, กราฟ intrinsic value, margin of safety **ไม่เคยแสดง** และตาราง allocation ตกไปใช้ regex parse ข้อความ AI (ดู C3) — ทั้งที่ caption หน้านี้เขียนว่า "คะแนนและ DCF คำนวณในระบบ — Groq ใช้เพื่ออธิบายเหตุผลเท่านั้น" ซึ่ง**ไม่ตรงกับความเป็นจริงของโค้ด**

### H6. คำตอบ AI ภาษาไทยโดนตัดกลางประโยค + งาน JSON ไม่คุมพารามิเตอร์

ทุกจุดที่เรียก Groq ตั้ง `max_tokens` 300-800 โดยไม่เช็ค `finish_reason` — ภาษาไทยบน tokenizer ของ llama กินโทเคนสูง (คร่าว ๆ 2-4 โทเคน/คำ) → คำแนะนำ 400 คำที่ prompt สั่ง อาจต้องการ >1,000 โทเคน → **โดนตัดกลางประโยคเป็นประจำ** โดยเฉพาะ `ai_advisor.py:128 (max_tokens=500)` และ `backtest_summary.py:49 (400)`; ส่วน `ai_suggest_alerts` ไม่ตั้งทั้ง temperature และ max_tokens (default temp ≈ 1.0 กับงานเลือกตัวเลข)

**วิธีแก้:** เพิ่ม max_tokens (เช่น 1,500-2,000), เช็ค `finish_reason == "length"` แล้ว retry/ต่อความ, งานที่ต้องการ JSON ใช้ `response_format={"type": "json_object"}` ของ Groq + temperature 0-0.2 + validate schema ก่อนใช้

### H7. หน้า Macro ข้อมูลผิดโดยโครงสร้าง

`dashboard/app.py:1580-1622` — "CPI Inflation" ใช้ ticker `CPIAUCSL` ซึ่งเป็น **FRED series ID ไม่ใช่ Yahoo ticker** → คอลัมน์ว่างตลอด → สรุป macro แทบไม่เคยครบ; "Fed Rate" ใช้ `^IRX` (T-bill 13 สัปดาห์) ติดป้าย Fed Rate; และใน `analysis/macro.py:196-199` "inflation_cpi.value" คือ**ระดับดัชนี CPI (~320)** ไม่ใช่อัตราเงินเฟ้อ — เทรนด์คำนวณจากดัชนีที่ขึ้นเสมอ → ข้อความ "เงินเฟ้อชะลอ" แทบไม่มีวันโผล่แม้เงินเฟ้อลดจริง

**วิธีแก้:** CPI ต้องผ่าน FRED (มี key อยู่แล้ว) และคำนวณ YoY % change; ^TNX/^IRX ติดป้ายตามจริง

### H8. Endpoint ที่น่าจะพังตั้งแต่เขียน (คืน ORM ผ่าน JSONResponse)

`backend/routers/portfolio.py:28-45` (`/history`, `/add`) และ `backend/routers/alerts.py` ส่ง SQLAlchemy ORM object เข้า `JSONResponse(content=...)` โดยตรง — `json.dumps` serialize ORM ไม่ได้ → คาดว่า 500 ทุกครั้ง (ต่างจากการ `return` object ให้ FastAPI encode เอง) — ควรยืนยันด้วยการรันจริง แล้วแปลงเป็น dict/Pydantic ก่อนคืน

---

## ระดับ MEDIUM

- **M1. RSI 3 มาตรฐานในโค้ดเดียว:** `technical/indicators.py:64` เติมช่วง warmup เป็น **100** (โผล่เป็น Overbought ปลอมในกราฟ), `analysis/ta_compat.py:17` fallback เติม **0** (Oversold ปลอม), library `ta` ให้ NaN — และฐานราคาไม่ตรงกัน (Adj Close ใน analysis, ราคา `Close` ดิบใน screener/backtest ซึ่งความหมายขึ้นกับเวอร์ชัน yfinance เพราะไม่ระบุ `auto_adjust`)
- **M2. Backtest สลับกลยุทธ์เงียบ:** `analysis/backtest_engine.py:69-76` ถ้า RSI+MACD ไม่มีสัญญาณ → ใช้ RSI อย่างเดียวโดย**ไม่บอกใน response** ว่าใช้กลยุทธ์ไหน; `optimize()` เป็น in-sample ล้วน (overfit) ไม่มี walk-forward; Sharpe NaN → 0.0 ทำให้แยก "แย่" กับ "ไม่มีข้อมูล" ไม่ได้; ถ้าทุก combination error `round(float("-inf"))` จะ crash
- **M3. Forecast สร้างความมั่นใจเกินจริง:** `analysis/backtester.py:79` `accuracy_pct = 100 - MAPE` — MAPE ราคา 3% กลายเป็น "แม่น 97%" ซึ่งไม่ใช่ความหมายทางสถิติที่ควรสื่อกับคนตัดสินใจเงินจริง; ใช้ RSI (ฟังก์ชันของราคาเอง) เป็น regressor แล้ว freeze ค่าคงที่ในอนาคต; `make_future_dataframe` รวมเสาร์-อาทิตย์; เทรนจาก `ticker.history()` (auto-adjusted) ต่างจากฐานราคาส่วนอื่น
- **M4. Backtest พอร์ต 2 เวอร์ชันพฤติกรรมต่างกัน:** `run_portfolio_backtest` (dashboard เรียก) `pct_change().fillna(0)` โดยไม่ dropna ทั้งแถว → ช่วงก่อน QQQM เกิด (ก่อน ต.ค. 2020) QQQM ถูกนับ return 0% ทั้งที่ถือ weight → ฉุดผลย้อนหลังผิด; เวอร์ชัน `run_backtest` จัดการถูกแล้ว (dropna + intersect benchmark) แต่ dashboard ไม่ได้ใช้ตัวนั้น; ทั้งคู่เป็น daily-rebalance โดยไม่บอกผู้ใช้; Sharpe ใช้ rf=0 ขณะ `risk.py` ใช้ rf=2% (ดอกเบี้ยจริง ~4.3%) — Sharpe คนละหน้าเทียบกันไม่ได้
- **M5. FX คนละแหล่งคนละค่า:** tracker ใช้ `THB=X` สด (`tracker.py:120-134`), net worth ใช้ `default_fx_rate` 33.5 คงที่จาก config (`networth_service.py:17-18`), rebalance ใช้ `USDTHB=X` fallback 35.0 (`rebalance_service.py:28-35`) → มูลค่า THB ต่างกันข้ามหน้า
- **M6. ป้ายภาษาไทยใน dashboard หาย (ไฟล์เสียจริง):** `dashboard/app.py` เหลืออักษรไทย 119 ตัว จาก 2,308 ตัวในอดีต (commit `dab1666`) — label จำนวนมากเป็นช่องว่าง เช่น `"  DCA   (THB)"` — เป็นผลจาก commit "Fix encoding" ที่ strip ตัวอักษรออกแทนที่จะแก้ encoding; กู้ข้อความจาก git history ได้
- **M7. DCA reminder edge case:** `main.py:181-183` เทียบ `tomorrow.day != dca_day` — ถ้าตั้ง dca_day = 29/30/31 เดือนที่ไม่มีวันนั้นจะไม่เตือนเลย; scheduler ใน `main.py` ใช้เวลาท้องถิ่นเครื่อง (ถ้ารันบนเซิร์ฟเวอร์ UTC เวลาจะเพี้ยน +7) ต่างจาก backend APScheduler ที่ตั้ง timezone ถูกแล้ว
- **M8. `suggest_alerts` ไม่ validate ราคา AI:** ควรบังคับ `buy_alert < current_price < warning_alert` และ bound ระยะห่าง (เช่น ±25%) ก่อนบันทึก
- **M9. goal_service:** คำเตือน "ผลตอบแทนที่ต้องการสูงกว่าโปรไฟล์" ไม่มีวันทำงาน (`goal_service.py:128` ส่ง expected_return เทียบกับตัวเอง) และ `suggest_allocation` ยัด key `"note"` (string) ปนใน dict weights (ตัวเลข) — เสี่ยง consumer พัง; สมมติฐานผลตอบแทน 7/9/12% ควรแสดงเป็นสมมติฐานให้ผู้ใช้เห็น
- **M10. debt_service:** ถ้างบ/เดือน < ผลรวมขั้นต่ำ ระบบจ่ายเกินงบเงียบ ๆ ไม่แจ้ง; ชน cap 600 เดือนแล้วไม่บอกว่า "หนี้จ่ายไม่หมด"
- **M11. การจัดสรรงบ:** `calculate_allocation` ปัดเป็นหลักร้อยบาท เศษงบหายเงียบ และกติกา Neutral 10% ใน prompt ไม่ตรงกับโค้ด (Neutral ได้เงินเฉพาะเมื่อไม่มีทั้ง Strong Buy และ Buy)
- **M12. ค่าธรรมเนียม Dime:** โหลด CSV แล้ว**คำนวณ fee ทับค่าที่บันทึกไว้ทุกครั้ง** (`tracker.py:79`) — ถ้ากติกาโบรกเกอร์เปลี่ยน ประวัติเก่าจะถูกเขียนทับความจริง; และ Return % ไม่หัก fee
- **M13. websocket loop บล็อก event loop:** `websocket.py:50-65` เรียก yfinance แบบ sync ใน async loop ทุก 30 วิ → API ทั้งตัวหน่วงเป็นช่วง ๆ (ควร `asyncio.to_thread`)
- **M14. Screener รายวันไม่รวม preset ฝั่งขาย:** `scheduler_job.py:9` รันแค่ 3 preset ฝั่งซื้อ — `overbought_warning` ไม่เคยรันอัตโนมัติ ทั้งที่ผู้ใช้ถือของจริงและต้องการคำเตือนขาแพงด้วย; แจ้งเตือนไป **Telegram** (env `TELEGRAM_BOT_TOKEN/CHAT_ID` — ไม่มีในตาราง env ของ CLAUDE.md) ขณะที่ระบบอื่นใช้ Discord — ถ้าไม่ได้ตั้ง Telegram = สัญญาณ screener หายเงียบ (log อย่างเดียว) และ HTTP 4xx จาก Telegram ก็ไม่ถูกเช็ค

---

## ระดับ LOW / สุขอนามัยโค้ด

- **L1.** เทสต์ 28 ตัว เกือบทั้งหมดอยู่ที่ emergency fund (24) — คณิตเงินหลัก (returns, risk, allocation, tracker, DCA, financial model) **ไม่มีเทสต์เลย**; `test_screener.py` ยิง network จริง; `test_pipeline.py` ไม่มี test function
- **L2.** CLAUDE.md คลาดเคลื่อน: ชื่อไฟล์ `utils/cache_utils.py`/`config_utils.py` ไม่มีจริง (คือ `utils/cache.py`/`config.py`), "11 route groups" ปัจจุบัน 19 ไฟล์, Redis ที่อ้างใน docker ไม่มีโค้ดใช้, Telegram env ไม่ได้บันทึก
- **L3.** Dependencies ตาย: `google-genai` (+ GOOGLE_API_KEY ใน workflow) ไม่มีโค้ดเรียก, Redis service ใน docker-compose ไม่ถูกใช้
- **L4.** `st.warning` ถูกเรียกในโมดูลที่ backend ใช้ (fetcher, macro, tracker) — นอก Streamlit runtime เป็นแค่ log แต่ผูก analysis layer กับ UI framework โดยไม่จำเป็น (และทำให้ backend ต้องลาก streamlit เป็น dependency)
- **L5.** `backend/routers/analysis.py` import `analysis/` ตรง ๆ — ผิด convention "Routers → Services" ของโปรเจกต์เอง
- **L6.** `datetime.utcnow()` (deprecated), `@app.on_event` (deprecated), `_cache` ใน ai router โตไม่จำกัด, `load_config()` อ่านไฟล์ทุกครั้งที่เรียก
- **L7.** slip OCR ใช้ `claude-opus-4-7` — งานอ่านสลิปใช้ `claude-haiku-4-5` ได้ผลใกล้เคียงที่ ~1/5 ของราคา และควรใช้ structured outputs (`output_config.format`) แทนการหวังว่า JSON จะ parse ได้; endpoint นี้ไม่มี auth (ดู H1)

---

## สิ่งที่ทำได้ดีอยู่แล้ว (ควรรักษาไว้)

- กลุ่ม service ใหม่ (`debt_service`, `emergency_fund_service`, `cashflow_service`) เป็นคณิตล้วน มี typed models และมีเทสต์ (emergency fund) — เป็นแบบอย่างที่ดีให้ส่วนที่เหลือ
- สูตร PMT/Monte Carlo ใน goal_service ถูกต้อง; ตรรกะ Avalanche/Snowball ถูกต้อง
- `analysis_service` ส่ง "ตัวเลขที่คำนวณแล้ว" ให้ AI อธิบาย (แนวที่ถูก) และบังคับ disclaimer ทุกคำตอบ
- slip OCR มี limit ขนาด/ชนิดไฟล์ และ schema ชัดเจน
- `run_backtest` (เวอร์ชัน portfolio/backtest.py) จัดการช่วงเวลา benchmark ถูกต้อง
- Logging ใน screener engine ละเอียดดี (จาก commit ล่าสุด)

---

## แผนการแก้ที่เสนอ (เรียงตามความคุ้ม)

### เฟส 1 — "หยุดเลือด" ก่อนใช้ตัดสินใจครั้งถัดไป (กระทบไฟล์ ~10 ไฟล์)
1. สถานะ NO-DATA ทั่วระบบ + ห้ามผลิตสัญญาณ/ราคา/คะแนนจากความล้มเหลว (C1)
2. Pin requirements.txt ทั้งหมด + ตัด pandas-ta → ใช้ `ta` เดียว + ตัด google-genai (C5)
3. ย้ายการคำนวณตัวเลขออกจาก AI: ต่อ `calculate_allocation` เข้า `get_monthly_advice`, คำนวณ alert levels ในโค้ด, validate เอาต์พุต AI, dashboard เลิก parse ข้อความ (C3, H5, M8)
4. โมดูล signal กลางหนึ่งเดียว — นิยาม RSI/MA/เกณฑ์เดียวกันทุกหน้า (C2, M1)
5. แก้ DCF: ตัด GLDM ออก + ติดป้าย placeholder (C4)
6. ต่อ `check_alerts()` เข้า GH Actions รายวัน + แก้ "0 items" hardcode (C6)
7. หมุน Discord webhook + เอาไฟล์ข้อมูลออกจาก git (H1 ส่วนเร่งด่วน)

### เฟส 2 — ความถูกต้อง
แก้ H2 (เลือก storage เดียว), H3 (cache จริง), H4 (การ์ด Overview), H6 (Groq truncation/JSON mode), H7 (macro/CPI), H8 (ORM serialization), M2-M5, M7, M13, M14, กู้ป้ายไทย M6

### เฟส 3 — ความยั่งยืน
เทสต์คณิตเงินด้วย fixture (ไม่ยิง network), auth บน backend, เอกสาร CLAUDE.md ให้ตรงจริง, backtest honesty (แจ้ง strategy ที่ใช้จริง + walk-forward), ปรับการสื่อสาร forecast (เลิกใช้คำว่า accuracy), รวม FX แหล่งเดียว

### ทางเลือกเรื่องโมเดล AI (ไม่บังคับ ไม่มีค่าใช้จ่ายเพิ่มถ้าไม่เลือก)
- ค่าเริ่มต้นของแผนนี้: **อยู่กับ Groq ฟรี** — เมื่อทำเฟส 1 ข้อ 3 แล้ว คุณภาพจะดีขึ้นมากเพราะ AI เหลือหน้าที่แค่อธิบาย
- ถ้าภายหลังยอมจ่าย: ปริมาณการเรียกระดับนี้ (วันละ 1-2 ครั้ง) ใช้ Claude Haiku 4.5 ($1/$5 ต่อล้านโทเคน) ตกเดือนละไม่ถึง 10 บาท / Sonnet 5 ราว 20-30 บาท — หมายเหตุ: Claude API คิดเงินแยกจาก plan Claude Code (Pro/Max) เสมอ; โปรเจกต์นี้มี `ANTHROPIC_API_KEY` ใช้กับ slip OCR อยู่แล้ว (ซึ่งจ่ายตามจริงอยู่แล้วเช่นกัน และควรลดรุ่นจาก Opus เป็น Haiku — ดู L7)

---

---

# บันทึกการแก้ไข — เฟส 1 (12 กรกฎาคม 2026)

**สถานะ: เสร็จทั้ง 7 ข้อ + แก้บั๊กเพิ่มที่เจอตอนทดสอบจริงอีก 3 ตัว**
ทดสอบแล้ว: เทสต์ใหม่ 50 ตัว (รวมทั้งชุด 74 ผ่าน), ทุกหน้า dashboard render ผ่าน, backend ตอบ 200,
ตรวจกับข้อมูลตลาดจริง

## แก้ตามแผน (C1-C6 + H1)

| ข้อ | แก้อะไร | ไฟล์หลัก |
|---|---|---|
| **C1** สัญญาณปลอมจากข้อมูลพัง | `data/fetcher.py` raise `PriceDataUnavailableError` แทนคืนค่าว่าง; คะแนน/พอร์ต/screener/websocket มีสถานะ `data_ok` / `Price OK` / `NO DATA`; ราคาที่ดึงไม่ได้เป็น NaN ไม่ใช่ 0 | fetcher, financial_model, tracker, technical_service, screener/engine, websocket, price_alert |
| **C2** สัญญาณขัดกันเอง | **สร้าง `technical/signal_rules.py`** เป็นนิยามเดียว (oversold ในขาขึ้น = สะสม ไม่ใช่ขาย) + **รวมระบบคะแนนเป็นสูตรเดียว** `score_from_prices()` (trend 40 / timing 30 / momentum 20 / dividend 10) | signal_rules, financial_model, technical_service, analysis_service, etf_service, dashboard |
| **C3** AI คิดเลขเอง | ระดับราคา alert คำนวณจากกฎ deterministic (`_suggest_alert_levels`, ไม่เรียก LLM แล้ว); แผนจัดสรรงบมาจาก `calculate_allocation()` และส่งให้ AI แค่ "อธิบาย"; **ลบ regex ที่แกะตัวเลขจากข้อความ AI ออกจาก dashboard และ PDF** | ai_advisor, dashboard, pdf_export |
| **C4** DCF ปลอมของ GLDM | `dcf_valuation()` ปฏิเสธสินทรัพย์ที่ไม่มี P/E (ไม่ fallback PE=20 อีก) และ **ตัด DCF ออกจากคะแนนทั้งหมด** — เหลือเป็นข้อมูลประกอบพร้อมป้ายกำกับว่าเป็น earnings-yield proxy; ลบ `_DCF_SCORE_PLACEHOLDER = 15` | financial_model, dashboard |
| **C5** dependencies ลอย | pin ทุกตัวใน `requirements.txt`; **ถอด `pandas-ta`** (พังกับ numpy 2) → ใช้ `analysis/ta_compat.py` ที่เพิ่ม macd/bbands; ถอด `google-genai` (ไม่มีโค้ดใช้) | requirements.txt, ta_compat + ทุกที่ที่เคย import pandas_ta |
| **C6** alert ไม่มีใครตรวจ | GH Actions มี step `Check Price Alerts` เรียก `check_alerts()` จริง + commit สถานะ triggered กลับ repo; `main.py --job price_alert` เรียกตัวจริงแล้ว; ข้อความ Discord นับ alert จากของจริง (เลิก hardcode "0 items") | scheduler.yml, main.py, jobs/daily_check.py |
| **H1** secrets หลุด | webhook ออกจาก `config.json` และ **`.env.example`** (มีของจริงฝังอยู่!); `save_config()` ไม่เขียน webhook ลงดิสก์อีก; `load_config()` อ่านจาก env; `vaultis.db` + `transactions.csv` ออกจาก git + เข้า .gitignore | config.py, config.json, .env.example, .gitignore |

## บั๊กเพิ่มที่เจอตอนทดสอบจริง (ไม่ได้อยู่ในรายงานเดิม)

1. **การจัดสรรเงินกลับหัว** — การแบ่งงบเป็นก้อนต่อกลุ่ม (60%/30%) ทำให้เมื่อมี Strong Buy 4 ตัวกับ Buy 1 ตัว → **GLDM คะแนนต่ำสุด (42.9) ได้ 1,500 บาท ส่วน VOO คะแนนสูงสุด (100) ได้แค่ 800 บาท**
   → แก้เป็นน้ำหนัก = คะแนน × ตัวคูณกลุ่ม แล้วแบ่งตามสัดส่วน รับประกันว่าคะแนนสูงกว่าได้เงินมากกว่าเสมอ (มีเทสต์คุม)
2. **VOO ได้คะแนนขัดกันเอง 100% vs 47.3% ในวันเดียวกัน** — เพราะมี 2 สูตรคะแนนที่ปรัชญาตรงข้าม (ตัวหนึ่ง RSI ต่ำ = ดี, อีกตัว RSI กลาง = ดี) → รวมเป็นสูตรเดียว ตอนนี้ทั้ง 2 entry point ให้ 72.0% ตรงกัน
3. **FX rate ไม่เคยดึงได้จริงเลย** — `df.get("Close")` คืน DataFrame เพราะ yfinance ใช้ MultiIndex → `pd.to_numeric` พัง → ตกไปใช้ default **33.5 ตลอดมา** (ของจริง 33.24) ทุกตัวเลข THB จึงเพี้ยน; เดิมถูกซ่อนด้วย `st.warning` กว้าง ๆ → แก้ `_close_series_from()` + sanity check ช่วง 20-50

## เรื่องที่ต้องตัดสินใจก่อนเฟส 2 (ผลข้างเคียงจากการรวมคะแนน)

**การจัดสรรงบตอนนี้ไม่สนใจสัดส่วนพอร์ตเป้าหมาย** — ใช้คะแนนล้วน ๆ ผลคือเดือนนี้ GLDM (ทองคำ, ต่ำกว่า MA200 → คะแนน 20%) **ไม่ได้รับเงินเลย** ทั้งที่เป็นตัวกระจายความเสี่ยงของพอร์ต
นี่คือกลยุทธ์ "DCA ตามเทรนด์" ซึ่งเป็นการตัดสินใจเชิงนโยบาย ไม่ใช่บั๊ก — ทางเลือก:
- (ก) คงไว้: ไม่ซื้อสินทรัพย์ที่อยู่ในขาลง
- (ข) ใช้สัดส่วนเป้าหมายเป็นฐาน แล้วให้คะแนนเป็นตัว tilt (เช่น ±30% รอบเป้าหมาย) — เหมาะกับ DCA ระยะยาวแบบคลาสสิกกว่า
- (ค) กำหนดพื้นขั้นต่ำต่อสินทรัพย์ (เช่น อย่างน้อย 5% ของงบ)

---

# บันทึกการแก้ไข — เฟส 2 (12 กรกฎาคม 2026)

**สถานะ: เสร็จ** — เทสต์รวม 93 ตัวผ่าน, ทุกหน้า dashboard render ผ่าน, ทุก endpoint ตอบ 200

## บั๊กเงินจริงที่ยังเหลือจากเฟส 1 (แก้แล้ว)

**FX ยังพังอีก 2 จุดที่เฟส 1 ไม่ได้แตะ** — สร้าง `utils/fx.py` เป็นแหล่งเดียว (ดึงสด → sanity check 20-50 → cache 1 ชม. → fallback พร้อมธง `is_live`)

| จุด | เดิม | ผลกระทบ |
|---|---|---|
| `networth_service` | ใช้ `default_fx_rate` 33.5 จาก config **คงที่ ไม่เคยดึงสด** | Net Worth คิดด้วยเรตคนละตัวกับหน้า Portfolio |
| `rebalance_service` | `USDTHB=X` + fallback **35.0** | แผน rebalance ใช้เรตต่างจากทุกที่ |

## แก้ตามแผน (H2, H3, H7, H8, M3-M14)

| ข้อ | แก้อะไร |
|---|---|
| **H8** endpoint พัง | `POST /api/portfolio/add` **ไม่เคยทำงานเลย** (`Transaction() got multiple values for 'ticker'` → ตาราง 0 แถวเสมอ); `POST /api/alerts` พังตอน serialize ORM → ตอนนี้ทุก endpoint คืน dict และตอบ 200 |
| **H2** ข้อมูลซ้ำ 2 ระบบ | ถอด ORM `Transaction`/`PriceAlert` ทิ้ง (ชุดที่ไม่มีใครใช้ได้จริง) → **ledger เดียว** = CSV (`portfolio/tracker.py`), **alert store เดียว** = JSON (`alerts/price_alert.py`); backend delegate ไปที่เดียวกับ dashboard; เพิ่ม `tx_id` (uuid) รองรับการลบผ่าน API |
| **H7** macro ผิด | CPI เดิมใช้ `CPIAUCSL` (FRED ID) เป็น Yahoo ticker → **404 ตลอด** คอลัมน์ว่างเสมอ; ค่า "เงินเฟ้อ" ที่รายงานคือ **ระดับดัชนี (333.98)** ไม่ใช่อัตรา → ตอนนี้ดึงจาก FRED และแปลงเป็น **YoY % จริง (4.27%)**; "Fed Rate" เดิมใช้ `^IRX` (T-bill) ติดป้ายผิด → ใช้ FEDFUNDS จริง |
| **H3** ไม่มี cache | เพิ่ม `TTLCache` ใน backend (ราคา 10 ปี cache 1 ชม., ราคาล่าสุด 5 นาที) — ลดการยิง yfinance ที่ทำให้โดน rate limit จนเกิดสัญญาณปลอม; dashboard ใช้ `@st.cache_data` |
| **M4** backtest | ETF ที่ยังไม่เกิด (QQQM ก่อน ต.ค. 2020) เดิมถูกนับผลตอบแทน **0%** ทั้งที่ถือน้ำหนักอยู่ → ฉุดผลย้อนหลังต่ำกว่าจริง; Sharpe ใช้ rf=0% ขณะหน้า Risk ใช้ 2% → **เทียบกันไม่ได้** → รวมเป็น `DEFAULT_RISK_FREE_RATE` เดียว + บอกผู้ใช้ว่าโมเดล rebalance รายวันและไม่คิดค่าธรรมเนียม |
| **M3** forecast | ถอด `accuracy_pct = 100 - MAPE` ออก — MAPE 3% กลายเป็น **"แม่นยำ 97%"** ซึ่งหลอกคนใช้เงินจริง (พยากรณ์ naive ก็ได้ MAPE ต่ำ) |
| **M7** scheduler | ตั้ง DCA วันที่ 31 → เดือน ก.พ./เม.ย./มิ.ย./ก.ย./พ.ย. **ไม่เคยเตือนเลย** → ใช้วันสุดท้ายของเดือนแทน; เวลาทั้งหมดผูก `Asia/Bangkok` (เดิมใช้เวลาเครื่อง → บนเซิร์ฟเวอร์ UTC งาน 08:00 ยิงตอน 15:00 ไทย) |
| **M9** goals | คำเตือน "ผลตอบแทนที่ต้องการสูงเกินไป" **ไม่มีวันทำงาน** (เทียบ expected_return กับตัวเอง) → เพิ่ม `required_annual_return()` คำนวณจริง; แยก `weights` ออกจาก `warning` (เดิมยัด string ปนใน dict ตัวเลข) |
| **M10** debt | งบต่ำกว่ายอดขั้นต่ำรวม → เดิม "จ่าย" เกินงบเงียบ ๆ แล้วรายงานว่าหนี้หมด; ชนเพดาน 50 ปีแล้วยังมีหนี้ก็ไม่บอก → ตอนนี้ raise พร้อมเหตุผล (HTTP 422) |
| **M12** ค่าธรรมเนียม | เดิมคำนวณทับค่าที่บันทึกไว้ทุกครั้งที่โหลด → ประวัติจริงถูกเขียนทับด้วยสูตรปัจจุบัน |
| **M14** screener | ไม่เช็ค HTTP status ของ Telegram และถ้าไม่ได้ตั้ง Telegram สัญญาณ**หายเงียบ** → เพิ่ม fallback ไป Discord |
| **M6** ป้ายไทย | กู้ 138 บรรทัด (อักษรไทย 119 → 4,596 ตัว) เขียนจากบริบทโค้ดจริง (การจับคู่อัตโนมัติกับ git history แม็ปผิด — ป้ายผิดอันตรายกว่าป้ายว่างในแอปการเงิน) |

## บั๊กเพิ่มที่เทสต์จับได้ระหว่างทำเฟส 2

**การเพิ่มธุรกรรมแรกสุดลงสมุดที่ว่างเปล่าจะ crash** — `_load_transactions()` คืน DataFrame ว่างที่คอลัมน์ `date` เป็น object → `.dt` accessor พัง ผู้ใช้ไม่เคยเจอเพราะมี 1 รายการอยู่แล้ว แต่ถ้าลบหมดหรือติดตั้งใหม่จะพังทันที

## ยังไม่ได้แก้ (เฟส 3)

- **auth บน backend** (ยัง CORS `*` และไม่มี API key — แต่ตอนนี้ Render ephemeral disk ทำให้ ledger ไม่ได้อยู่บนนั้นแล้ว)
- **CI ไม่เห็นพอร์ต** — ledger เป็น CSV ที่ gitignored (จำเป็น เพราะ repo public) → AI advisor รายเดือนใน GH Actions จะไม่มีบริบทพอร์ต ทางแก้: ใช้ Postgres (ตอนนี้ Supabase เชื่อมต่อไม่ได้ — โปรเจกต์น่าจะถูก pause)
- M2 (backtest optimize เป็น in-sample), M5 ที่เหลือ, M11, M13 (แก้แล้วบางส่วน), เทสต์เพิ่มเติม, `datetime.utcnow()` deprecated

## เรื่องที่ยังต้องตัดสินใจ (ยกมาจากเฟส 1)

**นโยบายจัดสรรงบ DCA** — ตอนนี้ใช้คะแนนล้วน ทำให้ GLDM (ทองคำ, ต่ำกว่า MA200 → คะแนน 20%) **ไม่ได้รับเงินเลย** เป็นกลยุทธ์ "DCA ตามเทรนด์" ที่สอดคล้องในตัวเอง แต่ขัดกับหลัก DCA แบบคลาสสิกที่ซื้อทุกเดือนไม่ว่าตลาดเป็นอย่างไร ทางเลือก: (ก) คงไว้ (ข) ใช้สัดส่วนเป้าหมายเป็นฐานแล้วให้คะแนนเป็นตัวปรับน้ำหนัก (ค) กำหนดพื้นขั้นต่ำต่อสินทรัพย์

---

*รายงานตรวจสร้างจากการอ่านโค้ด ณ commit `5a33408`; การแก้เฟส 1-2 ทดสอบด้วย venv Python 3.12 + dependencies ที่ pin แล้ว + ข้อมูลตลาดจริง*
