# แผนแก้ไข Vaultis — จากผลตรวจ 10 รอบ (2026-08-02)

สถานะ: **ยังไม่ลงมือ** — เอกสารนี้คือแผน ไม่ใช่บันทึกสิ่งที่ทำแล้ว
ที่มา: การตรวจ 51 agent / 10 รอบ ทุกข้อบังคับให้รันโค้ดจริง + ตรวจซ้ำแบบพยายามหักล้าง
ผลคัดกรอง: พบ 58 ข้อ → ยืนยัน 20 → หักล้างทิ้ง 19 → ยังไม่ยืนยัน 19 → พบเพิ่มจาก completeness critic 6

---

## 0. ข้อมูลที่ต้องรู้ก่อนลงมือ

**เครื่อง host ไม่มี venv และไม่มี dependency ของโปรเจกต์** — รันไพธอนได้เฉพาะในคอนเทนเนอร์

**โค้ดถูก bake เข้า image** (bind mount มีแค่ `portfolio/data`, `alerts/data`, `.docker-data`, `config.json`)
→ ไฟล์ที่แก้บน host จะ **ไม่** ปรากฏในคอนเทนเนอร์ เว้นแต่ rebuild หรือ mount ทับ

คำสั่งที่ยืนยันแล้วว่าใช้ได้ (เห็นโค้ดที่แก้บน host ทันที ไม่ต้อง rebuild):

```bash
# รันเทสต์เฉพาะไฟล์
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app tests pytest -q tests/test_xxx.py
# รันสคริปต์ตรวจเลข
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app tests python - <<'PY'
...
PY
```

**เส้นฐานขึ้นกับว่า mount หรือไม่ (วัดจริง 2026-08-05):**

| คำสั่ง | ผล |
|---|---|
| ไม่ mount (`docker compose --profile dev run --rm tests`) | 348 passed |
| **mount ทับ** (คำสั่งข้างบน — ตัวที่ต้องใช้) | **347 passed, 1 failed** |

ตัวที่แดงคือ `tests/test_llm_cost_guard.py::TestSingleProvider::test_missing_key_fails_loudly` — **ไม่ใช่ regression** แต่เป็นบั๊กจริงที่โผล่มาเพราะการ mount (ดูข้อ 1.8) ดังนั้น**เกณฑ์ผ่านของเฟส 1–4 คือ `347 passed + 1 failed ตัวนี้` หรือดีกว่า** และเมื่อแก้ข้อ 1.8 เสร็จต้องกลับเป็น **348 passed แม้ตอน mount**

ตอนจบต้อง `docker compose build` + `up -d` เพื่อให้บริการที่รันอยู่ได้โค้ดใหม่ — **ยืนยันแล้วว่าคอนเทนเนอร์ที่รันอยู่ตอนนี้เก่ากว่า HEAD** (image 16:06:31 < commit 9861db8 ที่ 16:08:36 → ยังไม่มี Google News RSS)

**ledger จริงว่างเปล่า** (`portfolio/data/transactions.csv` มีแต่ header) — ทุกการตรวจที่ผูกกับพอร์ตต้องใช้สมุดสังเคราะห์ **ห้ามเขียนทับไฟล์จริง**

---

## เฟส 1 — แก้กฎ fail-loud ที่ถูกละเมิด (ไฟล์ไม่ทับกัน ทำขนานได้)

### 1.1 `analysis/llm.py:123` — คำตอบว่างเปล่าถูกส่งออกเป็นผลสำเร็จ 🔴

**อาการ** `return text + _TRUNCATION_NOTE` ทำให้ด่าน `if not text: raise` (บรรทัด 158) ยิงไม่ได้เลย เพราะ `"" + note` เป็น truthy เสมอ → โมเดลใช้โควตาหมดกับ thinking แล้วคืนหมายเหตุเปล่า ๆ จ่ายเงิน 2 รอบได้ศูนย์

**แก้** ใน `_chat_anthropic` ก่อนต่อหมายเหตุ:
```python
if attempt == 0:
    budget = max_tokens * 2
    continue
if not text:
    raise RuntimeError(
        f"เรียก LLM ไม่สำเร็จ: โมเดลใช้โควตา {budget} tokens หมดโดยไม่มีเนื้อหาตอบกลับ "
        "(stop_reason=max_tokens) — เพิ่ม max_tokens หรือย่อ prompt"
    )
return text + _TRUNCATION_NOTE
```

**เทสต์ใหม่** `tests/test_llm_truncation.py` — stub client ที่คืน `stop_reason="max_tokens"` + content ว่าง ทั้ง 2 attempt → ต้อง `RuntimeError`; คืนเนื้อหาบางส่วน → ต้องได้ข้อความ + หมายเหตุ

**ตรวจต่อ** ทุกผู้เรียกต้องรับมือ `RuntimeError` ได้อยู่แล้ว (งานอัตโนมัติ catch `LLMDisabledError` — ต้องเช็คว่า catch `RuntimeError` ด้วยไหม ถ้าไม่ ต้องเพิ่ม ไม่งั้น job พัง)

---

### 1.2 `portfolio/tracker.py:156` — เติม FX เองเงียบ ๆ 🟠

**อาการ** `df["fx_rate_thb"] = pd.to_numeric(...).fillna(DEFAULT_USDTHB)` เติม 33.5 ทับค่าว่าง แล้ว `dropna(subset=[..., "fx_rate_thb", ...])` บรรทัดถัดไปจึงไม่มีวันทำงาน = กุตัวเลขบนเส้นทางเงินตรง ๆ

**แก้ (2 ชั้น)**
1. **หาค่าจริงก่อนเดา** — แถวที่มี `amount_thb` ครบ อัตราที่จ่ายจริงคือ `amount_thb / (shares * price_usd)` ใช้ค่านี้ ไม่ใช่ค่า default (ได้ค่าที่ถูกต้องแทนค่าเดา)
2. **ที่เหลือปล่อยเป็น NaN** ให้ `dropna` ทำงานจริง แล้ว**รายงานออกไป** ตามแบบเดียวกับ `missing_prices` ใน `get_total_summary()` — เพิ่มคีย์ `skipped_rows` / `skipped_reason` แล้วให้ dashboard แสดง `st.error`

**ห้าม** ตัดแถวเงียบ ๆ (แค่ย้ายจากกุตัวเลขไปเป็นซ่อนข้อมูล ผิดกฎเหมือนกัน)

**เทสต์ใหม่** `tests/test_tracker_fx_missing.py` — 3 เคส: FX ว่าง+มี amount_thb (ต้อง derive ได้ตรง), FX ว่าง+ไม่มี amount_thb (ต้องตัดออก+รายงาน), FX ครบ (ไม่เปลี่ยนพฤติกรรม)

**หมายเหตุ** `DEFAULT_USDTHB` อ่านตอน import ครั้งเดียว — แก้ config ระหว่างรันไม่มีผล ถ้ายังต้องใช้ที่อื่นให้เปลี่ยนเป็นอ่านตอนเรียก

---

### 1.3 `backend/services/rebalance_service.py:38,43,64,118` — ราคาหายกลายเป็น 0 แล้วพลิกคำสั่งซื้อเป็นขาย 🟠

**อาการ** `prices.get(sym, 0.0)` แต่ contract ของ `get_current_prices()` คือ ticker ที่ดึงไม่ได้ **หายไปจาก dict** → ของที่ถืออยู่ถูกตีมูลค่า 0 → ตัวหารเล็กลง → ตัวอื่นกลายเป็น overweight

หลักฐานที่วัดแล้ว: GLDM ดึงไม่ได้ (มูลค่า 16,028 USD หายจากตัวส่วน) → **XLV พลิกจาก `buy 846.38` เป็น `sell 756.42`** และ QQQM สั่งขายเพิ่มเท่าตัว
เคสหนักกว่า: `calculate_drift` บรรทัด 43 `if total <= 0: return 1.0` → ราคาหายหมด = `max_drift_pct: 100.0` ที่ผลิตจากความล้มเหลวล้วน ๆ

**แก้** rebalance สั่งเงินจริง ต้อง **fail closed**:
- เก็บ `missing_prices` แล้ว **ไม่ผลิต actions เลย** ถ้ามี ticker ที่ถืออยู่ขาดราคา — คืน `{"needs_rebalance": None, "missing_prices": [...], "actions": [], "detail": "..."}`
- `calculate_drift`: `total <= 0` → `raise ValueError` (หรือคืน `None`) ห้ามคืน 1.0
- router ตอบ 200 พร้อมฟิลด์ `missing_prices` (ไม่ใช่ 500) แล้วให้ dashboard/หน้าจอแสดงคำเตือน

**เทสต์ใหม่** `tests/test_rebalance_missing_price.py` — ปัจจุบัน `grep -rn "calculate_drift\|compute_rebalance" tests/` = **ว่างเปล่า** ไม่มีเทสต์แตะเลย

---

### 1.4 `backend/services/cache_service.py:66-76` — แคชความล้มเหลว + ไม่คืนสำเนา 🟠

**อาการ** CLAUDE.md เขียนว่า caching layer *"never caches failures"* — จริงเฉพาะ `utils/cache.py` (มี `_is_cacheable()`) ส่วน `TTLCache` **ไม่มีตัวกรองเลย** แคช `{}` ไว้ 5 นาที และคืน object ตัวเดิม (ผู้เรียกแก้แล้วสกปรกข้าม request)

ผลจริง: yfinance rate-limit หนึ่งครั้ง → `/api/etf/prices` คืน `{}` ค้าง 5 นาที → `jobs/daily_check.py` ตกไปเส้นทาง fallback ที่กุ `+0.00%` (ข้อ 2.3)

**แก้** ยก `_is_cacheable()` จาก `utils/cache.py` มาใช้ร่วมกัน (หรือ import ตรง ๆ — อย่าเขียนตัวที่สอง) + `copy.deepcopy` ตอนคืนค่า
เพิ่มเงื่อนไข: ผลบางส่วน (ขอ 5 ตัวได้ 4) ก็ไม่ควรแคช — ต้องเทียบจำนวนคีย์ที่ขอกับที่ได้

**เทสต์ใหม่** `tests/test_cache_service.py`

---

### 1.5 `analysis/financial_model.py:314-315` — โมเมนตัมใช้ผลรวมรายวันแทนผลตอบแทนจริง 🟠

**อาการ** `returns.tail(21).sum()` เป็นผลรวมเลขคณิตของ pct_change ซึ่ง > ผลตอบแทนเรขาคณิตเสมอเมื่อมีความผันผวน

วัดเองแล้ว: VOO วันนี้ 3 เดือน รายงาน **4.3875%** ราคาจริงขึ้น **4.2492%** (+0.14 จุด) · สแกน 10 ปี × 5 ETF: **156/10,718 วัน (1.46%)** ที่ผลรวม > 0 แต่ผลตอบแทนจริง ≤ 0 → ได้ `momentum_score` 10 คะแนนฟรี · **flip ทางลงเป็น 0 ทุกตัวทุกหน้าต่าง** = อคติทิศเดียว · ดัน tilt ได้ ~16%

**แก้**
```python
def _period_return_pct(closes: pd.Series, bars: int) -> float:
    """ผลตอบแทนทบต้นจริงของช่วง — ไม่ใช่ผลรวมของผลตอบแทนรายวัน"""
    if len(closes) <= bars:
        return float("nan")
    return float(closes.iloc[-1] / closes.iloc[-(bars + 1)] - 1.0) * 100.0
```
- ใช้กับทั้ง `return_1m` (21) และ `return_3m` (63)
- **ห้ามใช้ `_safe_float(..., 0.0)`** — NaN ต้องเป็น NaN แล้วให้ `_momentum_score` คืน `None`/ตัดออกจาก `max_score` เหมือนที่ `dividend_score` ทำอยู่ (ตอนนี้ NaN → 0.0 → นับเป็น "ไม่บวก" ซึ่งเป็นการเดา)
- `score_from_prices` บังคับ ≥ 200 แท่งอยู่แล้ว จึงไม่ตกขอบในทางปฏิบัติ แต่ guard ต้องมี

**ผลข้างเคียงที่ต้องรับ** คะแนนจะเปลี่ยน → เทสต์ที่ตรึงตัวเลขไว้อาจแดง ต้องอัปเดตพร้อมเหตุผล และ `dashboard/app.py:3042` ที่เอา `return_1m_pct/return_3m_pct` ไปโชว์เป็นชิปพร้อมสีเขียว/แดง จะถูกต้องขึ้นเองโดยไม่ต้องแก้

**เทสต์ใหม่** `tests/test_momentum_return.py` — ป้อนซีรีส์ที่รู้คำตอบ (ขึ้นแล้วลงกลับที่เดิม → ต้องได้ 0.00% ไม่ใช่ค่าบวก)

---

### 1.6 `portfolio/benchmark.py:111` — `xirr()` คืนขอบบนเป็นคำตอบ 🟠

**อาการ** เจอ NaN ในกระแสเงินสด → bisection ไม่ลู่เข้า แล้วคืน `(low+high)/2` = **10.0 = "+1000%/ปี"** ทั้งที่ docstring เขียนเองว่า *"คืน None เมื่อ... ผู้เรียกแสดง คำนวณไม่ได้ ห้ามเดาเลขแทน"*

**แก้** ก่อน `return` ตอนจบลูป ต้องตรวจว่ารากใช้ได้จริง:
```python
result = (low + high) / 2.0
npv = _npv(result, flows, t0)
if not math.isfinite(npv) or abs(npv) > _NPV_TOLERANCE:
    return None
return result
```
`_NPV_TOLERANCE` ควรสัมพันธ์กับขนาดกระแสเงิน (เช่น `1e-6 * sum(abs(a))`) ไม่ใช่ค่าคงที่
เพิ่มการกรอง NaN ตอนสร้าง `flows` ด้วย (`math.isfinite(a)`)

**หมายเหตุ** แกนคณิตของ solver **ไม่มีบั๊ก** — ตรงกับ scipy brentq ที่ 2.55e-13 บนสมุด DCA จริง 54 ไม้ บั๊กอยู่ที่ขอบเท่านั้น

**เทสต์เพิ่มใน** `tests/test_benchmark.py` (ปัจจุบัน 8 passed แต่ไม่มีเคส NaN เลย)

---

### 1.7 `backend/main.py:89-90` — route `/api/backtest` ซ้ำสองที่ 🔴

**อาการ** `analysis.router` ลงทะเบียนก่อน `backtest.router` ทั้งคู่ประกาศ `POST /api/backtest` → FastAPI ส่งงานให้ตัวแรกเสมอ
ผล: **`backend/routers/backtest.py` เป็นโค้ดตายทั้งไฟล์** (BacktestEngine vectorbt RSI+MACD, `optimize()`, `best_params`, `?include_ai=true`) และ `openapi.json`/`/docs` เก็บ schema ของตัวที่เข้าไม่ถึง → ใครทำตามเอกสารได้ **422 ตลอดกาล**

**แก้** ตาม Backend Router Map ใน CLAUDE.md (`/api/backtest → routers/backtest.py vectorbt RSI+MACD strategy`):
- ให้ `routers/backtest.py` **ถือ path `/api/backtest`** ตามเอกสาร
- ย้าย backtest แบบน้ำหนักพอร์ตใน `routers/analysis.py` ไปเป็น **`POST /api/analysis/backtest`**
- `grep -rn "api/backtest" dashboard/ backend/ tests/ README.md` แล้วอัปเดตผู้เรียกทุกจุด
- อัปเดต README.md:104 + CLAUDE.md Router Map ให้ตรงกับความจริง

**เทสต์ใหม่** `tests/test_route_uniqueness.py` — ไล่ `app.routes` แล้ว assert ว่าไม่มีคู่ `(path, method)` ซ้ำกันเลย (กันบั๊กชนิดนี้กลับมาทั้งตระกูล)

---

### 1.8 `analysis/llm.py:62,145` — ไฟล์ `.env` ชนะ env ของโปรเซส (พบเพิ่ม 2026-08-05) 🟠

**อาการ** `chat_text()` และ `auto_enabled()` เรียก `load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)` **ทุกครั้งที่ถูกเรียก** — `override=False` แปลว่า "ถ้าตัวแปรไม่มีใน env ให้เอาจากไฟล์" ผลคือ **การ unset ตัวแปรใน env ของโปรเซสไม่มีผล ไฟล์ชนะเสมอ** รวมถึง `VAULTIS_LLM_AUTO` ซึ่งเป็นสวิตช์คุมค่าใช้จ่าย

หลักฐานที่วัดแล้ว: `test_missing_key_fails_loudly` ทำ `monkeypatch.delenv("ANTHROPIC_API_KEY")` แล้วคาดว่าจะได้ `RuntimeError` เรื่องไม่มีคีย์ แต่ `chat_text` โหลด `.env` ทับกลับมา จึงเดินเลยด่านไปชนกับ guard `"เรียก provider จริง!"` แทน → **เทสต์ชุดนี้ไม่ hermetic: ผลขึ้นกับว่าเครื่องที่รันมี `.env` หรือไม่**

**แก้** ย้าย `load_dotenv` ไปทำครั้งเดียวตอน import (หรือถอดออกจาก library code แล้วให้ entrypoint โหลดแทน) — **env ของโปรเซสต้องเป็นแหล่งความจริงตอนรัน `.env` เป็นแค่ค่าเริ่มต้นตอนบูต** · `grep -rn "load_dotenv" analysis/ backend/ portfolio/ utils/ alerts/ jobs/` แล้วแก้ทุกจุดที่เรียกซ้ำในเส้นทางร้อนแบบเดียวกัน

**เกณฑ์ผ่าน** `test_missing_key_fails_loudly` ต้องผ่าน**ตอน mount ทับ** → เส้นฐานกลับเป็น 348 passed

---

## เฟส 2 — งานเบื้องหลังที่รายงานผลผิด (ทำขนานได้ ไฟล์ไม่ทับกัน)

### 2.1 `backend/screener/scheduler_job.py:29` + `engine.py:64,87,107` 🟠

สามบั๊กในเอนจินเดียว:
- **"ดึงราคาไม่ได้" กับ "ไม่มีสัญญาณ" ให้ผลเหมือนกันทุกไบต์** — `engine.run()` กลืน exception ไว้ใน `logger.error` แล้วคืน list ว่าง; job 07:00 พิมพ์ `[screener] no signals today` และไม่ส่ง Telegram ผู้ใช้เชื่อว่า "ไม่มีอะไรต้องทำ" ทั้งที่ระบบตาบอด (คอมเมนต์ที่ `engine.py:27-30` ห้ามเรื่องนี้ไว้เอง)
- **`logic` ที่ไม่ใช่ `"AND"` ตกไป `any()` เสมอ** (บรรทัด 87-90) → พิมพ์ `"BOTH"` แล้ว AND กลายเป็น OR เงียบ ๆ; field/operator สะกดผิดก็ได้ HTTP 200 "ไม่มีสัญญาณ"
- **fail-loud ไม่สอดคล้องกันในเอนจินเดียว** — `price_vs_ma200` raise แต่ `golden_cross`/`death_cross`/`bb_squeeze` คืน `False` เมื่อคำนวณไม่ได้ → preset `golden_cross_alert` (ที่ job 07:00 ใช้) รายงาน "ไม่มีสัญญาณ" ตลอดกาลสำหรับ ETF ที่ประวัติสั้นกว่า 200 วัน

**แก้** ผลลัพธ์ต้องพก error รายสัญลักษณ์: `{"results": [...], "errors": [{"symbol", "detail"}], "symbols_checked": n, "symbols_failed": m}` · `logic` ที่ไม่รู้จัก → `ValueError` (422) · `field`/`operator` ที่ไม่รู้จัก → `ValueError` · detector คืน `None` เมื่อคำนวณไม่ได้ ไม่ใช่ `False` · job ส่งแจ้งเตือน "วันนี้สแกนไม่สำเร็จ" แทนที่จะเงียบ
**เสริม** `_fetch_df` ใช้ `period="1y"` (~250 แถว) ให้กฎที่ต้องใช้ MA200 — เหลือ margin แค่ ~50 แถว ควรเป็น `"2y"`

### 2.2 `backend/routers/websocket.py:55` 🟠

ใช้ `fast_info['previous_close']` ซึ่งเป็นฟิลด์จาก quote endpoint ที่ไม่ตรงกับ bar รายวันใด ๆ → **QQQM กลับเครื่องหมาย** (จริง +0.69% โชว์ −0.07% สีแดง) · VOO ต่าง 0.21 จุด · GLDM 0.12 จุด
แถบราคาสดนี้อยู่บนสุดของ **ทุกหน้า** (`dashboard/app.py:444-462`) และระบบเดียวกันรายงาน %ของวันเดียวกันไม่ตรงกัน 3 ค่าจาก 3 ทาง

**แก้** ใช้ราคาปิด 2 แท่งล่าสุดจาก history เหมือน `/api/etf/daily-snapshot` (ฐานเดียวทั้งระบบ) · `prev <= 0` → คืน `None` ไม่ใช่ `change_pct = 0.0`

### 2.3 `jobs/daily_check.py:46-57,123-124` 🟠

`_change_pct_yfinance` มี `except → return 0.0` → Discord โชว์ `+0.00% 🟢` ทั้งที่ดึงไม่ได้
พี่น้องของมันแก้ไปแล้ว: `alerts/price_alert.py:240` มีคอมเมนต์ *"เดิมโชว์ 🟡 (0.00%) ทำให้ดูเหมือนราคาไม่เปลี่ยน ทั้งที่ดึงข้อมูลไม่ได้ (AUDIT.md C1)"* แต่ job นี้ (ที่ GitHub Actions รันทุกวันทำการ) ตกหล่น

**แก้** คืน `None` แล้วแสดง `⚠️ ดึง %เปลี่ยนแปลงไม่ได้` ตามแบบ `price_alert.py`
**เสริม** `alerts/price_alert.get_price_snapshots` ยิง `yf.download` ตรง ไม่ผ่าน `data/fallback` (Stooq/AlphaVantage) → cron ไม่ได้ประโยชน์จาก fallback ที่มีอยู่แล้ว

### 2.4 `alerts/notifier.py:91` 🟠

`send_technical_alert` ส่ง Discord ที่ `Signal: ` **ว่างเปล่า** และบรรทัด MA200 เป็น `MA200:  MA200 ` — และที่หนักกว่าคือ **oversold ในขาขึ้นกับขาลงได้ข้อความเดียวกันสีเขียวเหมือนกัน** = เชียร์ให้ซื้อในขาลง ซึ่งเป็นสิ่งที่ `signal_rules` ถูกสร้างมากันโดยเฉพาะ (ฟังก์ชันรับ `price`/`ma200` มาแล้วแต่ใช้ใน `elif` ที่ไปไม่ถึงเมื่อ `rsi<30`)

**แก้** เรียก `signal_rules.dca_signal()` + `thai_description()` แทนการประกอบข้อความเอง — แยก `ACCUMULATE` (เขียว, "จังหวะสะสม") ออกจาก `DOWNTREND_WATCH` (เหลือง, "เฝ้าดู รอยืนยัน") ตามนิยามกลาง

### 2.5 `portfolio/backtest.py:81-86` + `portfolio/dca.py:154-158` 🟠

`/api/backtest` และ `/api/dca/simulate` **ตัด ticker ที่ไม่รู้จักทิ้งเงียบแล้ว normalize น้ำหนักใหม่**
ถาม "VOO ครึ่ง AAPL ครึ่ง ย้อนหลังเป็นยังไง" → ได้ผล **VOO 100% เป๊ะทุกทศนิยม** พร้อม HTTP 200 ไม่มี warning
สาเหตุ: `market_analysis_service._prices()` ดึงเฉพาะ `get_tickers()` แล้ว backtest กรอง `ticker in price_df.columns`

**แก้** ticker ที่ไม่มีข้อมูล → `ValueError` (422) พร้อมบอกชื่อ หรืออย่างน้อยคืนฟิลด์ `dropped_tickers` แล้วให้หน้าจอเตือน — ห้ามตอบคนละคำถามกับที่ถูกถามโดยไม่บอก

### 2.6 `analysis/news_fetcher.py:87,282,363` 🟠

- **ไม่มีตัวกรองความเกี่ยวข้องเลย** — `params={"q": symbol}` ดิบ ๆ ไม่กรองผลกลับ `q=XLV` ลากข่าว NFL/NASA/มิสซาเข้ามา (XLV = เลขโรมัน 45 = Super Bowl XLV) **6/20 = 30% ของผล NewsAPI ดิบไม่เกี่ยว** และ 2 ชิ้นขึ้นหน้าจริง
- **dedupe เทียบ URL ดิบ** จับซ้ำข้ามแหล่งไม่ได้เลย — บทความเดียวกินได้ 4 ช่อง เสียช่อง **20–36%** (VOO 7/25, SCHD 9/25, QQQM 8/25, XLV 5/25, GLDM 8/25) และ sentiment ถูกนับซ้ำ 2–4 เสียงจน**พลิกป้าย** (VOO 0.1304 "positive" → 0.0333 "neutral"; XLV กลับเครื่องหมาย)
- **`fetch_rss_status` คืน `status=ok count=0`** เมื่อฟีดตอบ 200 แต่เนื้อเป็น HTML (หน้า error/consent) → "ดึงไม่สำเร็จ" กลายเป็น "ไม่มีข่าว" ตรง ๆ ผิด C1

**แก้** เพิ่มตัวกรองความเกี่ยวข้อง (ticker + ชื่อกอง + คำกลุ่มอุตสาหกรรม, ปฏิเสธเมื่อไม่พบใน title/description) · dedupe ด้วย normalized URL (ตัด query/utm/แฟรกเมนต์, unwrap Google News redirect) + fuzzy title · `feedparser` ที่ได้ `bozo`/0 entries จาก content-type ที่ไม่ใช่ feed → `status=error`

### 2.7 `analysis/risk.py` + `dashboard/app.py:3542` + `/api/etf/risk` 🟡

ตาราง Risk เทียบ ETF **คนละช่วงเวลา** โดยไม่บอก:
```
        ตามที่โชว์            ช่วงร่วมกันจริง (n=1456)
        MaxDD                 MaxDD              Δ
VOO     -33.99%               -24.52%          -9.47
QQQM    -35.04%               -35.04%           0.00
SCHD    -33.37%               -16.84%         -16.52
```
บนจอ QQQM ดู "แย่กว่า VOO นิดเดียว" ทั้งที่เทียบช่วงเดียวกันห่างกัน **10.5 จุด** และ "ตัวที่ drawdown ตื้นสุด" เปลี่ยนจาก GLDM เป็น SCHD
หน้า Return Analysis ที่คอลัมน์ติดกันมี caption เตือน (`app.py:3537`) แต่ตาราง Risk ไม่มี

**แก้** แสดงคอลัมน์ช่วงข้อมูลจริง + จำนวนแถวต่อ ETF และเพิ่มโหมด "ช่วงร่วมกัน" (`dropna(how="any")`) ให้เทียบกันได้จริง

### 2.8 `dashboard/app.py:3462` 🟡

แถว 10Y ว่าง (N/A) เสมอทั้งบนจอและใน PDF ทั้งที่ backend คำนวณได้ 308% — เพราะดึงแค่ 10 ปี = **2,511 แถว** ให้หน้าต่างที่ต้องการ **2,520 แถว**
**แก้** ดึง `years=11` หรือคำนวณหน้าต่างจากวันที่จริง ไม่ใช่นับแถว

---

## เฟส 3 — dashboard (ไฟล์เดียว 3,700+ บรรทัด ต้องทำทีละคนหลังเฟส 1-2)

### 3.1 `dashboard/app.py:2447-2523` — หัวข้อ "ชนะ VOO ไหม" 🔴

**สามบั๊กซ้อนกันในหัวข้อเดียว**

**(ก) เทียบคนละฐาน** ขาเงาใช้ `cached_prices` → `fetch_adjusted_close_data` → **Adj Close = total return** (ยืนยันที่ `data/fetcher.py:85`) ส่วนขาพอร์ตจริงคือ `Current Value (USD)` = มูลค่าหุ้นล้วน ปันผลที่รับเป็นเงินสดไม่ถูกนับกลับ
วัดเอง: VOO 3 ปี total **70.29%** vs price **63.73%** = **เอียงเข้าข้าง VOO ~2.2 จุด/ปี ตลอดเวลา** สมุดที่ซื้อ VOO ล้วนวันเดียวกันเป๊ะยังโชว์ว่าแพ้ VOO

**(ข) DRIP ถูกนับเป็นเงินใหม่** บรรทัด 2453 เอาแถวที่ `tx_type != dividend` ทั้งหมดเป็น "เงินเข้า" รวมไม้ที่ซื้อด้วยเงินปันผล → ขาเงาพองเกินจริง 6.73% และ `invested_usd` ที่พองยังไปเป็นตัวหารของ "พอร์ตจริง %" กดผลตอบแทนพอร์ตจริงลงอีกชั้น **ผู้ใช้ที่วินัยดีที่สุดโดนลงโทษหนักที่สุด**

**(ค) ราคาหายตัวเดียวทำ XIRR เพี้ยน 8.62 จุด** บรรทัด 2479 `priced = holdings_df[holdings_df["Price OK"]]` ตัดตัวที่ไม่มีราคาออกจาก `actual_value_usd` แต่ขาเงายังนับเงินก้อนนั้นครบ และ `actual_value_usd` ตัวเดียวกันไปเป็น flow สุดท้ายของ XIRR (บรรทัด 2500) → **16.25%/ปี กลายเป็น 7.63%/ปี** โดยไม่มีคำเตือนใน section นี้เลย

**แก้ — โมเดลเดียวที่แก้ทั้งสามข้อพร้อมกัน: กระแสเงินสดภายนอกชุดเดียวกัน**

> เงาต้องเจอ**ตารางเงินเข้า-ออกจากภายนอก**ชุดเดียวกับพอร์ตจริง
> - ทุก **ไม้ซื้อ** = เงินเข้า → เงาซื้อ VOO ด้วยจำนวนเดียวกัน วันเดียวกัน
> - ทุก **ปันผลที่รับ** = เงินที่พอร์ตจริงคายออกมา → **เงาขาย VOO มูลค่าเท่ากัน วันเดียวกัน**
> - มูลค่าปลายทาง: เงา = หุ้น VOO ที่เหลือ × ราคาวันนี้ | จริง = มูลค่าที่ถืออยู่

พิสูจน์ว่าถูก: ไม้ DRIP คือ (ปันผลเข้า → ซื้อออก) ซึ่งหักล้างกันพอดีในขาเงา · พอร์ตที่ซื้อ VOO ล้วนได้ส่วนต่าง **0.00 เป๊ะ** · กำไรสุทธิเท่ากันไม่ว่าจะมองปันผลเป็น "ถอนออก" หรือ "ลงทุนต่อ"

ต้องแก้ที่ `portfolio/benchmark.shadow_benchmark()` ให้รับ flow ทั้งขาเข้าขาออก (ไม่ใช่แค่ `buys`) แล้วคืน `shares` ที่อาจลดลงได้
**(ค)** เพิ่มด้วย: มี ticker ไหน `Price OK=False` → **ไม่แสดงทั้ง section** พร้อม `st.error` (เทียบไม่ได้ ก็อย่าแสดงตัวเลขที่เทียบไม่ได้)

**เทสต์ใหม่** `tests/test_shadow_symmetry.py` — สมุดที่ซื้อ VOO ล้วนวันเดียวกัน **ต้องได้ส่วนต่าง 0.00** (เทสต์นี้จะจับบั๊กทั้งตระกูลนี้ตลอดไป) + เคส DRIP + เคสราคาหาย

### 3.2 `dashboard/app.py:2756` — โหมด USD แปลงเงินสองรอบ 🟠

`invested = total_invested_thb / today_fx` ทั้งที่ยอด THB นั้นแปลงมาด้วย **FX วันซื้อ** แล้ว → กำไรเหลือ ~1/4 ของจริง (วัดได้ P&L 400 USD → โชว์ 98.46) และ `delta=total_return_pct` ที่แปะข้าง ๆ เป็นตัวเลขฐาน THB
ถ้าบาทแข็งพอ `total_pnl_thb` ติดลบขณะ USD ยังกำไร → **ตัวเลขพลิกเครื่องหมาย**
ฝั่ง API ทำถูกอยู่แล้ว (`portfolio_service.py:90-95` บวก USD จาก holdings ตรง ๆ)

**แก้** โหมด USD ต้องบวกจาก `holdings` เป็น USD ตรง ๆ (ทำแบบเดียวกับ API) และ `delta` ต้องเป็น % ฐาน USD

### 3.3 `portfolio/tracker.py:431` — Return (%) คนละฐานกับ P&L ข้าง ๆ 🟠

`grouped["return_pct"] = pnl_usd / invested_usd` (ฐาน USD) แต่วางติดกับ `P&L (THB)` และไม่ตรงกับ `get_total_summary()` บรรทัด 494 ที่ใช้ฐาน THB → หน้าจอเดียวโชว์ **"ขาดทุน 1,072 บาท / +14.44%"** และ % รายตัวบวกกันไม่เท่ากับ % รวม
ไหลออก 4 ทาง: ตาราง holdings, `/api/portfolio/*`, AI advisor, PDF

**แก้** `return_pct` ใช้ฐาน THB (สกุลที่ผู้ใช้จ่ายและวัดผลจริง) และถ้าจะเก็บฐาน USD ไว้ ให้เป็นคอลัมน์แยกที่มีป้ายชัด

### 3.4 `dashboard/app.py:3120,3127` — โหมด "ดึงพอร์ตเข้าเป้า" 🟠

- **สร้างเป้าหมายชุดที่สอง** — `get_target_weights(list(current_values))` ส่งเฉพาะตัวที่ถืออยู่ → เป้าถูก normalize ใหม่เป็น VOO 38.9% ขณะที่หน้า Settings โชว์ 35.0% สำหรับตัวเดียวกัน และ **GLDM ที่ยังไม่เคยซื้อจะไม่มีวันได้เงิน** โหมดที่ชื่อ "ดึงพอร์ตเข้าเป้า" กลับ**ดึงออกจากเป้า** (นี่คือบั๊กที่ `portfolio/targets.py` ถูกสร้างมาแก้ กลับมาเกิดซ้ำ)
- **ราคาหาย = มูลค่าหายเงียบ** — แถว `Price OK=False` ถูก filter ทิ้ง ทำให้ VOO ขยับจาก 35.0% เป็น 43.7% เพราะราคาของ **ticker อื่น** ดึงไม่ได้ ไม่มีคำเตือน

**แก้** เป้าหมายมาจาก `get_target_weights(get_tickers())` เสมอ (แหล่งเดียว) · ราคาหาย → `st.error` + ไม่แสดงแผน

---

## เฟส 4 — เพิ่มความแม่น (3 อันดับแรกจากผลตรวจ)

### ① μ/σ ที่ป้อน Monte Carlo หน้า Goals — บิดคำตอบมากกว่าบั๊กทุกตัวรวมกัน

**ปัญหา 3 ชั้น** (`backend/services/goal_service.py:54,56,130-140` → `analysis/risk.py:130`)

1. **ข้อมูลถูกตัดโดยไม่มีใครรู้** `.dropna()` ทั้งแถว แต่ QQQM เกิด 2020-10 → ใช้จริง **1,455 จาก 2,511 แถว (5.8 ปี)** ขณะที่ป้ายเขียน `"พอร์ตจริงจาก ledger (ย้อนหลัง 10 ปี)"` และหน้าต่างที่เหลือ**ไม่มีวิกฤตใหญ่สักรอบ**
2. **μ ที่วัดจากอดีตถูกใช้เป็น μ พยากรณ์** μ=15.08% ป้อนเข้า MC ตรง ๆ:
   ```
   เติม 10,000 บาท/เดือน 20 ปี  เป้า 8 ล้าน
     μ 15.08% (ที่ใช้อยู่)  → P 85.0%   median 13.32 ล้าน
     μ 12%    (aggressive)  → P 57.5%   median  8.77 ล้าน
     μ  9%    (moderate)    → P 25.9%   median  5.92 ล้าน
     μ  7%    (conservative)→ P 11.5%   median  4.66 ล้าน
   ```
   **ต่าง 59 จุดเปอร์เซ็นต์ / มูลค่าปลายทาง 2.25 เท่า** จากสมมติฐานตัวเดียว
3. **normal iid ล้วน** เทียบ block bootstrap จากผลตอบแทนจริง: เป้า 12 ล้าน 58.3% → **39.4%** (ต่าง 18.9 จุด)

**ทำ**
- ยืดประวัติด้วย `PROXY_MAP` ที่มีอยู่แล้วที่ `portfolio/ab_backtest.py:44` (QQQM→QQQ, GLDM→GLD) ใช้ซ้ำได้ทันที → sigma 14.26% → 15.76%, maxDD −21.2% → **−29.0%** (ที่ผู้ใช้ควรเตรียมใจ ต่ำไป 7.8 จุด)
- **แก้ป้ายให้บอกช่วงจริง + จำนวนแถวที่ใช้** — ป้ายที่ไม่ตรงข้อมูลคือการกุข้อมูลชนิดหนึ่ง
- **แยก "μ ที่วัดได้จากอดีต" ออกจาก "μ ที่ใช้พยากรณ์"** แล้วโชว์หลายฉาก (อดีต / 9% / 7%) แทนตัวเลขเดียว — ตรงกับหลัก fail-loud ที่ห้ามให้ค่าที่ไม่รู้กลายเป็นเลขที่ดูน่าเชื่อ
- เพิ่ม block bootstrap เป็นตัวเลือกข้าง normal
- **แถม (แก้ที่เดียวกัน)** `goal_service.py:173` ใช้ arithmetic mean เป็นอัตราทบต้น → สูงกว่า CAGR จริง 0.56 จุด/ปี ทำให้เงินออมที่ต้องการต่ำไป 8%
- **แถม** เป้าหมายเป็นบาท**นามธรรม** ไม่ปรับเงินเฟ้อเลย — เป้า 20 ปีที่ 2%/ปี มีอำนาจซื้อจริงเหลือ ~67% ของตัวเลขที่โชว์ (ควรแสดงคู่กัน)

### ② ช่วงความเชื่อมั่นบนทุกเลขที่ใช้ตัดสิน "ชนะไหม" — แรงน้อยที่สุด ข้อมูลพร้อมอยู่แล้ว

`portfolio/ab_backtest.py:316` ตัดสินด้วยการเทียบจุดเดียว (`by_value = tilt > plain`) ทั้งที่มีอนุกรมผลตอบแทนรายเดือนอยู่ในมือ

```
[proxy] tilt-plain        +0.136%/ปี  SE 0.080  t=+1.70  CI95 [-0.02,+0.29]
[real]  tilt-plain        -0.002%/ปี  SE 0.122  t=-0.02  CI95 [-0.24,+0.24]
[proxy] พอร์ต5ตัว vs VOO  -0.93%/ปี   SE 0.85   t=-1.10  CI95 [-2.59,+0.73]
        → ต้องใช้ข้อมูล 96.4 ปี ถึงจะสรุปได้ว่าต่างจริงที่ขนาดนี้
```

**ทำ**
- เพิ่ม paired t-test บนผลตอบแทนรายเดือน + CI95 เข้า verdict ของ harness (`portfolio/ab_backtest.py`)
- หัวข้อ "ชนะ VOO ไหม" (`dashboard/app.py:2447`) เลิกตอบเป็นตัวเลขเดียว — คำตอบที่ถูกคือ **"แยกไม่ออกจากศูนย์"**
- ด่าน edge ปัจจุบันบังเอิญตัดสินถูกด้วยเหตุผลผิด (proxy บอก "มูลค่าชนะ Sharpe แพ้" ทั้งที่ทั้งคู่อยู่ในกำแพงเสียงรบกวน) — paired test มี power ดีกว่าที่คิด (**MDE 0.22%/ปี** เพราะสองแขนสหสัมพันธ์สูง) จึงใช้เป็นด่านที่แข็งขึ้นได้ทันทีโดยไม่ต้องหาข้อมูลเพิ่ม
- **แถม** `ab_backtest.py:228` เก็บ `round(sharpe, 2)` แล้วเอาค่าที่ปัดแล้วไปเทียบ — ส่วนต่างจริงระหว่างแขนเล็กกว่าความละเอียดที่เก็บ (ตรวจแล้วยังไม่พลิกคำตัดสิน แต่ควรเก็บค่าเต็ม)

### ③ Look-through holdings — ข้อมูลนี้ระบบไม่เคยมีเลย และมันเปลี่ยนภาพพอร์ตทั้งใบ

`dashboard/app.py:2583-2634` หัวข้อ "การกระจายจริง & ความทับซ้อน" มีแค่ correlation matrix กับข้อความบรรยาย — **ไม่มีตัวเลขความทับซ้อนสักตัว** ทั้งที่ yfinance ให้ฟรีผ่าน `Ticker(t).funds_data`

```
ทะลุลงถึงรายหุ้น (ขอบล่าง — นับเฉพาะ top-10 ของแต่ละกอง):
  NVDA 4.14% [VOO,QQQM]   AAPL 3.63% [VOO,QQQM]   MSFT 2.37% [VOO,QQQM]
  UNH  1.77% [SCHD,XLV]   MRK  1.68% [SCHD,XLV]   AMGN 1.41% [SCHD,XLV]
ทะลุลงถึงเซกเตอร์:
  technology 28.88%   healthcare 19.02%   consumer_defensive 8.00%
```

สามอย่างที่ผู้ใช้เข้าใจผิดอยู่ตอนนี้:
- คิดว่าถือ healthcare 10% (XLV) — **จริงคือ 19.02%** (SCHD มี healthcare 20.77%, VOO มี 8.9%)
- คิดว่ากระจาย 5 กอง — **NVDA ตัวเดียวกิน 4.14%** ของพอร์ตทั้งใบ (และนี่คือขอบล่าง)
- VOO–QQQM correlation **0.94** (rolling 1 ปี ต่ำสุดยัง 0.81) → เงิน 55% ของพอร์ตอยู่ในสินทรัพย์ที่แทบเป็นตัวเดียวกัน โดยฝั่ง QQQM จ่ายค่าธรรมเนียมแพงกว่า 0.12 จุด/ปี

**ทำ** โมดูลใหม่ `portfolio/lookthrough.py` เรียก `funds_data.top_holdings` + `sector_weightings` คูณกับ `get_target_weights()` แล้วเพิ่ม section ใน dashboard
**เสริม (แก้ที่เดียวกัน)** `dashboard/app.py:2589` แสดง correlation นิ่งช่วงเดียว 10 ปี (และพาดหัวว่า "10 ปี" ทั้งที่ QQQM มีแค่ 5.8 ปี) — เปลี่ยนเป็น rolling 252 วัน แสดง ต่ำสุด/เฉลี่ย/สูงสุด/ปัจจุบัน:
```
        ต่ำสุด  เฉลี่ย  สูงสุด  ปัจจุบัน
QQQM    +0.81  +0.94  +0.98  +0.93   ← ทับซ้อนเสมอ ไม่เคยกระจาย
SCHD    +0.29  +0.77  +0.94  +0.29   ← เลขที่โชว์ 0.76 ไม่ตรงกับสถานะปัจจุบันเลย
GLDM    -0.11  +0.13  +0.31  +0.30
```
เกณฑ์เตือน `>= 0.85` ที่บรรทัด 2602 จับได้แค่ QQQM ทั้งที่ SCHD เคยขึ้นถึง 0.94 (กระจายความเสี่ยงหายไปในช่วงที่ต้องการมันที่สุด)

> **ข้อบังคับ** ทั้ง 3 อย่างเป็น**สถิติเชิงพรรณนา** — ห้ามให้ไหลเข้าเลขคะแนนหรือการจัดสรร DCA (invariant เดียวกับ `trend_channel.py`) ตรวจด้วย `grep` ตอนจบ

---

## เฟส 5 — ปิดงาน

1. `docker compose --profile dev run --rm tests` **เต็มชุด** ต้อง ≥ 348 passed
2. `grep -rn "news_fetcher\|sentiment\|trend_channel\|lookthrough" analysis/financial_model.py portfolio/targets.py technical/ backend/screener/` ต้อง = **0** (invariant ยังอยู่)
3. `docker compose build && docker compose up -d` แล้วยิง smoke test ทุก endpoint
4. อัปเดต `CLAUDE.md` (Router Map, caching, ข้อความเรื่อง TTLCache ที่ไม่ตรงความจริง) + `AUDIT.md` + README
5. commit แยกเป็นก้อนตามเฟส (**ยังไม่ commit จนกว่าจะสั่ง**)

---

## สิ่งที่ตรวจแล้วแต่ **ไม่อยู่ในแผน** (ทำแล้วได้ไม่คุ้ม หรือทำแล้วผิดกว่าเดิม)

| เรื่อง | เหตุผล |
|---|---|
| หัก expense ratio ออกจากผลตอบแทนย้อนหลัง | Adj Close หักไปแล้ว — พิสูจน์: QQQM vs QQQ ต่าง 0.083 จุด/ปี = ส่วนต่าง ER พอดี **ทำแล้วผิด** (หมายเหตุ: `financial_model.py:85` + `etf_info_service.py:50` อ่าน `annualReportExpenseRatio` ที่ yfinance คืน null ทุกกอง → เป็น 0 มาตลอด คีย์ที่ใช้ได้คือ `netExpenseRatio` เอาไปโชว์ได้ แต่ห้ามลบออกจากผลตอบแทน) |
| premium/discount ต่อ NAV | วัดจริง VOO +0.060% SCHD +0.060% QQQM +0.032% — เศษเสี้ยวของ FX spread 0.25% |
| tracking error เทียบดัชนี | ต้องซื้อข้อมูล index total-return ที่ไม่ฟรี เพื่อวัดสิ่งระดับ 0.0x%/ปี |
| realized TTM dividend yield | ไม่ข้ามเกณฑ์ 2%/4% ของ `_dividend_score` สักตัว (VOO 1.070 vs 1.07 · SCHD 3.131 vs 3.3) |
| ปรับสูตรคะแนนปันผลให้ละเอียดขึ้น | ทดสอบตัดออกทั้งก้อนแล้ว **การจัดสรรเงินเหมือนเดิมเป๊ะ** (1800/1300/1000/500/400) เพราะเป็นค่าคงที่รายกองที่เลื่อนทุกตัวพร้อมกัน — เปลี่ยนแค่ป้าย ไม่เปลี่ยนเงิน |
| ปรับจูน Prophet / เติม regressor | `forecaster.py:32-37` เขียนเหตุผลไว้แล้ว รากปัญหาคือฝึกบนข้อมูล 2 ปี = ฤดูกาลรายปี 2 รอบ และการพยากรณ์ราคาไม่ใช่ตัวขับการตัดสินใจของ DCA |
| เอาข่าว/sentiment เข้าเลขคะแนนหรือ DCA · ให้ LLM ผลิตตัวเลข | ผิด invariant ของโปรเจกต์ตรง ๆ — ยืนยันแล้วว่าปัจจุบันยังสะอาด ให้คงไว้ |
| risk-free 2% คงที่ (`analysis/risk.py:13`) | วัดกับ ^IRX จริงแล้วกระทบ Sharpe แค่ **0.015–0.021** แก้บรรทัดเดียวก็ได้ แต่อย่าคาดหวังว่าจะเปลี่ยนอะไร (ใส่ท้ายแถวถ้ามีเวลาเหลือ) |

---

## รายการที่เคย "ไม่ผ่านการตรวจซ้ำ" — **ตรวจครบแล้ว 2026-09-01**

รายการ 19 ข้อชุดนี้ถูกเขียนไว้ตอนตรวจ (2026-08-02) เป็น "อ้างว่าเจอ" ที่ยังไม่ผ่านด่าน
หักล้าง แล้ว **ไม่มีใครกลับมาขีดออกระหว่างทำเฟส 1–5** ผลคือรายการค้างอยู่ 1 เดือน
โดยที่ 18 ใน 19 ข้อถูกแก้ไปแล้วในระหว่างนั้น — ใครหยิบไปทำต่อจะเสียเวลาไล่แก้ของที่
แก้แล้ว **บทเรียน: คิวงานที่ไม่ถูกล้างหลังปิดเฟส มีค่าเท่ากับคิวที่ผิด**

ไล่เช็คทุกข้อกับโค้ดปัจจุบันแล้ว (2026-09-01) — สรุป **แก้แล้วครบทั้ง 19 ข้อ**

| ข้ออ้างเดิม | สถานะ | หลักฐานในโค้ดวันนี้ |
|---|---|---|
| `ta_compat.py:59` RSI 2 ชุดต่างกัน 21.16 จุด | ✅ แก้แล้ว | `_TaWrapper.rsi` และ fallback เรียก `_rsi_fallback` ตัวเดียวกัน (D3.8) |
| `tests/test_money_math.py:240` เทสต์ warm-up สั้นไป 1 index | ✅ แก้แล้ว | `rsi.iloc[:14]` + `first_valid_index() == index[14]` พร้อมคอมเมนต์อธิบายขอบเขตเดิม |
| `screener/engine.py:74` โบนัส +1.0 เมื่อ RSI>65 ฝั่งซื้อ | ✅ แก้แล้ว | `_compute_signal_strength` ดู `preset.direction` และใช้ `signal_rules.RSI_OVERSOLD/OVERBOUGHT` (B6.3) |
| `utils/fx.py:76` แคช fallback 1 ชม. | ✅ แก้แล้ว | `FALLBACK_CACHE_TTL_SEC = 60` แยกจาก `CACHE_TTL_SEC` |
| `utils/fx.py:76` ทิ้ง `is_live` | ✅ แก้แล้ว | `FxRate.is_live` ถูกเก็บใน `_cached` และคืนออกทุกเส้นทาง |
| `tracker.py:490` ตัวหารไม่ตรงกันเมื่อราคาบางตัวหาย | ✅ แก้แล้ว | แยก `invested_thb_all` / `invested_thb_priced` (H9) |
| `benchmark.py:41` guard `or 0.0` ดัก NaN ไม่ได้ | ✅ แก้แล้ว | เหลือแต่คอมเมนต์ห้ามใช้สำนวนนั้น (`bool(nan) is True`) |
| `targets.py:43` ตั้ง weight = 0 เองแล้วไม่มีผล | ✅ แก้แล้ว | "มีคีย์ = ตั้งแล้ว" `{"GLDM": 0}` = ไม่ถือ (B10) |
| `cashflow_rebalance.py:63` เศษ <100 บาทหาย | ✅ แก้แล้ว | largest-remainder + `unallocated_thb` ที่ผู้เรียกต้องแสดง |
| `financial_model.py:444` งบ 1-99 บาทคืนแผนว่างเงียบ | ✅ แก้แล้ว | `raise ValueError` เมื่อ `budget_thb < ALLOCATION_UNIT_THB` (D3.10) |
| `goal_service.py:173` arithmetic mean ใช้เป็นอัตราทบต้น | ✅ แก้แล้ว | `mu_geometric` + `monthly_compound_rate()` |
| `backtest_engine.py:120` กลืน exception | ✅ แก้แล้ว | ไม่จับ `except Exception` แล้ว — บั๊กจริงต้องดังถึงผู้เรียก (B3.2) |
| `backtest_engine.py:154` กลยุทธ์ที่ไม่เคยเทรดถูกรายงานว่าชนะ | ✅ แก้แล้ว | `num_trades > 0` เป็นเงื่อนไขก่อนคิดตัวชี้วัด (B3.2) |
| `ab_backtest.py:307` แขน benchmark ซื้อคนละวัน | ✅ แก้แล้ว | แขน VOO ใช้ `arm_rows` ชุดเดียวกับอีกสองแขน พร้อมคอมเมนต์อธิบายเหตุ |
| `sentiment_analyzer.py:84` batch ที่ล้มหายเงียบ | ✅ แก้แล้ว | `failed_batches` ไหลเข้า `aggregate_sentiment()` และขึ้นในโน้ต coverage |
| `sentiment_analyzer.py:144` ทุกแหล่งพังอ่านเป็น no news | ✅ แก้แล้ว | `_process_symbol` แยก `all_news_sources_failed` / `has_error` ออกจาก "ไม่มีข่าว" |
| `price_alert.py:280` นับ alert ที่ดึงราคาไม่ได้รวมใน checked | ✅ แก้แล้ว | `checked += 1` อยู่หลังด่าน `unchecked.append(...) + continue` ทุกด่าน |
| `routers/analysis.py:45` DCF ticker ไม่มีจริง → 500 | ✅ แก้แล้ว | route แปลงเป็น 404 ผ่าน `_looks_like_unknown_symbol` (D3.1) — ดูกับดักข้างล่าง |

### กับดักตอนตรวจข้อสุดท้าย — **ทดสอบผิดชั้นแล้วได้ข้อสรุปกลับด้าน**

รอบแรกของการตรวจข้อนี้เรียก ``market_analysis_service.dcf_for_ticker("ZZZZNOTREAL")``
**ตรง ๆ** เห็น ``urllib.error.HTTPError`` โผล่ออกมา แล้วสรุปว่า "route ไม่ได้ดัก ⇒ 500"
ซึ่ง**ผิด**: ชั้น service โยน exception ดิบออกมาตามการออกแบบ ส่วน ``routers/analysis.py``
คือชั้นที่แปลงมันเป็นรหัส HTTP อยู่แล้ว ยิงผ่าน ``TestClient`` จริงได้:

```
GET /api/analysis/dcf/ZZZZNOTREAL  -> 404  ไม่พบสัญลักษณ์ ... (ตรวจตัวสะกดอีกครั้ง)
GET /api/analysis/dcf/GLDM         -> 422  GLDM ไม่มีข้อมูล P/E (กองทองคำ)
GET /api/analysis/dcf/VOO          -> 200
```

ทั้งสามเคสถูกตรึงไว้แล้วที่ ``tests/test_audit_d3.py`` (D3.1) ตั้งแต่ 2026-08-06

**บทเรียนสำหรับการตรวจรอบหน้า: ข้ออ้างที่พูดถึงรหัส HTTP ต้องพิสูจน์ด้วยคำขอ HTTP จริง**
ผลจากการเรียกฟังก์ชันภายในไม่ใช่หลักฐานของสัญญาที่ endpoint ให้ไว้ — และกับดักนี้
ทำงานสองทาง: มันสร้างทั้ง "บั๊กปลอม" แบบนี้ และซ่อนบั๊กจริงที่เกิดเฉพาะตอนผ่าน
middleware/serializer (เช่น ``Timestamp`` ที่ทำ ``/api/etf/risk`` ล่มทั้งที่ฟังก์ชันคืนค่าปกติ)

## ยังไม่มีใครตรวจเลย (คิวถัดไปหลังจบแผนนี้)

`networth_service` · `debt_service` · `cashflow_service` · `emergency_fund_service` · `report_service` (ทั้งหมดแตะเงินจริง + SQLite) · `analysis/forecaster.py` · `analysis/sentiment_aggregator.py` · `backend/routers/transactions.py` (slip OCR — เส้นทาง LLM ที่ได้รับข้อยกเว้นจากกฎ `analysis/llm.py`) · `alerts/line_notifier.py` · `scripts/fix_goals.py` · `.github/workflows/`
