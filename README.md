# Vaultis — สรุปโปรเจกต์แบบละเอียด

**Vaultis** เป็นแพลตฟอร์มวิเคราะห์พอร์ต ETF ระยะยาว (สไตล์ DCA / buy-and-hold) เน้นกลุ่มหลักเช่น VOO, SCHD, QQQM, XLV, GLDM ประกอบด้วย:

- **แดชบอร์ด Streamlit** — ดูภาพรวม วิเคราะห์ แบ็กเทสต์ แจ้งเตือน และตั้งค่า
- **Backend FastAPI** — REST API + WebSocket สำหรับข้อมูลราคา การวิเคราะห์ พอร์ต และสกรีนเนอร์
- **สคริปต์และงานตามเวลา** — แจ้งเตือน Discord, สรุปรายสัปดาห์, AI Advisor รายเดือน, เช็กราคา, สกรีนเนอร์รายวัน
- **โมดูลวิเคราะห์** — ผลตอบแทน ความเสี่ยง สหสัมพันธ์ DCF โมเดล มาโคร เซนติเมนต์ และพยากรณ์

เอกสารนี้สรุปโครงสร้าง การทำงานของแต่ละส่วน และคำอธิบายไฟล์ใน repo ตามที่มีในโปรเจกต์

---

## เทคโนโลยีหลัก

| หมวด | รายการ |
|------|--------|
| ภาษา / runtime | Python 3.11+ |
| Web API | FastAPI, Uvicorn, Pydantic |
| DB (แอปหลัก) | SQLite (`vaultis.db`) ผ่าน SQLAlchemy |
| DB (เซนติเมนต์) | PostgreSQL เมื่อตั้ง `DATABASE_URL` |
| Cache (ดีพลอย) | Redis ใน `docker-compose.yml` |
| ข้อมูลราคา | yfinance |
| เทคนิคอล | pandas-ta, ta |
| แดชบอร์ด | Streamlit, Plotly |
| AI | Groq API, Google GenAI (ตามการใช้งานในโมดูล) |
| มาโคร | FRED API (`fredapi`) |
| งานตามเวลา | APScheduler (ใน backend), `schedule` (ใน `main.py`), GitHub Actions |
| ทดสอบ | `pytest` (โฟลเดอร์ `tests/`) |

---

## โครงสร้างโฟลเดอร์ระดับบน

```
Vaultis/
├── backend/          # FastAPI แอปหลัก + สกรีนเนอร์ + เซอร์วิส
├── dashboard/        # Streamlit UI
├── analysis/         # ตรรกะวิเคราะห์ แบ็กเทสต์ AI มาโคร เซนติเมนต์
├── portfolio/        # DCA, แบ็กเทสต์พอร์ต, ติดตามธุรกรรม
├── alerts/         # แจ้งเตือนราคา + ส่ง Discord
├── data/           # ดึงข้อมูลราคา (yfinance)
├── technical/      # อินดิเคเตอร์เทคนิค (เช่น RSI)
├── utils/          # config, cache, PDF
├── db/             # โมเดล PostgreSQL สำหรับเซนติเมนต์
├── jobs/           # งานรายวัน (เช่น daily check → Discord)
├── scripts/        # สคริปต์ช่วย (init DB เซนติเมนต์)
├── tests/          # ยูนิตเทสต์
├── main.py         # Scheduler แจ้งเตือน (Discord, DCA, weekly, RSI, price)
├── requirements.txt
├── config.json     # การตั้งค่าแอป (DCA, ticker, การแจ้งเตือน, แสดงผล)
├── Dockerfile / docker-compose.yml
├── render.yaml / Procfile  # ดีพลอย backend
└── .env.example    # ตัวอย่างตัวแปรสภาพแวดล้อม (ห้าม commit คีย์จริง)
```

---

## การรันระบบ (สรุป)

1. **ติดตั้ง dependencies:** `pip install -r requirements.txt`
2. **ตั้งค่า:** คัดลอก `.env.example` เป็น `.env` แล้วกรอกคีย์ที่จำเป็น (ไม่ควร commit `.env`)
3. **Backend:**  
   `uvicorn backend.main:app --reload`  
   หรือใช้ `run.sh` / Docker ตาม `docker-compose.yml`
4. **แดชบอร์ด Streamlit:**  
   `streamlit run dashboard/app.py` (จาก root ของ repo)
5. **Scheduler แบบ long-running (เครื่อง local):**  
   `python main.py` หรือ `python main.py --job all`  
   งานเดี่ยว: `--job weekly_summary`, `monthly_advice`, `price_alert`

**หมายเหตุ:** `backend/main.py` ลงทะเบียน APScheduler ให้รัน `run_daily_screener` ทุกวัน 07:00 เวลา `Asia/Bangkok`

---

## Backend FastAPI (`backend/`)

### `backend/main.py`

- สร้างตาราง SQLite จาก `Base.metadata.create_all`
- เปิด CORS แบบเปิดกว้าง (`allow_origins=["*"]`) — เหมาะสำหรับพัฒนา; โปรดจำกัดใน production
- รวม router ทั้งหมด: ETF, backtest, forecast, etf_analysis, portfolio, analysis, alerts, ai, sentiment, screener, websocket
- ตั้ง `AsyncIOScheduler` สำหรับสกรีนเนอร์รายวัน

### `backend/database.py`

- `DATABASE_URL = sqlite:///./vaultis.db`
- `engine`, `SessionLocal`, `get_db()` สำหรับ dependency injection

### `backend/schemas.py`

- Pydantic models: ธุรกรรม, price alert, คำขอ backtest/DCA, AI advice, generic/sentiment response

### `backend/models/`

- **`orm.py`** — SQLAlchemy ORM: `Transaction`, `PriceAlert`, `Config`
- **`etf_models.py`**, **`backtest_models.py`** — โมเดลข้อมูลที่ใช้กับ API/บริการ (รายละเอียดตามโค้ดในไฟล์)

### `backend/routers/` (เส้นทาง API หลัก)

| ไฟล์ | บทบาทโดยย่อ |
|------|----------------|
| `etf.py` | `/api/etf/*` — ราคา, snapshot รายวัน, ผลตอบแทน, ความเสี่ยง, correlation, technical |
| `analysis.py` | `/api/backtest`, `/api/dca/simulate`, `/api/macro`, DCF, `/api/analysis/full` |
| `portfolio.py` | `/api/portfolio` — สรุป, holdings, history, เพิ่ม/ลบธุรกรรม |
| `alerts.py` | `/api/alerts` — CRUD price alerts + `/check` |
| `ai.py` | `/api/ai/advice`, history, suggest-alerts |
| `sentiment.py` | `/api/sentiment/{symbol}` |
| `screener.py` | `/api/screener/run`, presets, custom |
| `forecast.py` | `/api/forecast/{symbol}` |
| `backtest.py` | `/api/backtest` (response model เฉพาะ) |
| `etf_analysis.py` | `/api/etf/compare`, `/api/etf/{symbol}` |
| `websocket.py` | WebSocket `/ws/prices` |
| `__init__.py` | แพ็กเกจ router |

### `backend/services/`

ชั้นบริการที่เรียกจาก router: `etf_service`, `etf_info_service`, `analysis_service`, `technical_service`, `portfolio_service`, `alert_service`, `cache_service` — แยกตรรกะออกจาก HTTP layer

### `backend/screener/` (สกรีนเนอร์เทคนิค)

| ไฟล์ | บทบาท |
|------|--------|
| `engine.py` | `ScreenerEngine` — ดึง OHLCV, ประเมินกฎ (RSI, MACD cross, MA200, golden/death cross, BB squeeze, volume spike, price drop %) |
| `crossover_detector.py` | ตรวจจับ crossover ต่างๆ |
| `presets.py` | พรีเซ็ตกฎสำเร็จรูป |
| `models.py` | โครงสร้าง `ScreenerPreset`, `ScreenerRule`, `ScreenerResult` |
| `history_service.py` | บันทึกผลการสแกน |
| `notifier.py` | สรุปและแจ้ง Telegram |
| `scheduler_job.py` | `run_daily_screener` — รันหลายพรีเซ็ตกับสัญลักษณ์คงที่ แล้วแจ้งถ้ามีสัญญาณ |

---

## แดชบอร์ด (`dashboard/app.py`)

- แอป Streamlit ขนาดใหญ่: นำทางหลายหน้า (Overview, Portfolio, Backtest, DCA Simulator, Technical, Correlation, DCF, AI Advisor, Macro, Price Alerts, Settings)
- เรียกโมดูลใน `analysis/`, `portfolio/`, `alerts/`, `data/`, `utils/`
- ใช้ `BACKEND_URL` จาก environment (ค่าเริ่มต้นชี้ไปที่ deploy บน Render ตามโค้ด)
- ธีมสีมืดกำหนดใน `THEME`, กลุ่มเมนูใน `NAV_GROUPS`

---

## การวิเคราะห์ (`analysis/`)

| ไฟล์ | บทบาทโดยย่อ |
|------|----------------|
| `returns.py` | คำนวณผลตอบแทนตามช่วงเวลา |
| `risk.py` | เมตริกความเสี่ยง |
| `correlation.py` | เมทริกซ์ความสัมพันธ์ |
| `macro.py` | สแนปชอตมาโคร (เชื่อม FRED ฯลฯ) |
| `financial_model.py` | คะแนน ETF, DCF, การจัดสรร — ใช้ร่วมกับ AI Advisor |
| `ai_advisor.py` | คำแนะนำผ่าน Groq ตาม prompt ระบบ Vaultis + ส่ง Discord ได้ |
| `forecast_chart.py`, `forecaster.py` | พยากรณ์/กราฟ (เช่น Prophet ตาม dependency) |
| `backtester.py`, `backtest_engine.py`, `backtest_summary.py` | กรอบแบ็กเทสต์และสรุปผล |
| `ta_compat.py` | ความเข้ากันได้กับ pandas-ta |
| `news_fetcher.py` | ดึงข่าว |
| `sentiment_analyzer.py` | วิเคราะห์เซนติเมนต์ (งาน batch) |
| `sentiment_aggregator.py`, `sentiment_prompt.py` | รวมผลและ prompt |
| `ai_advisor.py` | (ซ้ำชื่อโฟลเดอร์) หลักสำหรับคำแนะนำ AI |

---

## พอร์ต (`portfolio/`)

| ไฟล์ | บทบาท |
|------|--------|
| `tracker.py` | ธุรกรรม, สรุปพอร์ต, อัตราแลกเปลี่ยน THB, ค่าธรรมเนียมประมาณการ |
| `dca.py` | จำลอง DCA รายเดือน |
| `backtest.py` | แบ็กเทสต์พอร์ตแบบรวม |
| `rebalance.py` | ตรรกะ rebalance |
| `data/transactions.csv` | ข้อมูลตัวอย่าง/สำรองธุรกรรม (ถ้ามีการใช้) |

---

## แจ้งเตือน (`alerts/`)

| ไฟล์ | บทบาท |
|------|--------|
| `notifier.py` | ส่ง Discord webhook (รายงาน, technical alert, DCA reminder ฯลฯ) |
| `price_alert.py` | เก็บ/เช็ก alert ราคา (ทำงานร่วมกับ backend หรือไฟล์ JSON) |
| `data/price_alerts.json` | ที่เก็บ alert แบบไฟล์ (ตามการใช้งานจริง) |

---

## ข้อมูลและเทคนิค

| ไฟล์ | บทบาท |
|------|--------|
| `data/fetcher.py` | ดึง Adj Close หลาย ticker จาก yfinance (มี retry และใช้ `st.warning` เมื่อรันผ่าน Streamlit) |
| `technical/indicators.py` | อินดิเคเตอร์ เช่น RSI |

---

## Utils (`utils/`)

| ไฟล์ | บทบาท |
|------|--------|
| `config.py` | โหลด/บันทึก `config.json`, merge ค่าเริ่มต้น, จัดการรายการ ticker |
| `cache.py` | แคชทั่วไป |
| `pdf_export.py` | ส่งออกรายงาน PDF (reportlab) |

---

## ฐานข้อมูลเซนติเมนต์ (`db/`)

| ไฟล์ | บทบาท |
|------|--------|
| `sentiment_models.py` | PostgreSQL engine จาก `DATABASE_URL`, ตาราง `sentiment_results`, `sentiment_summary` |
| `__init__.py` | แพ็กเกจ |
| `scripts/init_db.py` | เรียก `create_tables()` สำหรับตารางเซนติเมนต์ |

แยกจาก SQLite หลักของแอป: ใช้เมื่อต้องการเก็บประวัติเซนติเมนต์บน Postgres (เช่นใน CI ที่มี `DATABASE_URL`)

---

## งานและสคริปต์

| ไฟล์ | บทบาท |
|------|--------|
| `jobs/daily_check.py` | ดึง snapshot/ราคาจาก backend, ประกอบข้อความ Discord รายวัน + embed AI Advisor |
| `main.py` | Scheduler ด้วย `schedule`: AI เดือนละครั้ง (วันที่ 1), DCA reminder, สรุปสัปดาห์, RSI alert, price alert |
| `test_dcf.py` | สคริปต์ทดลองเรียก `run_full_analysis` และพิมพ์ DCF/การจัดสรร |
| `run.sh` | รัน uvicorn reload พอร์ต 8000 |

---

## การทดสอบ (`tests/`)

- `test_backtest.py`, `test_forecast.py`, `test_etf_analysis.py`, `test_pipeline.py`, `test_screener.py` — ครอบคลุม pipeline หลักของโปรเจกต์

รันโดยทั่วไป: `pytest` จาก root (ตรวจสอบว่า environment พร้อมสำหรับเทสต์ที่เรียก API ภายนอก)

---

## การตั้งค่าและความปลอดภัย

### `config.json`

- `dca`: งบรายเดือน (THB), วันที่ DCA ของเดือน
- `etf.tickers`: รายการสัญลักษณ์
- `notifications`: URL Discord, เปิด/ปิด weekly summary, DCA reminder, RSI alert
- `display`: หน้าเริ่มต้น, สกุลเงิน, อัตรา FX เริ่มต้น

**คำแนะนำ:** อย่า commit URL webhook หรือข้อมูลลับลง Git — ใช้ตัวแปรสภาพแวดล้อมหรือ secrets ของ CI แทน

### `.env` / `.env.example`

ตัวแปรที่เกี่ยวข้องกับโปรเจกต์รวมถึง: `DISCORD_WEBHOOK_URL`, `GOOGLE_API_KEY`, `FRED_API_KEY`, `GROQ_API_KEY`, `DCA_*`, `BACKEND_URL`, `DATABASE_URL`, คีย์ Reddit/NewsAPI ตาม workflow เซนติเมนต์

---

## CI/CD (`.github/workflows/scheduler.yml`)

- รันตาม cron: เซนติเมนต์ + สรุปรายสัปดาห์ (วันจันทร์), AI Advisor (วันที่ 1), `jobs/daily_check.py` วันทำการ
- ต้องตั้ง GitHub Secrets ที่ workflow อ้างถึง

---

## Deploy

- **`render.yaml` / `Procfile`:** รัน `uvicorn backend.main:app` พร้อมตัวแปรสภาพแวดล้อมสำหรับคีย์ API
- **`Dockerfile`:** ภาพ Python 3.11-slim, ติดตั้ง `requirements.txt`, CMD uvicorn
- **`docker-compose.yml`:** Redis + backend พร้อม `REDIS_URL` (บริการบางส่วนอาจอ่าน Redis ผ่าน `cache_service`)

---

## ไฟล์อื่นใน repo

| ไฟล์ | คำอธิบาย |
|------|-----------|
| `.gitignore` | ไม่ติดตาม `.env`, `__pycache__`, `.claude/` ฯลฯ |
| `cursorrules` | คำแนะนำสำหรับ AI/นักพัฒนาใน Cursor (สไตล์โค้ด, stack) |
| `vaultis.db` | SQLite ที่สร้างเมื่อรัน backend (ไม่ควรแชร์ข้อมูลส่วนตัว) |
| `.claude.json`, `.claude/settings.local.json` | การตั้งค่าเครื่องมือ Claude (ถ้ามีในเครื่อง — มักไม่จำเป็นสำหรับผู้ใช้ทั่วไป) |

---

## สรุปการไหลของข้อมูล

1. **ราคา/ย้อนหลัง:** yfinance → `data/fetcher.py` / บริการ backend → API หรือ Streamlit  
2. **พอร์ต:** ธุรกรรมใน SQLite → `portfolio_service` / แดชบอร์ด  
3. **คำแนะนำ:** โมเดลการเงิน + Groq → `ai_advisor` → Discord หรือ API  
4. **แจ้งเตือน:** `main.py` / GitHub Actions / APScheduler screener → Discord หรือ Telegram (สกรีนเนอร์)

---

## ข้อจำกัดและข้อควรทราบ

- ข้อมูลราคาพึ่งพาแหล่งภายนอก (yfinance) — อาจมีความล่าช้าหรือข้อจำกัดการใช้งาน  
- คำแนะนำ AI ไม่ใช่คำแนะนำการลงทุนที่ได้รับอนุญาต — ใช้เพื่อการศึกษาและตัดสินใจด้วยตนเอง  
- CORS แบบเปิดและ webhook ใน config ควรถูกจัดการใหม่ก่อน production

---

*เอกสารนี้สร้างจากโครงสร้างและโค้ดใน repo ณ เวลาที่จัดทำ — หากเพิ่มไฟล์หรือเปลี่ยนเส้นทาง API ควรอัปเดต README นี้ให้สอดคล้อง*
