# DAR-DCA simulation อนาคต 5–20 ปี (2026-10-02)

ตัวจำลองว่าถ้า DCA 5,000 บาท/เดือน (ไม่หยุด) ด้วยวิธีต่าง ๆ ไป 5/10/15/20 ปี ภายใต้โลกที่มีวิกฤต สงคราม เงินเฟ้อ
ค่าเงินบาท ดอกเบี้ย น้ำมัน ฯลฯ จะได้ผลเป็นอย่างไร — **เพื่อดูความเสี่ยงของแต่ละวิธี ไม่ใช่พยากรณ์** และสร้างหลักฐานว่า DAR
ชนะไม่ได้ (ผลคือสิ่งที่สมมติใส่เข้าไป) ผลที่อ่านได้อยู่ใน `REPORT.md`

ไม่ได้แตะ `research/dar_dca/` (ไฟล์ที่ล็อกด้วย SHA-256) และไม่ได้แตะแผนหลัก — โฟลเดอร์นี้ import `analysis.dar_dca` / `portfolio.risk_weights`
อย่างเดียว ไม่มีอะไรในระบบหลัก import มัน (นอกจากเทสต์ `tests/test_dar_sim_parity.py`)

## รัน (ในคอนเทนเนอร์ของโปรเจกต์ — โฮสต์ไม่มี pandas/yfinance)

```bash
S=/path/to/scratch        # โฟลเดอร์ว่างสำหรับ cache (pickle/CSV ไม่ต้องเข้า git)
D="docker compose --profile dev run --rm -v $PWD:/app -v $S:/scratch -e PYTHONPATH=/app tests"
mkdir -p $S/fred
for id in CPIAUCSL FEDFUNDS DGS10 DCOILWTICO DEXTHUS VIXCLS USREC FPCPITOTLZGTHA; do
  curl -s "https://fred.stlouisfed.org/graph/fredgraph.csv?id=$id" -o $S/fred/$id.csv; done
$D python research/dar_sim/fetch_data.py  /scratch/daily.pkl   # ราคา total return ทีละกอง (ไม่ใช้ yf.download)
$D python research/dar_sim/fetch_macro.py /scratch/macro.pkl   # USDTHB (THB=X)
$D python research/dar_sim/panel.py       /scratch             # แผงสอบเทียบ → panel.pkl
$D python research/dar_sim/verify.py      /scratch             # ต้อง "ทุกข้อผ่าน" ก่อนเชื่อผลใด ๆ
$D python research/dar_sim/run.py         /scratch grid  60000 # ~20 นาที / 6 คอร์
$D python research/dar_sim/run.py         /scratch sweep 60000
$D python research/dar_sim/report.py      /scratch             # → REPORT.md
```

ใช้ CPU อย่างเดียว: 60,000 เส้นทาง × 240 เดือน × 5 กอง ต่อการตั้งค่า ≈ 1 นาทีบน 6 คอร์ GPU ไม่ช่วย
(งานเป็น numpy ขนาดเล็กหลายก้อน ไม่ใช่เมทริกซ์ใหญ่) · เวลารันไม่ใช่ตัวชี้วัดคุณภาพ — ส่วนที่แพงจริงคือความไม่แน่นอนของพารามิเตอร์ต่อเส้นทาง,
ประวัติย้อนหลัง 15 ปีที่สูตรต้องเห็น, ERC ที่ต้องแก้ทุกเดือนทุกเส้นทาง และเส้นทางจำนวนมากพอจะเห็นผลต่าง ~0.1%/ปี

## โครงสร้าง

| ไฟล์ | ทำอะไร |
|---|---|
| `fetch_data.py`, `fetch_macro.py` | ดึงราคา/ค่าเงิน (ทีละกองผ่าน `fetch_total_return_history`) |
| `panel.py` | สร้างแผงสอบเทียบ: เดือนจริงในอดีต แยก regime, เมทริกซ์เปลี่ยน regime, ความแปรปรวนร่วมรายวันจริงรายเดือน (บาท), เงินเฟ้อไทย, ขนาดเหตุการณ์ที่วัดได้ |
| `sim.py` | ตัวสร้างเส้นทาง + กลยุทธ์ 5 แบบ (DAR, 1/N, ERC, 35/25/20/10/10, VOO ล้วน) แบบเวกเตอร์ |
| `verify.py` | พิสูจน์ว่าโค้ดเวกเตอร์ = ฟังก์ชันจริงของโปรเจกต์ |
| `run.py`, `report.py` | รันชุดใหญ่/กวาดจุดคุ้มทุน/เขียนรายงาน |

## ข้อสมมติทั้งหมดอยู่ที่ `sim.py` ส่วน "ข้อสมมติ" (`DRIFT_SETS`, `WORLDS`, `build_events`)

ตัวเลขที่ **วัดจากข้อมูล** กับที่ **สมมติ** ถูกกำกับ `MEASURED` / `ASSUMED` ไว้ในตารางเหตุการณ์ของ `REPORT.md` ห้ามอ่านผลโดยไม่ดูว่ามันมาจากโลกไหน

## รอบที่ 2 (2026-10-05): สูตรใหม่เทียบ ERC — `PREREG_NEW.md` → `REPORT_NEW.md`
`PREREG_NEW.md` + `LOCK_NEW.sha256` ล็อกสูตร (CF_ERC, CF_EQ, BLEND) เกณฑ์ผ่าน/ไม่ผ่าน และคำทำนายไว้ก่อนรัน · `run_new.py` (main/sens/headroom/verdict) ·
`diag_events.py` (วินิจฉัยแยกเหตุการณ์ — สำรวจ ไม่อยู่ในสเปก) · `report_new.py` · โลกใหม่ `boot` = สุ่มบล็อกประวัติจริง · ห้ามแก้ไฟล์ที่ล็อกแล้ว
(ตรวจ: `grep -E '^[0-9a-f]{64}' LOCK_NEW.sha256 | sha256sum -c -`)
