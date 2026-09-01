# ผลทดสอบ 10 รอบ (2026-08-07) — ประเด็นที่ยังไม่ได้แก้

เส้นฐานตอนเขียน: 1297 passed, 0 failed · HEAD = 9d17030 (commit แล้ว)
รวม 56 ประเด็น: critical 1 · high 15 · medium 21 · low 19
ทุกข้อผ่านการรันพิสูจน์จริง (รวม 450 คำสั่ง) — หลักฐานคือคำสั่ง+ผลจริง ไม่ใช่การอ่านโค้ด

## [CRITICAL] config.json ผิดคีย์เดียวใน portfolio.target_weights = แดชบอร์ดใช้ไม่ได้ทั้งใบ และเข้าหน้า Settings ไปแก้ไม่ได้

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py:4249 (+ /home/da00/code/Vaultis/portfolio/targets.py:160-206)`

**อาการ**
การแก้ B10 ทำให้ get_target_weights() โยน InvalidTargetWeights แทนที่จะเดาค่า — ถูกต้องตามหลัก fail-loud แต่ render_dashboard() เรียก _tracked_target_weights() ที่บรรทัด 4249 ซึ่งอยู่ "ก่อน" _render_custom_sidebar() และก่อน dispatch หน้า ⇒ exception ถูก try ชั้นนอกจับแล้ว return ทันที sidebar ไม่เคยถูกวาด ผู้ใช้จึงไปหน้าไหนไม่ได้เลย ทั้งที่ _render_target_weights_table() ในหน้า Settings ดัก InvalidTargetWeights ไว้อย่างดีพร้อมข้อความไทย — แต่ไม่มีทางไปถึง

**หลักฐาน (รันจริง)**
```
รัน render_dashboard() จริงด้วย streamlit stub (target_weights = {"VOO":0.4,"SCHD":-0.1}, default_page=Settings):
$ docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app tests python - <<'PY' … A.render_dashboard() …
ลำดับการเรียกที่เกิดขึ้นจริง:
   error -> เกิดข้อผิดพลาดใน dashboard: portfolio.target_weights[SCHD] = -0.1 ติดลบ — น้ำหนักเป้าหมายต้องไม่ติดลบ (0 = ตั้งใจไม่ถือ)
sidebar ถูกเรียกไหม : False
หน้า Settings ถูกเรียกไหม: False

เทียบกับ HEAD (9861db8) ที่ไม่โยน:
$ git show HEAD:portfolio/targets.py > /tmp/.../head_targets.py ; docker … exec ไฟล์นั้น
HEAD get_target_weights (SCHD=-0.1) -> {'VOO': 0.38095238095238093, 'SCHD': 0.23809523809523808, 'QQQM': 0.19047619047619047, 'XLV': 0.09523809523809523, 'GLDM': 0.09523809523809523}
```

**ผลกระทบ**
ผู้ใช้พิมพ์ค่าเดียวผิดใน config.json (ติดลบ / เป็นสตริง / ผลรวมอ่านไม่ออก / NaN — พิสูจน์แล้วว่าโยนทั้ง 5 แบบ) แล้วแดชบอร์ดทั้ง 13 หน้าดับพร้อมกัน รวมถึงหน้าที่ระบบสร้างไว้เพื่ออธิบายและแก้ปัญหานี้โดยเฉพาะ ทางออกเดียวคือแก้ไฟล์ด้วยมือนอกแอป — ผู้ใช้ที่ไม่เปิด editor ก็จบ

**แนวแก้ที่เสนอ**
ครอบบรรทัด 4249 ด้วย try/except InvalidTargetWeights แล้วเก็บ default_weights = None + ข้อความ error ไว้ ยัง render sidebar/dispatch ต่อได้ตามปกติ · หน้าที่ต้องใช้เป้าหมายจริง (Backtest/DCA Simulator/Scorecard) ค่อยแสดง _render_invalid_target_weights(exc) ของตัวเอง — หน้า Settings ต้องเข้าถึงได้เสมอเพราะเป็นทางแก้

**พบในรอบ** T2 — ล่ารูที่การแก้รอบนี้สร้างขึ้นเอง (money path + ค่าที่ "มีอยู่แต่ใช้ไม่ได้")

---

## [HIGH] ดึงราคาไม่สำเร็จถูกรายงานเป็น "คอนฟิกผิด" — Scorecard ตายพร้อมข้อความที่ชี้ผิดที่ ทั้งที่ config.json ถูกต้องทุกอย่าง

**ไฟล์** `/home/da00/code/Vaultis/portfolio/targets.py:212-224 (_fill_without_unset) → /home/da00/code/Vaultis/analysis/financial_model.py:467 → /home/da00/code/Vaultis/dashboard/app.py:4082`

**อาการ**
calculate_allocation() เรียก get_target_weights(list(usable.keys())) ด้วย "เฉพาะ ticker ที่มีข้อมูล" ตามนโยบาย DCA · ถ้าวันไหน ticker ที่ตั้งน้ำหนักไว้ > 0 ดึงราคาไม่ได้ เหลือแต่ตัวที่ผู้ใช้ตั้งเป็น 0 ไว้ตั้งใจ (เคสตัวอย่างในคอมเมนต์ของ targets.py เอง) subset ที่ส่งเข้าไปจะกลายเป็น "ตั้งครบทุกตัวและรวมได้ 0" ⇒ _fill_without_unset โยน InvalidTargetWeights ว่า "ตั้งเป็น 0 ทุก ticker" ซึ่งไม่จริง และไม่ใช่สาเหตุ · dashboard/app.py:4082 เรียก calculate_allocation โดยไม่มี try (ต่างจากบรรทัด 3825 และ 4003 ที่ดัก InvalidTargetWeights ไว้เรียบร้อย แต่ทั้งคู่รันทีหลัง)

**หลักฐาน (รันจริง)**
```
config ที่ถูกต้อง {"VOO":0.5,"SCHD":0.5,"QQQM":0,"XLV":0,"GLDM":0} + VOO/SCHD data_ok=False (rate limited):
$ docker … tests python -c "FM.calculate_allocation(scores, 5000.0)"
RAISED InvalidTargetWeights : portfolio.target_weights ตั้งเป็น 0 ทุก ticker — ไม่มีสัดส่วนให้จัดสรร ลบคีย์ที่ไม่ต้องการออก หรือกำหนดน้ำหนักให้อย่างน้อยหนึ่งตัว
(ถ้าทุกตัวมีข้อมูล: {'VOO': {...2500...}, 'SCHD': {...2500...}} ปกติ)

รัน render_dashboard() หน้า Scorecard ด้วย config เดียวกัน:
sidebar -> Scorecard
error -> เกิดข้อผิดพลาดใน dashboard: portfolio.target_weights ตั้งเป็น 0 ทุก ticker — ไม่มีสัดส่วนให้จัดสรร …

HEAD ไม่โยน: HEAD get_target_weights subset [QQQM,XLV,GLDM] -> {'QQQM': 0.5, 'XLV': 0.25, 'GLDM': 0.25}
```

**ผลกระทบ**
ละเมิดกฎแกนของโปรเจกต์โดยตรง — "ดึงไม่สำเร็จ" ถูกแปลงเป็น "คอนฟิกผิด" ผู้ใช้จะไปแก้ config.json ที่ไม่ได้ผิด (และถ้าแก้ตามคำแนะนำ = ลบเจตนา "ไม่ถือทอง" ทิ้ง) ทั้งที่ความจริงคือ yfinance ล่มชั่วคราว · หน้า Scorecard ตายทั้งหน้า ไม่ได้เห็นแม้แต่คำเตือน "ไม่มีข้อมูล: VOO, SCHD" ที่บรรทัด 4090 เตรียมไว้

**แนวแก้ที่เสนอ**
_fill_without_unset ต้องรู้ว่าตัวเองถูกเรียกด้วย subset: ให้ get_target_weights_with_status รับพารามิเตอร์บอกว่ารายชื่อที่ส่งเข้ามาเป็นชุดย่อยของที่ตั้งไว้หรือไม่ · เมื่อ subset รวมได้ 0 ให้คืน {} หรือโยน exception คนละชนิด (เช่น NoTargetForSubset) พร้อมข้อความว่า "ticker ที่มีข้อมูลรอบนี้ (QQQM, XLV, GLDM) ถูกตั้งเป้าไว้ 0% ทั้งหมด ส่วนตัวที่มีน้ำหนัก (VOO, SCHD) ดึงราคาไม่ได้" · และครอบ dashboard/app.py:4082 ด้วย try เหมือนอีกสองจุด

**พบในรอบ** T2 — ล่ารูที่การแก้รอบนี้สร้างขึ้นเอง (money path + ค่าที่ "มีอยู่แต่ใช้ไม่ได้")

---

## [HIGH] /api/networth/current คืน 500 เพราะอัตราแลกเปลี่ยน ทั้งที่สมุดไม่มี ETF สักตัวและคำตอบไม่ได้ใช้ FX เลย

**ไฟล์** `/home/da00/code/Vaultis/backend/services/networth_service.py:56 (+ /home/da00/code/Vaultis/utils/fx.py:69-88)`

**อาการ**
การแก้ B9 ทำให้ _config_fallback() โยน FxRateUnavailable เมื่อ display.default_fx_rate อยู่นอกช่วง 20–50 แต่ _etf_assets_live() เรียก fx.get_usdthb() แบบไม่มีเงื่อนไข ก่อนจะรู้ด้วยซ้ำว่ามี holding หรือไม่ (บรรทัด 54-56: report = get_holdings(); rate, fx_is_live = fx.get_usdthb()) ⇒ สมุดว่าง = ไม่มีอะไรต้องแปลงเป็นบาท แต่ก็ยังระเบิด และ router บรรทัด 25-26 แปลงเป็น HTTP 500

**หลักฐาน (รันจริง)**
```
$ docker … tests python — stub get_holdings ให้คืน {"holdings": []} + FX สดดึงไม่ได้ + default_fx_rate=3.35 (พิมพ์ตกจุดจาก 33.5):
RAISED FxRateUnavailable : ดึงอัตราแลกเปลี่ยน THB/USD สดไม่ได้ และค่าสำรองใน config.json (display.default_fx_rate = 3.35) อยู่นอกช่วงที่เป็นไปได้ 20–50 บาท/USD — คำนวณมูลค่าเงินบาทต่อไม่ได้

เทียบค่าสำรองปกติ (33.5) ด้วยสมุดว่างชุดเดียวกัน:
etf_status: no_holdings | net: 0.0 | fx: 33.5 False
warnings: ['ยังไม่เคยบันทึก snapshot — ยอดนี้มีเฉพาะ ETF ยังไม่รวมเงินสด/สินทรัพย์อื่น/หนี้สิน']

HEAD ไม่โยน: $ git show HEAD:utils/fx.py → HEAD get_usdthb() with default_fx_rate=3.35 -> FxRate(rate=3.35, is_live=False)
```

**ผลกระทบ**
เงินสด สินทรัพย์นอก ETF และหนี้สินทั้งหมดใน snapshot — ซึ่งเป็นตัวเลขบาทที่บันทึกไว้เองและไม่พึ่ง FX แม้แต่บาทเดียว — หายไปทั้งก้อนพร้อม HTTP 500 · เคสเดียวกันเกิดได้อีกทางเมื่อดึงราคา ETF ไม่ได้เลยและคำตอบมาจาก snapshot ล้วน ๆ (etf_status=from_snapshot) ซึ่งก็ไม่ต้องใช้ FX เหมือนกัน

**แนวแก้ที่เสนอ**
ย้ายการเรียก fx.get_usdthb() ให้อยู่หลังจากรู้ว่ามีแถวที่ต้องแปลงจริง (ถ้า holdings ว่าง คืน _LiveEtf(fx_rate=None, fx_is_live=None) ตามที่ NetWorthResponse.fx_rate ประกาศเป็น float | None ไว้แล้ว) · และเมื่อจำเป็นต้องใช้ FX จริงแล้วมันล้ม ให้จับ FxRateUnavailable ที่ networth_service แล้วคืน 200 พร้อม etf_status="unavailable" + warnings ภาษาไทย แทนที่จะทิ้งทั้งคำตอบเป็น 500

**พบในรอบ** T2 — ล่ารูที่การแก้รอบนี้สร้างขึ้นเอง (money path + ค่าที่ "มีอยู่แต่ใช้ไม่ได้")

---

## [HIGH] กล่อง "ชนะ VOO ไหม" เทียบพอร์ตครึ่งเดียวกับเงา VOO เต็มพอร์ต — และพิมพ์ 0.00 / −100.00% เมื่อดึงราคาไม่ได้เลย

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py:2983-2992`

**อาการ**
`actual_value_usd` นับเฉพาะกองที่มีราคา (`priced`) แต่ `invested_usd` และ `shadow_value` มาจาก `shadow_benchmark(buys, ...)` ซึ่งนับ **ทุกไม้** รวมไม้ของกองที่วันนี้ดึงราคาไม่ได้ ⇒ ทั้ง delta % และ "ส่วนต่าง" เทียบคนละฐาน · และบรรทัด `... if not priced.empty else 0.0` เปลี่ยน "ไม่รู้มูลค่า" ให้เป็น 0.00 บาท ซึ่งเป็นบั๊ก AUDIT C1 ตัวเดิม (พอร์ตโชว์ −100% ปลอม) ที่ยังเหลืออยู่ในกล่องนี้ · caption ตัวเดียวที่พูดถึง QQQM เขียนว่า "%ต่อปีด้านล่างจึงต่ำกว่าความจริง" คือคุ้ม XIRR ข้างล่าง ไม่ได้คุม 3 metric ข้างบน

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app -T tests python - < /tmp/.../t3_proof.py

F1 (QQQM ไม่มีราคา):
  จอ: ["st.c0.metric","พอร์ตจริง (USD)","2,350.00","delta=-22.44%"]
       ["st.c1.metric","ถ้าซื้อ VOO ล้วน (USD)","3,516.67","delta=+16.06%"]
       ["st.c2.metric","ส่วนต่าง","-1,166.67 USD"]
  shadow_invested_usd(ทุกไม้ รวม QQQM) = 3030.0
  shadow_value_usd(เฉพาะไม้ที่มีราคาวันนี้) = 2475.0
  ส่วนต่างที่จอพิมพ์ = -1166.67  ·  ส่วนต่างที่เทียบฐานเดียวกัน = -125.00
  %พอร์ตที่จอพิมพ์ = -22.44%  ·  %ฐานเดียวกัน (API pnl_usd/invested_usd_priced) = +15.76%

F1b (ดึงราคาไม่ได้เลยสักกอง):
  จอ: ["st.c0.metric","พอร์ตจริง (USD)","0.00","delta=-100.00%"]
       ["st.c2.metric","ส่วนต่าง","-3,516.67 USD"]
  ขณะที่ tracker.current_value_thb = "nan" · api.current_value_usd = null · api.pnl_usd = null
  (XIRR ข้างล่างทำถูก: "คำนวณ %ต่อปี (XIRR) ไม่ได้จากกระแสเงินสดปัจจุบัน — ไม่แสดงตัวเลขแทน")

tests/test_dashboard_c2.py เรียก _render_benchmark_section ด้วย _priced_holdings() ทุกเคส — เคสราคาหายไม่มีเทสต์คุม
```

**ผลกระทบ**
หัวข้อ "ชนะ VOO ไหม" คือคำตัดสินที่ผู้ใช้เอาไปตัดสินใจว่าจะเลิก DCA หรือไม่ ตอนราคาบางกองดึงไม่ได้ (เกิดจริงบ่อยจาก yfinance rate limit) จอบอกว่าพอร์ตขาดทุน 22% และแพ้ VOO 1,167 ดอลลาร์ ทั้งที่ตัวเลขเทียบฐานเดียวกันคือ +15.76% และแพ้ 125 ดอลลาร์ (เพี้ยน 9 เท่า) · ตอนราคาหายหมด จอพิมพ์ "0.00 / −100.00%" ซึ่งเป็นการกุตัวเลขตรง ๆ ทั้งที่ทุกชั้นอื่นตอบ null/NaN ถูกต้องแล้ว

**แนวแก้ที่เสนอ**
1) สร้างขา benchmark จากไม้ของกองที่ **มีราคาวันนี้** เท่านั้น (`buys[buys["ticker"].isin(priced["Ticker"])]`) แล้วบอกในบรรทัดใต้กล่องว่าตัดกองไหนออกจากทั้งสองขา — หรือถ้าเลือกไม่ตัด ก็ต้องหาร `actual_pct` ด้วย invested ของเฉพาะกองที่มีราคาและติดป้ายว่า "ส่วนต่าง" เทียบไม่ได้ 2) แทน `else 0.0` ด้วยการหยุดแสดงกล่องนี้ (st.warning "ดึงราคาไม่ได้สักกอง — ยังเทียบ VOO ไม่ได้") ห้ามให้ 0.00/−100% ออกจอ 3) เพิ่มเทสต์เคส unpriced ใน tests/test_dashboard_c2.py

**พบในรอบ** T3 — เส้นทางเงิน end-to-end (tracker → portfolio_service → API → dashboard → pdf_export → benchmark/XIRR → networth)

---

## [HIGH] โหมดแสดงผล USD แปลงต้นทุนบาทย้อนหลังด้วยอัตราวันนี้ — เงินลงทุน/กำไรที่ติดป้าย (USD) ไม่ตรงกับที่ API ตอบ และสามช่องบวกลบกันไม่ลง

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py:3272-3283`

**อาการ**
`_render_portfolio_totals()` เมื่อ `display.currency = USD` เอาตัวเลข **บาท** ทุกช่องมาหารด้วย `today_fx_rate` ตัวเดียว แต่ `invested_thb_all` คือต้นทุนที่คิดด้วยอัตราของ **วันที่ซื้อ** (35.00/36.00/34.50/34.00) การหารด้วยอัตราวันนี้จึงไม่ได้ทั้งดอลลาร์ที่จ่ายจริงและอะไรที่มีความหมาย · ส่วน delta ที่แปะข้าง "กำไร/ขาดทุน (USD)" คือ `total_return_pct` ซึ่งเป็น %ฐานบาท ไม่ใช่ %ฐานดอลลาร์ · caption ใต้กล่องยังอ้างเป็น "บาท" ทั้งที่ metric ทุกช่องเป็น USD จึงใช้กระทบยอดไม่ได้

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app -T tests python - < /tmp/.../t3_proof.py

F2_usd_mode (สมุดเดียวกัน ฉากเดียวกัน):
  จอ(USD): เงินลงทุนรวม (USD) = "3,234.15" · มูลค่าปัจจุบัน (USD) = "2,350.00" ·
           กำไร/ขาดทุน (USD) = "162.00" (delta=7.40%) · ค่าธรรมเนียมรวม (THB) = "157.67"
  API   : invested_usd_all = 3030.0 · invested_usd_priced = 2030.0 ·
          current_value_usd = 2350.0 · pnl_usd = 320.0
  ผลต่าง invested = +204.15 USD (+6.7%)   ผลต่าง pnl = -158.00 USD (-49.4%)
  บนจอเดียวกัน: 2,350.00 - 3,234.15 = -884.15 แต่ช่องกำไร/ขาดทุนเขียน +162.00
  %ที่ถูกต้องฐานดอลลาร์ = 320/2030 = 15.76% แต่ delta บนจอ = 7.40% (ฐานบาท)

การเข้าถึง: dashboard/app.py:898-903 หน้า Settings มี st.radio ["THB","USD"] · utils/config.py:115-116 ยอมรับ USD
tests: grep '_render_portfolio_totals' tests/ → ถูกเรียกด้วย "THB" ทุกเคส (test_dashboard_phase1.py:500,507 · test_dashboard_c2.py:623,629,634,661) โหมด USD ไม่มีเทสต์คุมเลย
```

**ผลกระทบ**
ผู้ใช้ที่กดสลับเป็น USD ในหน้า Settings เห็นเงินลงทุนสูงกว่าจริง 204 ดอลลาร์ และกำไรต่ำกว่าจริงครึ่งหนึ่ง โดยตัวเลขทั้งสามช่องบนแถวเดียวกันบวกลบไม่ลงตัว — เป็นอาการ H9 ตัวเดิม (เลข 3 ตัวบนจอเดียวกันที่ประกอบกลับไม่ได้) ที่ฝั่งบาทถูกแก้ไปแล้วแต่ฝั่งดอลลาร์ยังเหลือ · ถ้าบาทแข็ง/อ่อนแรง ๆ เครื่องหมายกำไรพลิกได้

**แนวแก้ที่เสนอ**
โหมด USD ต้องอ่านตัวเลขฝั่งดอลลาร์จากแหล่งเดียวกับ API (`invested_usd_all` / `invested_usd_priced` / `current_value_usd` / `pnl_usd` ที่ `portfolio_service` คำนวณไว้แล้ว) ไม่ใช่หารบาทด้วยอัตราวันนี้ · delta ต้องเป็น %ฐานเดียวกับตัวเลขในช่อง (pnl_usd/invested_usd_priced) · caption กระทบยอดต้องเปลี่ยนหน่วยตามโหมด · เพิ่มเทสต์เรียก `_render_portfolio_totals(..., "USD", ...)` แล้วยืนยันว่าเลขตรงกับ portfolio_service

**พบในรอบ** T3 — เส้นทางเงิน end-to-end (tracker → portfolio_service → API → dashboard → pdf_export → benchmark/XIRR → networth)

---

## [HIGH] ตาราง Returns พิมพ์ 0.00% แทน NaN เมื่อผู้ให้ข้อมูลหยุดส่งแท่งของ ticker นั้น — ffill ลบ NaN ทิ้งก่อนถึง guard ตัวใหม่

**ไฟล์** `/home/da00/code/Vaultis/analysis/returns.py:104`

**อาการ**
`calculate_period_returns()` ถูกเขียนใหม่ในรอบนี้ให้เรียก `period_return_pct()` ซึ่งมี guard ใหม่ `if not (start > 0) or pd.isna(end): return NaN` — แต่บรรทัดก่อนหน้า `filled = price_df.ffill()` เติมช่องว่างท้ายคอลัมน์ไปแล้ว ราคาสุดท้ายจึงไม่เคยเป็น NaN, guard ตัวใหม่เป็นโค้ดตายบนเส้นทางนี้ และเมื่อช่องว่างยาวกว่าหน้าต่าง ตัวตั้งกับตัวหารกลายเป็นราคาเดียวกันเป๊ะ ⇒ ผลตอบแทน 0.0000% พอดี  ตัว docstring ที่เพิ่งเพิ่มในรอบนี้เขียนตรงข้ามทั้งสองที่: `returns.py:97` "ช่องที่คำนวณไม่ได้คง NaN ไว้" และ `backend/services/etf_service.py:149` "ช่องที่ข้อมูลไม่พอเป็น null (ไม่ใช่ 0 …)"  เทสต์ใหม่ `tests/test_momentum_return.py:249 test_forward_fill_behaviour_unchanged` คุมเฉพาะช่องว่าง**กลางทาง** (`values[-3] = nan` โดยที่ `values[-1]` ยังจริง) ช่องว่าง**ท้ายคอลัมน์** ไม่มีเทสต์คุม

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -e PYTHONPATH=/app -v /home/da00/code/Vaultis:/app -w /app tests python /tmp/probe2.py

  gap | 1M ที่ควรได้ (แท่งจริง) | 1M ที่ได้จริงจากตาราง
    0 |     2.7050% |     2.7050%
    1 |     2.7085% |     2.5762%
    5 |     2.7226% |     2.0610%
   10 |     2.7403% |     1.4169%
   20 |     2.7766% |     0.1288%
   21 |     2.7802% |     0.0000%
   40 |     2.8520% |     0.0000%
   90 |     3.0597% |     0.0000%

เส้นทางคะแนน (ไม่ ffill) ให้คำตอบถูก:  period_return_pct(dead, 21) = nan

payload ที่ /api/etf/returns ตอบจริง (frame_to_dict, X ขาด 40 แท่ง):
  "VOO": {"1M": 2.705023615285529, "3Y": null, …}
  "X":   {"1M": 0.0,               "3Y": null, …}   ← 0.0 ไม่ใช่ null

ช่องว่างท้ายคอลัมน์เกิดได้จริง — data/fetcher.py ใช้ `cleaned = adj_close.dropna(how="all").sort_index()` ซึ่งตัดเฉพาะแถวที่ NaN ทุกคอลัมน์ (ยืนยันด้วย inspect.getsource ในโปรบเดียวกัน)
```

**ผลกระทบ**
"ดึงราคาไม่ได้" ถูกอ่านเป็น "ราคาไม่ขยับเลยทั้งเดือน" บนเส้นทางที่ผู้ใช้เห็น 3 ทาง: `/api/etf/returns`, การ์ด Best/Worst ETF (1Y) ในหน้า Overview (`dashboard/app.py:520`) และหน้า Performance ใน PDF (`utils/pdf_export.py:348`) — ทั้งสามไม่มีธง stale/data_ok กำกับ ค่ากลาง ๆ (gap 10 แท่ง = 1.42% แทน 2.74%) อันตรายกว่าเพราะดูสมเหตุสมผลจนไม่มีใครสงสัย  งานรายสัปดาห์ `main.py` **ไม่โดน** เพราะกรอง `_stale_reason()` ทิ้งก่อนอ่านคอลัมน์ 1M และคะแนน/DCA **ไม่โดน** เพราะ `financial_model` ไม่ ffill (ยืนยันแล้วทั้งคู่)

**แนวแก้ที่เสนอ**
เลิก ffill ในฟังก์ชันนี้ แล้วคิดจากแท่งจริงของแต่ละคอลัมน์เอง — นิยามเดียวกับที่ `main._real_bars` / `etf_service._real_bars` ใช้อยู่แล้ว: `period_return_pct(pd.to_numeric(price_df.iloc[:, i], errors="coerce").dropna(), window)` วิธีนี้แก้ทั้งช่องว่างกลางทาง (เหตุผลเดิมที่ใส่ ffill) และช่องว่างท้ายคอลัมน์พร้อมกัน เพราะดัชนีนับจากแท่งของ ticker นั้นเอง ไม่ยืมแถวของทั้งเฟรม  ถ้าจะคง ffill ไว้ ต้องปิดหน้ากากส่วนหางหลังแท่งจริงตัวสุดท้ายของแต่ละคอลัมน์กลับเป็น NaN ก่อนเรียก `period_return_pct` แล้วเพิ่มเทสต์คู่ขนานกับ `test_forward_fill_behaviour_unchanged` สำหรับช่องว่างท้ายคอลัมน์

**พบในรอบ** T4 — กวาด git diff หาสำนวนที่กฎห้ามซึ่งเพิ่งเพิ่มเข้ามาใหม่

---

## [HIGH] ไฟล์ price alert จริงของผู้ใช้หายไปจากเครื่องระหว่างรอบตรวจนี้ (ชุดเทสต์พ้นข้อสงสัยแล้ว — มีเอเจนต์อื่นรันพร้อมกันอยู่)

**ไฟล์** `/home/da00/code/Vaultis/alerts/data/price_alerts.json`

**อาการ**
รันชุดเทสต์ครั้งแรกโดย mount repo จริง ได้ 1 failed — `TestRealStoreUntouched::test_real_store_is_byte_identical_after_the_suite` ตาข่ายตัวนี้จับ `(st_mtime_ns, st_size)` ตอน import แล้วเทียบซ้ำตอนท้าย มันจะ skip ถ้าไม่มีไฟล์ ⇒ การที่มัน **fail** พิสูจน์ว่าไฟล์ยังอยู่ตอนเริ่มรัน แต่ตอนนี้ไดเรกทอรีเหลือแค่ `.gitkeep`  ผมสร้างสำเนา repo ไว้ที่ /tmp พร้อมไฟล์ล่อ แล้วรันชุดเทสต์ซ้ำ — ไฟล์ล่อรอดครบไบต์ต่อไบต์ และไม่มีเทสต์ไหนล้มเลย ⇒ **ชุดเทสต์ไม่ใช่ตัวลบ** และ `grep -rn "rmtree|unlink|os.remove" tests/` ไม่เจออะไรเลยสักบรรทัด

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app tests pytest -q
  FAILED tests/test_price_alert_store.py::TestRealStoreUntouched::test_real_store_is_byte_identical_after_the_suite
  1 failed, 1296 passed, 5 deselected, 3 xfailed in 67.14s

ls -la --time-style=full-iso alerts/data/
  drwxr-xr-x. 1 da00 da00  16 2026-08-07 20:45:22.365743671 +0700 .
  -rw-r--r--. 1 da00 da00 295 2026-08-02 11:18:52.642233618 +0700 .gitkeep
  (ไม่มี price_alerts.json / .bak / .lock / .tmp.* เหลืออยู่เลย · date = 20:46:54)

รีโปรบนสำเนาที่ /tmp/vrepro + ไฟล์ล่อ:
  docker compose --profile dev run --rm -v /tmp/vrepro:/app tests pytest -q
  1297 passed, 5 deselected, 3 xfailed in 86.67s
  ls -la /tmp/vrepro/alerts/data/ → price_alerts.json 185 bytes ยังอยู่ ไม่ถูกแตะ

docker ps -a --filter name=vaultis
  vaultis-tests-run-ece52fcac00b  Up 56 seconds  "bash -c 'python /mu…"
  vaultis-tests-run-d51d0f134109  Up 56 seconds  "bash -c 'python /mu…"
  vaultis-tests-run-8a151ab5889d  Up 56 seconds  "bash -c 'python /mu…"
  vaultis-tests-run-b1033e799d9a  Up 57 seconds  "bash -c 'python /mu…"
  (สี่ตัวนี้ไม่ใช่ของผม — เอเจนต์อื่นรันกับ working tree เดียวกันอยู่ในนาทีนั้น)

docker compose logs --since 20m scheduler → ไม่มีบรรทัดที่แตะ alert (งาน price alert ตั้งไว้ 09:00/21:00)
```

**ผลกระทบ**
ข้อมูล price alert จริงของผู้ใช้หายไปจากเครื่อง และไฟล์นี้ถูก gitignore ไว้ (ตั้งใจ) จึงไม่มีสำเนาใน git ให้กู้  ต้นเหตุอยู่นอกโค้ดที่ตรวจรอบนี้ — เอเจนต์/โปรเซสอื่นที่รันพร้อมกันบน working tree เดียวกัน ผมรายงานไว้เพราะผู้ใช้ต้องรู้ ไม่ใช่เพราะเป็นข้อบกพร่องของ diff  ผลข้างเคียงตอนนี้: `test_real_store_is_byte_identical_after_the_suite` จะ skip เงียบ ๆ ทุกรอบต่อจากนี้ (ไม่มีไฟล์ให้เทียบ) ตาข่ายจึงหยุดทำงานโดยไม่มีใครรู้

**แนวแก้ที่เสนอ**
1) ผู้ใช้ตรวจว่ามี alert ค้างที่ต้องตั้งใหม่ไหม (ไม่มี .bak เหลือ — ถ้ามี snapshot ของโฮสต์ให้กู้จากตรงนั้น) ห้ามให้ผมสร้างไฟล์เปล่าแทน เพราะ "ไฟล์เปล่า" กับ "ยังไม่เคยตั้ง alert" ปนกันทันที  2) แก้ตาข่ายใน `tests/test_price_alert_store.py:490-495` ให้แยกสองกรณีออกจากกัน: ตอนนี้ถ้าไฟล์หายระหว่างรัน `REAL_STORE.stat()` จะโยน `FileNotFoundError` ซึ่งอ่านเหมือนเทสต์พัง ควรเป็น `assert REAL_STORE.exists(), "คลัง alert จริงหายไประหว่างรันชุดเทสต์"` ก่อนแล้วค่อยเทียบ fingerprint  3) อย่ารันเอเจนต์หลายตัวกับ working tree เดียวกันพร้อมกัน — ผลเทสต์ของแต่ละตัวปนกันแยกไม่ออก

**พบในรอบ** T4 — กวาด git diff หาสำนวนที่กฎห้ามซึ่งเพิ่งเพิ่มเข้ามาใหม่

---

## [HIGH] utils/cache.py — cache hit คืน object ตัวจริง ไม่ใช่สำเนา แล้วไม่มีเทสต์ตัวไหนแดง (ผู้เรียกทำแคชสกปรกข้าม caller ได้)

**ไฟล์** `utils/cache.py`

**อาการ**
เปลี่ยน `return copy.deepcopy(hit[0])` เป็น `return hit[0]` (บรรทัด 96 ในเส้นทาง cache hit) แล้วชุดเทสต์เต็มยังเขียว 1297 passed ทั้งที่ docstring ของไฟล์เองและ CLAUDE.md ประกาศไว้ว่า "คืน 'สำเนา' เสมอ — ผู้เรียกแก้ผลลัพธ์ได้โดยไม่ทำ cache สกปรกข้าม caller" · สาเหตุที่รอด: `tests/test_cache.py:104 test_returned_value_is_a_copy` แก้ค่าที่ได้จาก **miss** (`first = build()` ครั้งแรก) ซึ่งเป็นค่าที่ return ตรงจากฟังก์ชัน ไม่ใช่ของในคลัง (ตอน store มี deepcopy อยู่แล้ว) การแก้ตัวนั้นจึงไม่มีทางแตะคลังไม่ว่าจะมี deepcopy ตอน hit หรือไม่ — เส้นทางที่ deepcopy ทำงานจริง (แก้ค่าที่ได้จาก **hit**) ไม่มีเทสต์ตัวไหนเดินผ่าน · `tests/test_cache.py:115 test_returned_dataframe_is_a_copy` มีรูแบบเดียวกัน

**หลักฐาน (รันจริง)**
```
$ /tmp/mut/tools/run_one.sh M35   # utils/cache.py: return copy.deepcopy(hit[0]) -> return hit[0]
MUTATION_APPLIED M35 utils/cache.py :: return cached object without copy
1297 passed, 5 deselected, 3 xfailed, 21 warnings in 81.65s (0:01:21)
=== EXIT=0 ===        <-- ไม่มีเทสต์ตัวไหนจับได้

$ /tmp/mut/tools/demo_pair.sh M35
# demo: a=fetch('VOO') [miss] ; b=fetch('VOO') [hit] ; b['price']=-999 ; c=fetch('VOO') [hit]
--- M35 BASE (ต้นฉบับ) ---
  calls=1 first=500.0 after_caller_mutation=500.0
  same_object(b is c)=False
--- M35 MUTANT ---
  calls=1 first=500.0 after_caller_mutation=-999.0
  same_object(b is c)=True
```

**ผลกระทบ**
`cache_data_1h` ครอบ `calculate_signal_score` / `dcf_valuation` / `get_macro_data` ซึ่งเป็นค่าที่ไหลเข้าเลขคะแนนและการจัดสรร DCA ถ้ามีผู้เรียกคนใดแก้ dict ที่ได้จาก cache hit (เช่นเติมคีย์ลงในผลคะแนนก่อนส่งขึ้นหน้าจอ) ราคาที่เพี้ยนจะค้างในคลัง 1 ชม. แล้วแจกให้ทุก request ถัดไปโดยไม่มีสัญญาณอะไรเลย — เป็นการกุตัวเลขที่ข้ามผู้เรียก วันนี้ยังไม่มีผู้เรียกที่แก้ผลลัพธ์ตรง ๆ (ai_advisor ใช้ `dict(get_macro_snapshot())` ป้องกันไว้เอง) แต่ invariant ที่เขียนประกาศไว้ไม่มีตาข่ายรับเลย ผู้เรียกรายถัดไปทำพังได้เงียบ ๆ

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ที่แก้ค่าที่ได้จาก **cache hit** ไม่ใช่จาก miss เช่นใน tests/test_cache.py: `first = build(); second = build(); second['items'].append(999); assert build()['items'] == [1, 2]` และเช็ค `build() is not build()` ตรง ๆ · ทำคู่เดียวกันกับ `test_returned_dataframe_is_a_copy` (แก้ค่าใน frame ที่ได้จากครั้งที่สอง) · เส้นทาง `backend/services/cache_service.py` มีเทสต์ครอบเรื่องสำเนาอยู่แล้ว (test_cache_service.py:293/307/372) ให้ยกรูปแบบมาใช้ให้ตรงกัน

**พบในรอบ** T5 — mutation testing 50 จุดบนเส้นทางเงินและโค้ดที่เพิ่งแก้ (สำเนา repo ที่ /tmp ไม่แตะไฟล์ใน repo)

---

## [HIGH] backend/services/rebalance_service.py — ราคา 0 / ติดลบ ถูกรับเป็นราคาจริง แล้วชุดเทสต์ยังเขียวหมด

**ไฟล์** `backend/services/rebalance_service.py`

**อาการ**
ถอดเงื่อนไข `price <= 0` ออกจาก `_usable_price()` (เหลือแค่ `not math.isfinite(price)`) แล้วชุดเทสต์เต็มยังเขียว 1297 passed · เทสต์ที่มีอยู่ใน tests/test_rebalance_missing_price.py ครอบแค่ "ticker หายไปจาก dict" (`prices={}`) และ "ราคาเป็น NaN" (`test_nan_price_counts_as_missing`) ไม่มีเคสไหนส่งราคา `0.0` หรือค่าติดลบเข้าไปเลย ทั้งที่ docstring ของฟังก์ชันเขียนเตือนเรื่อง "ดึงไม่สำเร็จกลายเป็นมูลค่า 0" ไว้เอง

**หลักฐาน (รันจริง)**
```
$ /tmp/mut/tools/run_one.sh M28   # _usable_price: `if not math.isfinite(price) or price <= 0:` -> `if not math.isfinite(price):`
MUTATION_APPLIED M28 backend/services/rebalance_service.py :: accept price <= 0 as usable
1297 passed, 5 deselected, 3 xfailed, 21 warnings in 75.61s (0:01:15)
=== EXIT=0 ===

$ /tmp/mut/tools/demo_pair.sh M28   # holdings VOO 10 + GLDM 20, prices GLDM=0.0, target 50/50
--- M28 BASE (ต้นฉบับ) ---
  _usable_price(price=0.0): None
  _usable_price(price=-3.0): None
  missing_holding_prices(GLDM=0.0): ['GLDM']
  calculate_drift(GLDM=0.0): RAISED ValueError: ดึงราคาไม่สำเร็จ: GLDM — คำนวณ drift ไม่ได้ (ห้ามตีมูลค่าเป็น 0 เพราะจะทำให้ตัวอื่นดูเกินสัดส่วน)
--- M28 MUTANT ---
  _usable_price(price=0.0): 0.0
  _usable_price(price=-3.0): -3.0
  missing_holding_prices(GLDM=0.0): []
  calculate_drift(GLDM=0.0): 0.5
```

**ผลกระทบ**
นี่คือบั๊กเดิมที่ AUDIT เรียกว่า "ราคาหายกลายเป็น 0 แล้วพลิกคำสั่งซื้อเป็นขาย" เป๊ะ ๆ: GLDM ราคา 0 ทำให้มูลค่าของมันหายจากตัวหาร VOO จึงดู overweight 100% (drift 0.5 แทนที่จะ raise) แล้ว `_build_actions` จะสั่งขาย VOO ด้วยเงินจริง · ราคาติดลบยิ่งหนักกว่า เพราะ `shares_delta = usd_amount / price` ได้จำนวนหน่วยติดลบ ตาข่ายเทสต์วันนี้กันได้เฉพาะ NaN กับคีย์ที่หายไป ถ้าแหล่งราคาเปลี่ยน contract มาเป็น "คืน 0 เมื่อดึงไม่ได้" (ซึ่งเป็นสำนวนที่พบบ่อยใน yfinance wrapper) บั๊กจะกลับมาโดยไม่มีเทสต์ตัวไหนแดง

**แนวแก้ที่เสนอ**
เพิ่มเคสใน tests/test_rebalance_missing_price.py ข้าง `test_nan_price_counts_as_missing`: parametrize `[0.0, -0.0, -3.0]` แล้ว assert ว่า `missing_prices == ['GLDM']`, `actions == []`, `needs_rebalance is None` และ `calculate_drift` โยน ValueError · ครอบ `missing_plan_prices` ด้วย (ราคา 0 ของ ETF ที่อยู่ในเป้าแต่ยังไม่ถือ ก็ต้องนับเป็นขาด)

**พบในรอบ** T5 — mutation testing 50 จุดบนเส้นทางเงินและโค้ดที่เพิ่งแก้ (สำเนา repo ที่ /tmp ไม่แตะไฟล์ใน repo)

---

## [HIGH] ETFInfoService._to_float ปล่อย NaN ผ่าน → /api/etf/{symbol} + /api/etf/compare ตอบ 500 เปล่า แล้ว NaN ค้างในแคช 6 ชม.

**ไฟล์** `backend/services/etf_info_service.py:22`

**อาการ**
`_to_float()` เป็นตัวเดียวใน 6 ตัวแปลง float ของโปรเจกต์ที่ไม่กรอง NaN — `float(nan)` สำเร็จ ไม่โยน exception จึงคืน `nan` ออกไปเป็นค่าของ `ETFInfo.ytd_return`/`beta`/`dividend_yield` ฯลฯ พร้อม `data_ok=True` yfinance คืน NaN ในช่องเหล่านี้ได้จริง (ค่าที่มาจาก pandas) เมื่อ FastAPI serialize `response_model=ETFAnalysis` → `json.dumps(allow_nan=False)` โยน `ValueError` **นอก** ตัว handler ⇒ `except Exception` ใน router ดักไม่ทัน ผู้ใช้ได้ `500 Internal Server Error` เปล่า ๆ ไม่มี detail ภาษาไทย และเพราะ `data_ok=True` ตัวกรอง `is_cacheable` ไม่ปฏิเสธ ⇒ ค่า NaN ถูกเก็บใน CacheService นาน `ETF_INFO_TTL` = 6 ชม. endpoint จึงพังค้างทั้ง 6 ชม. โดยไม่ลองดึงใหม่

**หลักฐาน (รันจริง)**
```
รัน `docker compose --profile dev run --rm -v /tmp/vprobe:/app ... tests python /sp/t6_info_nan.py`:
  A) etf_info_service._to_float(float('nan')) = nan
  A) technical_service._scalar_float(float('nan')) = None   ← พี่น้องกันแต่กรอง
  B) ETFInfo.data_ok = True | ytd_return = nan

รันต่อ end-to-end `/sp/t6_e2e_nan.py` (stub `yf.Ticker(...).info` ให้มี `ytdReturn: nan`, ราคา 400 แท่งเพื่อให้ technical ไม่ใช่สาเหตุ):
  GET /api/etf/VOO -> 500
  body: Internal Server Error
  GET /api/etf/compare?symbols=VOO -> 500
  2nd GET /api/etf/VOO -> 500
  cached ETFInfo entry: {'symbol': 'VOO', 'data_ok': True, ..., 'ytd_return': nan, ...}

traceback จริงที่หลุดออกมา: `starlette/responses.py:183 render -> json.dumps -> ValueError: Out of range float values are not JSON compliant: nan`

ไม่มีเทสต์คลุม: `grep -rn "nan\|NaN\|inf" tests/test_etf_info_cache.py` ไม่มีผลลัพธ์
```

**ผลกระทบ**
หน้า ETF รายตัวและหน้าเปรียบเทียบล่มทั้งหน้าเป็นเวลา 6 ชั่วโมง จากช่องข้อมูลเสริมช่องเดียวที่ yfinance ไม่มีค่าให้ — และ 500 เปล่าไม่บอกอะไรเลย ผิดกฎ fail-loud ที่ว่าความล้มเหลวต้องอ่านออก ที่แย่กว่านั้นคือมันขัดกับสถาปัตยกรรม cache ของโปรเจกต์เอง ซึ่งออกแบบมาไม่ให้แคชความล้มเหลว (B5) แต่ NaN แอบเข้าไปได้เพราะติดป้าย data_ok=True

**แนวแก้ที่เสนอ**
ให้ `_to_float` กรองแบบเดียวกับพี่น้องของมัน — หลัง `float(value)` ตรวจ `math.isfinite()` (หรือ `pd.isna`) แล้วคืน `None` เมื่อไม่ใช่ตัวเลขจริง เหมือน `technical_service._scalar_float` และ `utils/pdf_export._to_float` ที่ทำถูกอยู่แล้ว · เพิ่มเทสต์ใน `tests/test_etf_info_cache.py` ที่ป้อน `.info` ที่มี NaN แล้วยืนยันว่า `/api/etf/VOO` ได้ 200 พร้อมช่องนั้นเป็น `null`

**พบในรอบ** T6 — สัญญาข้อมูลระหว่างชั้น (service ↔ router ↔ Pydantic ↔ openapi ↔ JSON ↔ WebSocket)

---

## [HIGH] weight = inf รอดทุกด่านแล้วกลายเป็น NaN → POST /api/analysis/backtest ตอบ 200 พร้อมเส้นมูลค่าแบนราบ = กุผลตอบแทน 0%

**ไฟล์** `portfolio/backtest.py:19`

**อาการ**
`_normalize_weights()` กรองด้วย `normalized_weights > 0` — `inf > 0` เป็น True จึงรอด (ต่างจาก `NaN > 0` ที่เป็น False แล้วถูกดักถูกต้อง) จากนั้น `weight_sum = inf`, `if weight_sum <= 0` เป็น False จึงรอดอีกด่าน สุดท้าย `normalized_weights / weight_sum` = `inf/inf` = **NaN** · ปลายทาง `portfolio_prices.pct_change().fillna(0.0).mul(active_weights, axis=1).sum(axis=1)` ใช้ `Series.sum()` ที่ **skipna=True เป็นค่าเริ่มต้น** แถวที่เป็น NaN ล้วนจึงกลายเป็น **0.0** ไม่ใช่ NaN ⇒ ผลตอบแทนรายวันเป็น 0 ทุกวัน ⇒ `(1+0).cumprod()*10000` = เส้นแบนราบที่ค่าเริ่มต้นเป๊ะ endpoint ตอบ 200 ราวกับเป็นผลลัพธ์ปกติ

**หลักฐาน (รันจริง)**
```
รัน `docker compose --profile dev run --rm -v /tmp/vprobe:/app ... tests pytest -q -s /sp/test_t6_weights.py` (ราคาสังเคราะห์: VOO ไต่จาก 100 → 200 = +100%):
  A) _normalize_weights({'VOO': inf}) -> {'VOO': nan}
  D) distinct Portfolio Value = 1 | first = 10000.0 | last = 10000.0
     (คำตอบที่ถูกควรราว 20000)

ยิงผ่าน API จริง `/sp/t6_backtest_weights.py` บนราคา 10 ปีจริง:
  inf       -> 200 | rows=2512 | first={'Portfolio Value': 10000.0, 'Portfolio Return': 0.0} | last={'Portfolio Value': 10000.0, 'Portfolio Return': 0.0} | distinct values=1
  normal    -> 200 | rows=2512 | last={'Portfolio Value': 41624.91...} | distinct values=2501
  nan       -> 400 {"detail":"...weights ต้องมีค่ามากกว่า 0 อย่างน้อย 1 ตัว"}   ← NaN ดักถูก
  sum_zero  -> 400 (ดักถูก)

Starlette ใช้ `json.loads` ของ Python ซึ่งรับ literal `Infinity` ในเนื้อคำขอ jsonได้
```

**ผลกระทบ**
ตัวเลขบนเส้นทางเงินถูกกุขึ้นมาจากอินพุตขยะ — ผู้ใช้เห็น "พอร์ตนี้ให้ผลตอบแทน 0.00% ตลอด 10 ปี" ทั้งที่ระบบไม่เคยคำนวณอะไรเลย นี่คือรูปแบบเดียวกับที่ CLAUDE.md ห้ามไว้ตรง ๆ (`fillna(0)` บนเส้นทางราคา) เพียงแต่ซ่อนอยู่ใน `skipna` ดีฟอลต์ของ pandas แทนที่จะเขียนออกมาตรง ๆ

**แนวแก้ที่เสนอ**
ใน `_normalize_weights` กรองด้วย "เป็นจำนวนจริงและมากกว่า 0" — `np.isfinite(w) & (w > 0)` แทน `w > 0` เฉย ๆ แล้วโยน ValueError พร้อมชื่อ ticker ที่ค่าผิด · และที่ `portfolio/backtest.py` บรรทัดรวมผลตอบแทน ให้ใส่ `min_count=1` ใน `.sum(axis=1)` เพื่อให้แถวที่ไม่มีค่าจริงเลยเป็น NaN (= ไม่รู้) แทนที่จะเป็น 0.0 (= ไม่ขยับ)

**พบในรอบ** T6 — สัญญาข้อมูลระหว่างชั้น (service ↔ router ↔ Pydantic ↔ openapi ↔ JSON ↔ WebSocket)

---

## [HIGH] target_weights ผิดรูป = แดชบอร์ดตายทั้งใบ และหน้า Settings ที่มีตัวจัดการเฉพาะทางเข้าไม่ถึง

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py:4249`

**อาการ**
`render_dashboard()` เรียก `default_weights = _tracked_target_weights()` **ก่อน** `_render_custom_sidebar()` และก่อน dispatch หน้า ทั้งที่ `default_weights` ถูกใช้แค่ 2 หน้า (Backtest / DCA Simulator) พอ `portfolio.target_weights` ใน config.json ผิดรูป `get_target_weights()` โยน `InvalidTargetWeights` (ของใหม่รอบนี้ — เดิมกลืนค่าผิดเงียบ ๆ) แล้วไปตกที่ `except Exception` ตัวคลุมท้ายไฟล์ ⇒ ทุกหน้าดับพร้อมกัน รวมหน้า News/Price Alerts ที่ CLAUDE.md ประกาศไว้ว่า "ใช้ได้เสมอ" และรวมหน้า Settings ซึ่งเป็น**ทางเดียวในแอปที่จะแก้ค่านั้น** — `_render_target_weights_table()` มีตัวจัดการ `InvalidTargetWeights` พร้อมข้อความไทยที่ดีอยู่แล้ว แต่กลายเป็น dead code

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v $PWD:/app -v /tmp/t7:/probe tests python /probe/probe_dash2.py  (FakeSt ของ tests/test_dashboard_c2.py + config ที่ target_weights = {"VOO": "ห้าสิบ"})
[config พัง] page=Settings       calls=  7  [('error', "เกิดข้อผิดพลาดใน dashboard: portfolio.target_weights[VOO] = 'ห้าสิบ' ไม่ใช่ตัวเลข — ใส่สัดส่วน 0–1 (เช่น 0.35)")]
[config พัง] page=News           calls=  7  [('error', ...เหมือนกัน...)]
[config พัง] page=Price Alerts   calls=  7  [('error', ...เหมือนกัน...)]
มี render_settings_page ถูกเรียกไหม: False

[เรียก _render_target_weights_table ตรง ๆ]
   [error] `portfolio.target_weights` ใน config.json ใช้ไม่ได้: ... — ระบบไม่เดาค่าแทน แก้ไฟล์ให้ถูกต้องแล้วรีเฟรชหน้านี้   ← handler ใช้ได้จริง แต่ไม่มีวันถูกเรียก
```

**ผลกระทบ**
พิมพ์ค่าน้ำหนักผิดใน config.json ครั้งเดียว = ล็อกตัวเองออกจากแดชบอร์ดทั้งหมด ต้องไปแก้ JSON ด้วยมือถึงจะกลับเข้าได้ และหน้าที่ไม่เกี่ยวกับน้ำหนักเป้าหมายเลย (News/Price Alerts/Portfolio) ก็ดับไปด้วยทั้งที่ทำงานได้

**แนวแก้ที่เสนอ**
ย้าย `_tracked_target_weights()` ไปคำนวณแบบ lazy เฉพาะสองกิ่งที่ใช้จริง (`Backtest` / `DCA Simulator`) หรือครอบด้วย try/except `InvalidTargetWeights` ที่ตำแหน่งเดิมแล้วส่ง `default_weights=None` ต่อ พร้อมเรียก `_render_invalid_target_weights(exc)` (มีอยู่แล้วที่บรรทัด ~2921) เพื่อให้ dispatch เดินต่อและผู้ใช้ยังเข้า Settings ไปแก้ได้

**พบในรอบ** T7

---

## [HIGH] /api/portfolio ประกอบตัวเลขจากการดึงราคา 2 รอบที่ไม่เกี่ยวกัน — payload เดียวขัดแย้งกันเองได้

**ไฟล์** `/home/da00/code/Vaultis/backend/services/portfolio_service.py:161`

**อาการ**
`get_portfolio_summary()` เอาฝั่ง USD (`current_value_usd`/`pnl_usd`/`holdings_count`) มาจาก `get_holdings()` → `tracker.get_portfolio_summary()` (ยิง `_get_latest_prices` + `_get_fx_quote` รอบที่ 1) แล้วเอาฝั่ง THB + `missing_prices` + `fx_rate_thb`/`fx_is_live` มาจาก `tracker.get_total_summary()` ซึ่งเรียก `get_portfolio_summary()` ซ้ำอีกรอบ (ยิงราคา+FX รอบที่ 2) รอบนี้เพิ่งเปลี่ยนความหมายให้ "ไม่รู้ = None" (เดิมทั้งสองฝั่งยุบเป็น 0 เหมือนกัน) ⇒ ความไม่ตรงกันที่เคยถูกกลบ กลายเป็นความขัดแย้งที่อ่านออก และ docstring ของฟังก์ชันเองก็ประกาศว่า payload ต้องไม่ขัดกันเอง

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v $PWD:/app -v /tmp/t7:/probe tests pytest -q -s /probe/test_double_fetch.py  (สมุด VOO 10 หน่วย, `_get_latest_prices` คืน {"VOO":450} รอบแรก และ {} รอบสอง = โดน rate limit)
  จำนวนครั้งที่ยิงราคาใน 1 คำขอ /api/portfolio : 2
    holdings_count       = 1
    current_value_usd    = 4500.0      ← รู้
    pnl_usd              = 500.0       ← รู้
    current_value_thb    = None        ← ไม่รู้
    pnl_thb              = None
    return_pct           = None
    missing_prices       = ['VOO']     ← บอกว่าดึงราคา VOO ไม่ได้ ทั้งที่มูลค่า USD ข้างบนคิดจากราคา VOO
[คุมกลุ่ม ราคาเสถียร] ยิงราคา 2 ครั้ง → current_value_usd=4500.0 / current_value_thb=153000.0 / missing_prices=[]
```

**ผลกระทบ**
yfinance ติด rate limit ระหว่างสองรอบ (repo นี้มีประวัติเรื่องนี้จนต้องใส่แคช) ⇒ `/api/portfolio` ตอบมูลค่า USD ที่ดูสมบูรณ์คู่กับ `missing_prices` ที่บอกว่าไม่มีราคา และ `report_service._plain_narrative()` จะพิมพ์ "มูลค่า 4,500.00 USD" (เพราะ `_prices_unknown()` เห็นทั้งสองค่าเป็นตัวเลข จึงไม่เตือน) แล้วต่อท้ายด้วย "⚠️ ดึงราคาไม่ได้: VOO" ในรายงานฉบับเดียวกัน — และตัวเลขชุดนี้ถูกป้อนเข้า `_build_prompt()` ให้ LLM อธิบายด้วย นอกจากนั้นยังเป็นการยิงราคา/FX ซ้ำสองเท่าทุกคำขอ

**แนวแก้ที่เสนอ**
ให้ `tracker` มีจุดคำนวณเดียวต่อคำขอ: เพิ่มฟังก์ชันที่คืนทั้ง holdings และ totals จาก snapshot ราคา+FX ชุดเดียว (หรือให้ `get_total_summary()` รับ DataFrame ที่คำนวณแล้วเข้าไป) แล้วให้ `portfolio_service.get_portfolio_summary()` เรียกครั้งเดียว — ตรึงด้วยเทสต์ที่นับจำนวนครั้งที่ `_get_latest_prices` ถูกเรียกต่อหนึ่งคำขอ

**พบในรอบ** T7

---

## [HIGH] ตาข่าย fail-closed ของ auth ไม่มีเทสต์คุ้มกัน — แก้ _is_local() ให้คืน True กับทุก client แล้วเทสต์ผ่านครบ 1296 ตัว

**ไฟล์** `/home/da00/code/Vaultis/backend/security.py:33-35`

**อาการ**
CLAUDE.md และคอมเมนต์ใน docker-compose.yml สัญญาว่า "ไม่ตั้ง VAULTIS_API_KEY = คำขอจากภายนอกถูกปฏิเสธ 503 (fail closed)" แต่ถ้ามีใครทำ _is_local() พังโดยไม่ตั้งใจ ไม่มีเทสต์ตัวไหนในชุดร้อง — backend ที่ deploy โดยลืมตั้งคีย์จะเปิดสมุดบัญชี/LLM ให้ทุกคนเงียบ ๆ

**หลักฐาน (รันจริง)**
```
มิวแทนต์ M8 บนสำเนา /tmp/mut (baseline สะอาด = 1296 passed):
  แก้ backend/security.py: `return bool(client and client.host in _LOCAL_HOSTS)` -> `return True`
  docker compose --profile dev run --rm -v /tmp/mut:/app tests python /scratch/mutate2.py
  ผล: `1296 passed, 1 skipped, 5 deselected, 3 xfailed, 21 warnings in 58.09s` — ไม่มี FAILED สักบรรทัด (มิวแทนต์รอดชีวิต)

หาสาเหตุ:
  sed -n '30,72p' tests/test_phase3.py -> TestApiKeyGuard มี 5 เทสต์: 4 ตัวตั้ง VAULTIS_API_KEY=secret123 (ยิงเข้าเส้น hmac ซึ่งไม่เรียก _is_local เลย) + 1 ตัว test_local_dev_works_without_key ที่ delenv แล้วคาดหวัง 200 — ซึ่ง `return True` ก็ให้ 200 เหมือนกัน
  grep -rn "503" tests/test_phase3.py -> ไม่มีผลลัพธ์ (ไม่มีเทสต์ไหนตรวจ 503 เลยในไฟล์นี้)
  sed -n '180,215p' tests/test_docs_and_deps.py -> เทสต์ที่ docstring พูดถึง 503 ตรวจแค่ว่า render.yaml *ประกาศ* คีย์ ไม่ได้ตรวจพฤติกรรม runtime

เทียบคู่ควบคุม M11 (require_api_key ปิดการตรวจทั้งหมด): `2 failed` — TestApiKeyGuard::test_guarded_route_rejects_missing_key / _wrong_key จับได้ ⇒ เส้น "มีคีย์" มีตาข่าย เส้น "ไม่มีคีย์ + client ไม่ใช่ localhost" ไม่มี
```

**ผลกระทบ**
ช่องโหว่ที่ AUDIT.md H1 ปิดไปแล้วสามารถถูกเปิดกลับมาโดยไม่มีอะไรร้อง สถานการณ์จริงที่โดน: Docker (คำขอมาจาก bridge IP 172.x) และ Render (IP สาธารณะ) — สองที่ที่ข้อยกเว้น localhost ต้องไม่มีผล ผลคือ POST /api/portfolio, DELETE /api/alerts, /api/ai/* และ slip OCR เปิดให้ทุกคนพร้อมเผา credit Anthropic

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ใน tests/test_phase3.py::TestApiKeyGuard ที่ปลอม client host ให้ไม่ใช่ localhost แล้วยืนยัน 503 เมื่อไม่ตั้งคีย์ เช่น สร้าง TestClient(app, client=("172.18.0.5", 5000)) (httpx/starlette รับพารามิเตอร์ client) หรือ override request.scope["client"] ผ่าน middleware ในแอปทดสอบ แล้ว assert response.status_code == 503 และ assert "VAULTIS_API_KEY" in detail — คู่กับเทสต์เดิม test_local_dev_works_without_key ที่ยืนยัน 200

**พบในรอบ** T8 — ความน่าเชื่อถือของชุดเทสต์

---

## [HIGH] data/fetcher.py: บรรทัด raise PriceDataUnavailableError ที่ CLAUDE.md ระบุชื่อไว้ ไม่มีเทสต์คุ้มกันเลย

**ไฟล์** `/home/da00/code/Vaultis/data/fetcher.py:106-108`

**อาการ**
เปลี่ยน `raise PriceDataUnavailableError(...)` เป็น `return pd.DataFrame()` (คือทำสิ่งที่ CLAUDE.md ห้ามไว้ตรงตัว: "it does not return an empty frame") แล้วเทสต์ผ่านครบ 1296 ตัว ไม่มีตัวไหนร้อง

**หลักฐาน (รันจริง)**
```
มิวแทนต์ M6 บนสำเนา /tmp/mut (baseline สะอาด = 1296 passed):
  ผล: `1296 passed, 1 skipped, 5 deselected, 3 xfailed, 21 warnings in 64.91s` — ไม่มี FAILED (รอดชีวิต)

ยืนยันว่ามิวแทนต์เปลี่ยนพฤติกรรมจริง (สคริปต์ /scratch/confirm_m6.py stub yf.download ให้โยน RuntimeError):
  `มิวแทนต์ M6 คืน: DataFrame empty = True -> ไม่ raise`

ไล่หาผู้คุ้มกันที่ควรจะมี:
  grep -rn "PriceDataUnavailableError" tests/ -> เจอ 6 ไฟล์ รันเฉพาะ 6 ไฟล์นั้น = `115 passed` ทั้งที่มิวแทนต์ติดอยู่
  sed -n '1,60p' tests/test_price_fallback.py -> ไฟล์นี้ทดสอบ data/fallback.py::get_latest_prices_with_fallback ไม่ใช่ data/fetcher.py::fetch_adjusted_close_data
  grep -rn "fetch_adjusted_close_data" tests/ -> ที่เจอทั้งหมด (test_etf_snapshot_stale, test_pdf_export, test_daily_ci_jobs) เป็น monkeypatch.setattr ทับตัวจริงทิ้ง ไม่มีใครเรียกของจริง
  grep -n "fetch_adjusted_close_data|PriceDataUnavailable" tests/test_etf_analysis.py tests/test_screener.py tests/test_backtest.py tests/test_forecast.py -> ไม่มีผลลัพธ์ ⇒ แม้เปิด `-m network` ก็ไม่มีใครคลุม
```

**ผลกระทบ**
fetch_adjusted_close_data เป็นทางเข้าราคาของ main.py, goal_service, pdf_export, ai_advisor, etf_service, market_analysis_service, portfolio/backtest, technical/indicators และ dashboard ถ้ามันเงียบเป็น DataFrame ว่างแทนที่จะ raise กฎข้อ 1 ของโปรเจกต์ (fail loud ห้ามกุตัวเลข) จะพังทั้งระบบพร้อมกันโดยชุดเทสต์ยังเขียว — และเป็นการถดถอยที่โค้ดเคยเป็นมาก่อนจริง ๆ

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ยูนิตใน tests/test_price_fallback.py (หรือไฟล์ใหม่ test_fetcher_fail_loud.py) ที่ monkeypatch `data.fetcher.yf.download` ให้โยนทุกครั้ง + monkeypatch `data.fetcher.time.sleep` เป็น no-op แล้ว `with pytest.raises(PriceDataUnavailableError)` เรียก fetch_adjusted_close_data(["VOO"], years=1) พร้อมยืนยันว่าเรียก download ครบ 3 ครั้ง (นับ call) — ตรงกับสัญญาที่ CLAUDE.md เขียนไว้

**พบในรอบ** T8 — ความน่าเชื่อถือของชุดเทสต์

---

## [HIGH] goal_service กลืน PriceDataUnavailableError แล้วรายงานว่า "ยังไม่มีพอร์ตจริง/ราคา" — Monte Carlo สลับไป preset เงียบ ๆ แล้วแคชความล้มเหลวไว้ 10 นาที

**ไฟล์** `/home/da00/code/Vaultis/backend/services/goal_service.py`

**อาการ**
`real_portfolio_assumptions()` ครอบทั้งบล็อกด้วย `except Exception: result = None` (บรรทัด 57-58) ⇒ `PriceDataUnavailableError` ที่ `data/fetcher.py` ตั้งใจโยนให้ดัง กลายเป็น `None` เงียบ ๆ แล้ว `_build_progress()` ตกไปใช้ preset พร้อมข้อความ `preset โปรไฟล์ ... (ยังไม่มีพอร์ตจริง/ราคา)` ทั้งที่พอร์ตมีอยู่จริงและ Price OK ครบ · ผลลัพธ์ยังถูกแคช 10 นาที (`_real_assumptions_cache`) ⇒ ความล้มเหลวค้าง ไม่ลองใหม่ ขัดกฎ utils/cache.py C1 ที่ทั้งระบบยึด

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app tests python - <<'PY'
(stub get_portfolio_summary = 2 กอง Price OK มูลค่ารวม 150,000 บาท; stub fetch_adjusted_close_data ให้โยน PriceDataUnavailableError("yahoo ล่ม 3 ครั้งติด"))
PY
ผลจริง:
  real_portfolio_assumptions() = None
  assumptions_source = preset โปรไฟล์ aggressive (ยังไม่มีพอร์ตจริง/ราคา)
  assumed_annual_return_pct = 12.0
  probability_of_success = 0.306
  แคชไว้ 10 นาที? cache = None
```

**ผลกระทบ**
ผู้ใช้เห็น "ความน่าจะเป็นถึงเป้าหมาย 30.6%" ที่คิดจากสมมติฐาน preset 12%/15% แทนที่จะเป็น μ/σ ของพอร์ตจริง โดยระบบบอกเหตุผลผิด ("ยังไม่มีพอร์ต") ทั้งที่เหตุจริงคือดึงราคาไม่ได้ — ตรงกับข้อห้าม "ดึงไม่สำเร็จ ≠ ไม่มีข้อมูล" และตัวเลขนี้เข้าหน้า Goals + `/api/goals/{id}/progress` เต็ม ๆ · ยิ่งกว่านั้น `except Exception` ยังกลืน TypeError/KeyError ของบั๊กจริงในโค้ดเดียวกันไปด้วย

**แนวแก้ที่เสนอ**
แยก 3 สถานะออกจากกันแบบเดียวกับ `fetch_*_status` ของ news: (1) ไม่มีพอร์ต → `source="ยังไม่มีพอร์ต"` (2) ดึงราคาไม่สำเร็จ → คืนธง `assumptions_error` ให้ router/dashboard แสดง "ดึงราคาไม่สำเร็จ — ตัวเลขด้านล่างใช้สมมติฐาน preset" (3) สำเร็จ → พอร์ตจริง · จับเฉพาะ `PriceDataUnavailableError` ไม่ใช่ `Exception` · ห้ามแคชกรณี (2) (แคชเฉพาะผลสำเร็จ ตามกฎ cache_data_1h)

**พบในรอบ** T10

---

## [MEDIUM] main._fmt_price ไม่มีเทสต์แตะเลย — เปลี่ยนให้กุราคาเป็น $0.00 ได้โดยชุดเทสต์ 1297 ตัวเขียวหมด

**ไฟล์** `/home/da00/code/Vaultis/main.py`

**อาการ**
`_fmt_price()` คือฟังก์ชันที่ docstring ของมันเองเขียนว่า "ราคาที่อ่านไม่ได้ต้องเป็น ? ไม่ใช่ 0.00 (ห้ามกุตัวเลข)" และมันอยู่ในเส้นทางของ K7 พอดี (พิมพ์รายการ triggered ใน `format_price_alert_report`) แต่ไม่มีเทสต์ไฟล์ไหนอ้างถึงมันเลย กติกาข้อ 1 ของโปรเจกต์จึงไม่มีตาข่ายตรงจุดนี้

**หลักฐาน (รันจริง)**
```
grep -rn '_fmt_price|\$\?' /tmp/t1/repo/tests/ → ไม่มีผลลัพธ์ (ศูนย์บรรทัด)

mutation: `return "$?"` → `return "$0.00"`
$ docker compose --profile dev run --rm -v /tmp/t1/repo:/app tests pytest -q
→ 1297 passed, 5 deselected, 3 xfailed  (ไม่มีเทสต์ไหนแดง)

พิสูจน์ว่าเห็นจริงบนจอ (probe เรียก main.format_price_alert_report ด้วย triggered ที่ target_price=None, current_price='n/a'):
  MUTATED : • VOO above $0.00 (ราคาล่าสุด $0.00)
  PRISTINE: • VOO above $? (ราคาล่าสุด $?)
```

**ผลกระทบ**
alert ที่ราคาในคลังเสีย/หาย จะถูกรายงานเข้า Discord และ stdout ว่าราคาเป้าหมาย $0.00 และราคาล่าสุด $0.00 — เป็นเลขที่ระบบแต่งขึ้นเองบนเส้นทางเงินจริง และรอบถัดไปที่ใครแก้บรรทัดนี้ผิดจะไม่มีอะไรจับได้

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ใน tests/test_price_alert_job_report.py: ป้อน triggered ที่ target_price=None / current_price='n/a' แล้ว assert ว่ารายงานมี '$?' และไม่มี '$0.00' (คู่กับเคสราคาปกติที่ต้องยังพิมพ์ตัวเลขเหมือนเดิม)

**พบในรอบ** T1 — หักล้างการเก็บรู K1–K8 ด้วย mutation testing (คัดลอก repo ไป /tmp/t1/repo แล้วย้อนโค้ดที่นั่น · repo จริงไม่ถูกแตะเลย)

---

## [MEDIUM] K8: หน้าต่าง 12 เดือนของ still_growing ไม่มีเทสต์ — ตัดทิ้งแล้วคำแนะนำพลิกจาก "เพิ่มงบ" เป็น "รีไฟแนนซ์" โดยเทสต์เขียวหมด

**ไฟล์** `/home/da00/code/Vaultis/backend/services/debt_service.py`

**อาการ**
บรรทัด `still_growing = any(months and months[-1] > month - 12 for months in neg_amort_months)` คือหัวใจของ K8 ข้อที่แยก "ดอกเบี้ยเดินเร็วกว่าเงินต้น" ออกจาก "เงินต้นลดช้า" แต่เทสต์ทั้ง 61 ตัวของ debt ทดสอบแค่สองขั้วสุด (0% ไม่มี neg-amort เลย / หนี้โตตลอด) ไม่มีเคสไหนที่ neg-amort เกิดเฉพาะช่วงต้นแล้วหยุด ⇒ เงื่อนไข `months[-1] > month - 12` ไม่ถูกวัดเลย

**หลักฐาน (รันจริง)**
```
mutation: `months and months[-1] > month - 12` → `months`
$ pytest -q tests/test_debt_schema_and_sensitivity.py tests/test_debt_negative_amortization.py → 61 passed
$ pytest -q (ทั้งชุด) → 1297 passed, 5 deselected, 3 xfailed

probe ที่พิสูจน์ว่าเป็นพฤติกรรมจริง (snowball, งบ 20,000; หนี้บ้าน 6,000,000 @3.9% min 5,000 + ผ่อนมือถือ 0% 50,000 min 1,000 — neg-amort เกิดเฉพาะ 4 เดือนแรกตอนงบไปลงกองเล็ก):
  MUTATED : "...เหลือ 5,423,758 บาท หลังผ่านไป 50 ปี — ดอกเบี้ยเดินเร็วกว่าเงินต้นที่จ่ายได้ กรุณาเพิ่มงบหรือรีไฟแนนซ์"
  PRISTINE: "...เหลือ 5,423,758 บาท หลังผ่านไป 50 ปี — งบพอจ่ายดอกเบี้ยไหว แต่เงินต้นลดช้าเกินกว่าจะหมดใน 50 ปี กรุณาเพิ่มงบต่อเดือน"
```

**ผลกระทบ**
ข้อความที่ผู้ใช้ได้รับเป็นคำแนะนำการเงินคนละเรื่องกัน (ไปรีไฟแนนซ์ทั้งที่งบจ่ายดอกไหวอยู่แล้ว) และเป็นเคสที่ K8 อ้างว่าแก้แล้วโดยเฉพาะ — ตอนนี้ไม่มีเทสต์ตรึงไว้

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ในกลุ่ม TestZeroInterestRate/TestSensitivity: ใช้เคส probe ข้างบน (neg-amort เฉพาะเดือนต้น ๆ แล้วชนเพดาน 600 เดือน) assert ว่าข้อความมี 'เงินต้นลดช้า' และไม่มี 'รีไฟแนนซ์'

**พบในรอบ** T1 — หักล้างการเก็บรู K1–K8 ด้วย mutation testing (คัดลอก repo ไป /tmp/t1/repo แล้วย้อนโค้ดที่นั่น · repo จริงไม่ถูกแตะเลย)

---

## [MEDIUM] PDF รายเดือนและรายงาน Telegram ทิ้งคำเตือนสมุดบัญชี 2 ใน 3 ชุด (แถวที่อัตราถูกคำนวณย้อน + แถวที่ยอดเงินขัดกันเอง)

**ไฟล์** `/home/da00/code/Vaultis/utils/pdf_export.py:371-373 · /home/da00/code/Vaultis/backend/services/report_service.py:49-61,304-305`

**อาการ**
`portfolio/tracker.py` ผลิตรายงาน 3 ชุดที่ "ห้ามยุบรวมกัน" (skipped_rows / derived_fx_rows / inconsistent_rows) และเดินทางครบถึง `/api/portfolio` แล้ว แต่ `pdf_export` อ่านแค่ `total_summary.get("skipped_rows")` และ `report_service.get_portfolio_summary()` ใส่ลง dict แค่ `skipped_rows`/`skipped_reason` ⇒ `_plain_narrative()` ไม่มีอะไรจะพิมพ์ · ทั้งสองช่องทางจึงเสนอตัวเลขว่าสะอาด ทั้งที่มีแถวที่ยอดบาทขัดกับ จำนวนหุ้น×ราคา×อัตรา อยู่ในยอดรวม

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app -T tests python - < /tmp/.../t3_proof.py
(สมุดเพิ่ม T8 VOO 2024-11-11 อัตราผิด → inconsistent, T7 SCHD 2025-01-05 fx=0 → derived)

F3_tracker_flags = {"skipped": 0, "inconsistent": 1, "derived": 1}
F3_pdf_warnings = [
  "NOTE: the two Invested rows are different bases and differ by 34,000.00 THB ...",
  "WARNING: current price unavailable for QQQM ...",
  "WARNING: the model evaluated no ETF at all this month ..."]
  → ไม่มีบรรทัดใดพูดถึง VOO 2024-11-11 (ต่างกัน 5.70%) หรือ SCHD 2025-01-05 (fx คำนวณย้อน)
F3_pdf_mentions_derived_fx = false
F3_report_keys = [current_value_usd, holdings_count, invested_usd, missing_prices, pnl_usd,
                  skipped_reason, skipped_rows, top_holdings]   ← ไม่มี inconsistent_/derived_fx_
F3_narrative = [... "พอร์ต: 3 ETF | มูลค่า 3,020.00 USD | กำไร/ขาดทุน +430.00 USD",
                "⚠️ ดึงราคาไม่ได้: QQQM", ...]   ← ไม่มีคำเตือนอีกสองชุด

เทียบกับหน้า Portfolio ที่ทำถูก: dash._render_ledger_reports(total_summary) พิมพ์ครบ 3 ชุด
(st.error+st.warning+st.warning พร้อมข้อความไทยของทั้ง skipped / derived / inconsistent)
```

**ผลกระทบ**
รายงานรายเดือนคือช่องทางเดียวของงานอัตโนมัติ (cron วันที่ 1 → Telegram) และ PDF ถูกเก็บไว้อ่านย้อนหลังโดยไม่มีบริบทหน้าจอ ทั้งสองจึงนำเสนอยอดเงินที่มีแถวน่าสงสัยปนอยู่ว่าเป็นตัวเลขสะอาด ผู้ใช้ไม่มีทางรู้ว่าต้องไปแก้แถวไหนในสมุด — ผิด invariant ที่ tracker.py เขียนไว้เอง ("ทั้งสามชุดคือเตือนคนละความหมาย ห้ามยุบรวมกัน") และกฎ "ตัดข้อมูลทิ้งเงียบ ผิดพอกับกุตัวเลข"

**แนวแก้ที่เสนอ**
report_service.get_portfolio_summary(): ส่งต่อ `derived_fx_rows/derived_fx_reason` และ `inconsistent_rows/inconsistent_reason` จาก `summary` แล้วให้ `_plain_narrative()` + `_build_prompt()` พิมพ์เหมือน `skipped_reason` · pdf_export: อ่านสองคีย์นี้จาก `total_summary` แล้วเพิ่มบล็อก WARNING หน้า 1 แบบเดียวกับ skipped_rows (ข้อความไทยมีพร้อมแล้วจาก tracker แต่ต้องผ่าน `_pdf_text()` เพราะบิลด์ที่ไม่มีฟอนต์ไทย)

**พบในรอบ** T3 — เส้นทางเงิน end-to-end (tracker → portfolio_service → API → dashboard → pdf_export → benchmark/XIRR → networth)

---

## [MEDIUM] PDF รายเดือนไม่บอกเลยว่าตัวเลขบาททั้งหน้าคิดจากอัตราแลกเปลี่ยนสำรองใน config

**ไฟล์** `/home/da00/code/Vaultis/utils/pdf_export.py:371-401`

**อาการ**
`generate_monthly_report()` พิมพ์ Current Value / Profit-Loss / Total Return เป็นบาท ซึ่งคูณด้วยอัตราที่ `tracker._get_fx_quote()` หามาได้ แต่ไม่เคยอ่าน `total_summary["fx_is_live"]` / `["fx_rate_thb"]` เลย ⇒ ไฟล์ที่ได้เหมือนกันทุกตัวอักษรไม่ว่าอัตราจะเป็นค่าสดหรือค่าสำรอง

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app -T tests python - < /tmp/.../t3_proof.py
(สร้าง PDF สองรอบด้วยสมุดเดียวกัน ต่างกันแค่ fx.source_of → True แล้ว False)

F4_pdf_same_bytes_live_vs_fallback = [true, true]   # ทั้งลิสต์ Paragraph และตารางหน้า 1 เหมือนกันเป๊ะ
F4_pdf_any_fx_mention = []                          # ไม่มีย่อหน้าไหนมีคำว่า FX/exchange/อัตรา/fallback
F4_api_fx_is_live = false                           # ขณะที่ API ประกาศชัดว่าเป็นค่าสำรอง
ตารางหน้า 1 รอบค่าสำรอง: Current Value 98,150.00 · Profit/Loss 8,400.00 · Total Return 9.36%

ชั้นอื่นทำถูกหมด: dashboard/app.py:3251-3260 ขึ้น st.warning "อัตราแลกเปลี่ยนที่ใช้แปลงมูลค่าวันนี้เป็น**ค่าสำรอง**"
และ networth_service.py:252-256 ขึ้น "ใช้อัตราแลกเปลี่ยนสำรองจาก config ... ตัวเลขบาทอาจคลาดเคลื่อน"
```

**ผลกระทบ**
ขัดกฎที่ไฟล์นี้เขียนไว้เอง ("ดึงไม่สำเร็จ ≠ ไม่มีข้อมูล ต้องพิมพ์เป็นคำเตือนพร้อมสาเหตุ") และขัด B9 ที่ CLAUDE.md สั่งให้เตือนที่มาของอัตราแบบเดียวกับ missing_prices · PDF เป็นเอกสารที่เก็บไว้อ่านย้อนหลังหลายเดือน ตัวเลขบาทที่คลาดเคลื่อน (วัดได้ −1.39% ณ วันตรวจตาม AUDIT) จะถูกอ่านเป็นของจริงตลอดไปโดยไม่มีอะไรกำกับ

**แนวแก้ที่เสนอ**
อ่าน `total_summary.get("fx_is_live")` ต่อจากบล็อก missing_prices แล้วเติมย่อหน้า `WARNING: THB figures use the fallback FX rate from config (X.XX THB/USD), not a live quote — they can be off by a percent or more.` เมื่อค่าเป็น False และเติมหมายเหตุ "FX source unknown" เมื่อเป็น None (ตอนนี้ตัวเลขบาททุกช่องไม่มีป้ายที่มาเลย)

**พบในรอบ** T3 — เส้นทางเงิน end-to-end (tracker → portfolio_service → API → dashboard → pdf_export → benchmark/XIRR → networth)

---

## [MEDIUM] backend/services/rebalance_service.py — เกณฑ์ "ไม่ต้องทำอะไร" 0.01 USD ไม่มีเทสต์ตรึง ขยายเป็น 100 USD ก็ยังเขียว

**ไฟล์** `backend/services/rebalance_service.py`

**อาการ**
เปลี่ยน `if abs(delta_usd) < 0.01:` ใน `_build_actions()` เป็น `< 100.0` แล้วชุดเทสต์เต็มยังเขียว 1297 passed · ไม่มีเทสต์ไหนวางเคสที่ delta อยู่ใกล้เส้น (ระหว่าง 0.01 ถึง 100 USD) จึงไม่มีอะไรกันไม่ให้เส้นนี้ถูกเลื่อน

**หลักฐาน (รันจริง)**
```
$ /tmp/mut/tools/run_one.sh M30   # `if abs(delta_usd) < 0.01:` -> `if abs(delta_usd) < 100.0:`
MUTATION_APPLIED M30 backend/services/rebalance_service.py :: hold band 0.01 -> 100 USD
1297 passed, 5 deselected, 3 xfailed, 21 warnings in 74.45s (0:01:14)
=== EXIT=0 ===

$ /tmp/mut/tools/demo_pair.sh M30   # VOO 100.05 หน่วย @1000 + SCHD 500 @100, เป้า 2/3 : 1/3, งบ 0
--- M30 BASE (ต้นฉบับ) ---
  _build_actions(delta ~ 33 USD): [('VOO', 'sell', 16.67), ('SCHD', 'buy', 16.67)]
--- M30 MUTANT ---
  _build_actions(delta ~ 33 USD): [('VOO', 'hold', 0.0), ('SCHD', 'hold', 0.0)]
```

**ผลกระทบ**
เส้น 0.01 USD คือจุดที่ตัดสินว่า "ต่างน้อยจนไม่คุ้มทำ" กับ "ต้องซื้อ/ขายจริง" ถ้าเลื่อนขึ้นโดยไม่ตั้งใจ (หรือมีคนแก้เป็นหน่วยบาทแล้วลืมแปลง) แผน rebalance จะรายงาน hold ทั้งพอร์ตทั้งที่มีส่วนต่างจริงเป็นหลักสิบ-หลักร้อยดอลลาร์ ผู้ใช้เห็นว่า "ไม่ต้องทำอะไร" ซึ่งเป็นคำตอบที่ผิด แต่หน้าตาเหมือนคำตอบที่ถูก (ไม่ใช่ error) — ตรงข้ามกับเจตนา fail-loud ของไฟล์นี้

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ boundary ใน tests/test_rebalance_missing_price.py (หรือไฟล์ใหม่ test_rebalance_hold_band.py): delta = 0.005 USD ต้องได้ `hold` + `usd_amount == 0.0` และ delta = 0.02 USD ต้องได้ `buy`/`sell` พร้อม `fee_thb > 0` · ผูกค่าคงที่เป็นชื่อ (เช่น `HOLD_BAND_USD = 0.01`) แล้วให้เทสต์อ้างชื่อนั้น จะได้ไม่มีเลข 0.01 ลอยอยู่กลางฟังก์ชัน

**พบในรอบ** T5 — mutation testing 50 จุดบนเส้นทางเงินและโค้ดที่เพิ่งแก้ (สำเนา repo ที่ /tmp ไม่แตะไฟล์ใน repo)

---

## [MEDIUM] backend/services/rebalance_service.py — กิ่ง `total <= 0` ของ calculate_drift ไม่มีเทสต์เดินผ่าน ใส่ `return 1.0` (บั๊กเดิมที่ AUDIT ปิดไปแล้ว) กลับเข้าไปได้เงียบ ๆ

**ไฟล์** `backend/services/rebalance_service.py`

**อาการ**
แทรก `return 1.0` ก่อนบรรทัด raise ในกิ่ง `if total <= 0:` ของ `calculate_drift()` แล้วชุดเทสต์เต็มยังเขียว 1297 passed · เทสต์ที่ตั้งชื่อว่าครอบเรื่องนี้ (`tests/test_rebalance_missing_price.py:166 test_calculate_drift_raises_instead_of_returning_one`) ส่ง `prices={}` เข้าไป ซึ่งไปโดนกิ่ง **missing_holding_prices** raise ตั้งแต่ต้นฟังก์ชัน ไม่เคยเดินมาถึงกิ่ง `total <= 0` เลย · เทสต์อีกตัว (`test_module_has_no_or_fallback_on_the_value_path`) ตรวจด้วย AST แต่จับเฉพาะสำนวน `x or 1.0` (BoolOp/Or) ไม่จับ `return 1.0` ตรง ๆ

**หลักฐาน (รันจริง)**
```
$ /tmp/mut/tools/run_one.sh M26   # แทรก `return 1.0` ในกิ่ง `if total <= 0:` ของ calculate_drift
MUTATION_APPLIED M26 backend/services/rebalance_service.py :: drift returns 1.0 when total==0 (old bug)
1297 passed, 5 deselected, 3 xfailed, 21 warnings in 74.06s (0:01:14)
=== EXIT=0 ===

$ /tmp/mut/tools/demo_pair.sh M26
--- M26 BASE (ต้นฉบับ) ---
  calculate_drift(holdings=[], target, prices): RAISED ValueError: มูลค่าพอร์ตรวมเป็น 0 — ยังไม่มีหน่วยลงทุนให้เทียบสัดส่วน
  calculate_drift(shares=0 row): RAISED ValueError: มูลค่าพอร์ตรวมเป็น 0 — ยังไม่มีหน่วยลงทุนให้เทียบสัดส่วน
--- M26 MUTANT ---
  calculate_drift(holdings=[], target, prices): 1.0
  calculate_drift(shares=0 row): 1.0
```

**ผลกระทบ**
`max_drift_pct: 100.0` ที่ผลิตจากพอร์ตว่างล้วน ๆ คือบั๊กที่ FIX_PLAN ข้อ 1.3 บอกให้แก้ ตอนนี้แก้แล้วจริงแต่ไม่มีตาข่ายรับ วันนี้ `compute_rebalance()` มีด่าน `if not held:` กันไว้อีกชั้นจึงยังไม่ทะลุถึงผู้ใช้ แต่ `calculate_drift()` เป็นฟังก์ชันสาธารณะที่เทสต์เรียกตรง ๆ อยู่แล้ว ถ้าใครย้าย/ลบด่าน `if not held:` ในอนาคต พอร์ตที่ขายหมด (ทุกแถว shares=0) จะรายงาน "เบี่ยงเบน 100%" แล้วสั่ง action ตามนั้น โดยชุดเทสต์ไม่แดงสักตัว

**แนวแก้ที่เสนอ**
เพิ่มเคสที่เดินเข้ากิ่งนั้นจริงใน TestAllPricesMissing: `calculate_drift([], TARGET, PRICES_FULL)` และ `calculate_drift([{'symbol':'VOO','shares':0.0}], TARGET, PRICES_FULL)` ต้องโยน ValueError ที่มีคำว่า "มูลค่าพอร์ตรวมเป็น 0" (ไม่ใช่ข้อความ "ดึงราคาไม่สำเร็จ" — ต้อง assert ให้แยกสองสาเหตุออกจากกัน) · ขยาย AST check ให้จับ `ast.Return` ที่คืนค่าคงที่ตัวเลขในฟังก์ชันบนเส้นทางมูลค่าด้วย ไม่ใช่แค่ BoolOp/Or

**พบในรอบ** T5 — mutation testing 50 จุดบนเส้นทางเงินและโค้ดที่เพิ่งแก้ (สำเนา repo ที่ /tmp ไม่แตะไฟล์ใน repo)

---

## [MEDIUM] technical/signal_rules.py — เพดาน RSI ของ strong_buy (65) ไม่มีเทสต์ตรึง เลื่อนเป็น 75 ก็ยังเขียว

**ไฟล์** `technical/signal_rules.py`

**อาการ**
เปลี่ยน `rsi_ok = _valid(rsi) and float(rsi) < 65` ใน `overall_signal()` เป็น `< 75` แล้วชุดเทสต์เต็มยังเขียว 1297 passed · น่าสังเกตว่า boundary อื่น ๆ ในไฟล์เดียวกันมีเทสต์ครบหมด (M06 `p >= m200` โดน `test_uptrend_boundary_is_ma200_not_ma50` จับ, M07 `r < RSI_OVERSOLD` โดน `test_zone_at_boundary[30.0-neutral]` จับ, M05 RSI_OVERSOLD 30→35 โดนจับ 9 ตัว) — เหลือเลข 65 ตัวเดียวที่ไม่มีใครดู และมันเป็นเลขชุดที่สองที่ไม่ได้มาจาก RSI_OVERSOLD/RSI_OVERBOUGHT

**หลักฐาน (รันจริง)**
```
$ /tmp/mut/tools/run_one.sh M09   # overall_signal: `float(rsi) < 65` -> `float(rsi) < 75`
MUTATION_APPLIED M09 technical/signal_rules.py :: strong_buy rsi ceiling 65 -> 75
1297 passed, 5 deselected, 3 xfailed, 21 warnings in 82.60s (0:01:22)
=== EXIT=0 ===

$ /tmp/mut/tools/demo_pair.sh M09
--- M09 BASE (ต้นฉบับ) ---
  overall_signal(BULLISH, gc=True, rsi=70): 'buy'
  overall_signal(BULLISH, gc=True, rsi=64): 'strong_buy'
--- M09 MUTANT ---
  overall_signal(BULLISH, gc=True, rsi=70): 'strong_buy'
  overall_signal(BULLISH, gc=True, rsi=64): 'strong_buy'
```

**ผลกระทบ**
RSI 70 คือเส้น overbought กลางของระบบ (RSI_OVERBOUGHT) การที่สัญลักษณ์ที่ร้อนแรงเกินเส้นนั้นได้ป้าย **strong_buy** ขัดกับนโยบายที่เขียนไว้ในหัวไฟล์เอง ("Overbought = ระวังไล่ราคา ไม่ใช่คำสั่งซื้อ") ป้ายนี้ออกไปที่หน้า ETF analysis และเข้าไปในข้อความแจ้งเตือน/prompt ของ AI ผู้ใช้เห็น "strong_buy" ตอนราคาร้อนสุด — ผลกระทบตรงกับพฤติกรรมการซื้อจริง

**แนวแก้ที่เสนอ**
เพิ่ม parametrize boundary ใน tests/test_signal_rules_boundary.py แบบเดียวกับ TestRsiZoneBoundary: `overall_signal(BULLISH, golden_cross=True, rsi=64.9) == 'strong_buy'` และ `rsi=65.0 == 'buy'` และ `rsi=70.0 == 'buy'` · ระหว่างนั้นควรพิจารณายกเลขนี้ขึ้นเป็นค่าคงที่ชื่อ (เช่น `STRONG_BUY_RSI_CEILING = 65.0`) เพราะตอนนี้เป็นเลขลอยชุดที่สองในไฟล์ที่ประกาศตัวเองว่าเป็น single source of truth

**พบในรอบ** T5 — mutation testing 50 จุดบนเส้นทางเงินและโค้ดที่เพิ่งแก้ (สำเนา repo ที่ /tmp ไม่แตะไฟล์ใน repo)

---

## [MEDIUM] _normalize_weights ตัด weight ที่ ≤ 0 ทิ้งเงียบ ๆ แล้ว normalize ใหม่บนเซ็ตย่อย — ผู้ใช้ไม่มีทางรู้ว่ากองที่สั่งไปหายจากพอร์ต

**ไฟล์** `portfolio/backtest.py:25`

**อาการ**
บรรทัด `normalized_weights = normalized_weights[normalized_weights > 0]` ทิ้ง ticker ที่มีน้ำหนักติดลบหรือศูนย์ออกจากผลลัพธ์ทั้งดุ้น โดยไม่มี warning ไม่มีคีย์รายงาน ไม่มี log แล้ว `/ (weight_sum)` ตามด้วย `active_weights / active_weights.sum()` ก็ normalize ที่เหลือให้รวมเป็น 1.0 ⇒ ผลลัพธ์คือ backtest ของ **พอร์ตอื่น** ที่ไม่ใช่พอร์ตที่ผู้ใช้ส่งมา แต่ถูกนำเสนอเป็นคำตอบของคำขอนั้น · ฟังก์ชันเดียวกันนี้ถูกใช้โดย `portfolio/dca.py` ด้วย (ยืนยันจากซอร์ส) จึงกระทบทั้งหน้า Backtest และหน้า DCA Simulator

**หลักฐาน (รันจริง)**
```
รัน `docker compose --profile dev run --rm -v /tmp/vprobe:/app ... tests pytest -q -s /sp/test_t6_weights.py`:
  B) _normalize_weights({'VOO': -1.0, 'SCHD': 2.0}) -> {'SCHD': 1.0}      ← VOO หายไปเงียบ ๆ
  C) _normalize_weights({'VOO': 0.5, 'SCHD': 0.0}) -> {'VOO': 1.0}        ← SCHD หายไปเงียบ ๆ
  F) portfolio/dca.py ใช้ _normalize_weights? True

ยิงผ่าน API จริง `/sp/t6_backtest_weights.py` บนราคา 10 ปีจริง:
  negative  -> 200 | rows=2512 | last={'Portfolio Value': 33179.90040681206} | distinct values=2402
(ตอบ 200 ปกติ ไม่มีช่องไหนบอกว่า VOO ถูกตัดออก)
```

**ผลกระทบ**
ผิดกฎข้อ 2 ของโปรเจกต์โดยตรง — "ตัดข้อมูลทิ้งเงียบ ผิดพอกับกุตัวเลข" ผู้ใช้เปรียบเทียบพอร์ต 2 แบบแล้วเห็นตัวเลขที่ไม่ได้มาจากน้ำหนักที่ตัวเองกรอก การกรอกผิดเครื่องหมาย (-0.4 แทน 0.4) จึงกลายเป็นผลลัพธ์ที่ดูสมเหตุสมผลแทนที่จะเป็น error

**แนวแก้ที่เสนอ**
แยกสองกรณีให้ชัด: น้ำหนัก **ติดลบ** = อินพุตผิด ต้องโยน ValueError พร้อมชื่อ ticker (แพลตฟอร์มนี้เป็น long-only DCA ไม่มี short) · น้ำหนัก **0** = เจตนาจริงของผู้ใช้ ตัดได้ แต่ต้องส่งรายชื่อที่ถูกตัดออกไปกับผลลัพธ์ (เช่นคีย์ `dropped_tickers` แบบเดียวกับ `coverage`/`warning` ที่ `simulate_dca` ทำไว้แล้ว) เพื่อให้หน้าจอแสดงได้

**พบในรอบ** T6 — สัญญาข้อมูลระหว่างชั้น (service ↔ router ↔ Pydantic ↔ openapi ↔ JSON ↔ WebSocket)

---

## [MEDIUM] check_alerts() ยุบ "เครื่องนี้ไม่มีคลัง alert" เข้ากับ "อ่านคลังได้ 0 รายการ" — scheduler พิมพ์ "อ่านคลัง alert ได้ปกติ" ทั้งที่ไม่มีไฟล์คลังอยู่จริง

**ไฟล์** `alerts/price_alert.py:450`

**อาการ**
`_load_alerts()` คืนลิสต์ว่างเมื่อไม่มีไฟล์ (ตั้งใจ) และ `check_alerts()` ตั้ง `store_error` เฉพาะตอนโดน `AlertStoreUnavailable` ⇒ กรณี **ไม่มีไฟล์คลังเลย** ได้ payload หน้าตาเหมือนกรณี "มีไฟล์ อ่านได้ ไม่มี alert" ทุกช่อง (`store_error=False, checked=0, triggered=[], unchecked=[]`) และ payload ไม่มีคีย์ไหนบอกสถานะคลังเลย · `main.format_price_alert_report()` บรรทัด 415-416 จึงตกเข้าสาขาที่พิมพ์ข้อความยืนยันว่า **"(อ่านคลัง alert ได้ปกติ)"** ทั้งที่ไม่เคยมีคลังให้อ่าน — โมดูลมี `get_store_status()` ที่แยก `missing`/`error`/`ok` ไว้ถูกต้องแล้ว แต่ไม่มีใครบนเส้นทางนี้เรียกมัน

**หลักฐาน (รันจริง)**
```
รัน `docker compose --profile dev run --rm -v /tmp/vprobe:/app ... tests pytest -q -s /sp/test_t6_alert_missing.py` (ชี้ ALERTS_PATH ไป path ชั่วคราวที่ไม่มีจริง):
  ไฟล์คลังมีอยู่จริงไหม: False
  get_store_status(): {'status': 'missing', 'pending': None, 'triggered': None, 'error': None}
  check_alerts keys: ['checked', 'daily_discord_result', 'daily_summary', 'store_error', 'success', 'triggered', 'unchecked']
    store_error = False | checked = 0 | triggered = [] | unchecked = []
  บรรทัดที่ main.py จะพิมพ์:
  [price alert] ไม่มี alert ค้างให้ตรวจ (อ่านคลัง alert ได้ปกติ)

ยืนยันบนของจริงที่กำลังรันอยู่: `ls -la alerts/data` มีแต่ `.gitkeep` (ไม่มี price_alerts.json) และ `docker logs vaultis-scheduler --tail 5` แสดงงานรอบ 21:00 พิมพ์บรรทัดเดียวกันเป๊ะ:
  [price alert] ไม่มี alert ค้างให้ตรวจ (อ่านคลัง alert ได้ปกติ)
```

**ผลกระทบ**
CLAUDE.md ระบุเองว่า GitHub Actions มองไม่เห็นไฟล์ alert (ถูก gitignore) ⇒ ทุกรอบบน CI จะพิมพ์ "อ่านคลัง alert ได้ปกติ / ไม่มี alert ค้าง" ซึ่งผู้ใช้อ่านว่า "ตรวจแล้ว ไม่มีอะไรถึงเงื่อนไข" ทั้งที่ความจริงคือ "สภาพแวดล้อมนี้ไม่มีคลังให้ตรวจ" — เป็นรูเดิมของ K7/D1 ที่ปิดไปแค่ 2 ใน 3 สถานะ (`store_error` กับ `unchecked` ปิดแล้ว ส่วน `missing` ยังเปิดอยู่) และ `get_store_status()` ที่เขียนไว้แก้เรื่องนี้โดยเฉพาะกลายเป็นโค้ดที่ไม่มีใครเรียกบนเส้นทางนี้

**แนวแก้ที่เสนอ**
ให้ `check_alerts()` แนบ `store_status` (ผลของ `get_store_status()`) ลงใน dict ที่คืน แล้ว `main.format_price_alert_report()` เพิ่มสาขาสำหรับ `status == "missing"` ที่พิมพ์ว่า "เครื่องนี้ไม่มีไฟล์คลัง alert (<path>) — รอบนี้ไม่ได้ตรวจอะไรเลย ไม่ใช่ 'ไม่มี alert ถึงเงื่อนไข'" · เพิ่ม `store_status` เข้า `_PRICE_ALERT_RESULT_KEYS` (main.py:331) ให้ด่านตรวจสัญญาบังคับว่าต้องมี

**พบในรอบ** T6 — สัญญาข้อมูลระหว่างชั้น (service ↔ router ↔ Pydantic ↔ openapi ↔ JSON ↔ WebSocket)

---

## [MEDIUM] POST /api/backtest ไม่ตรวจรูปแบบ start/end — วันที่ผิดรูปถูกรายงานเป็น 503 "ดึงราคาไม่สำเร็จ" หลังยิงเน็ตซ้ำ 3 ครั้ง

**ไฟล์** `backend/models/backtest_models.py:12`

**อาการ**
`BacktestRequest.start` / `.end` ประกาศเป็น `str` เปล่า ไม่มี validator ไม่มี `format` ⇒ ค่าที่ไม่ใช่วันที่เดินผ่าน Pydantic ไปถึง `BacktestEngine.fetch_data` แล้วไปตายที่ยfinance (`ValueError: time data 'banana' does not match format '%Y-%m-%d'`) ซึ่งถูกนับเป็น "ผลว่าง" retry 3 รอบแล้วโยน `PriceDataUnavailableError` → router แปลงเป็น **503 "ดึงราคา VOO ไม่สำเร็จ"** ตาม policy ที่ไฟล์นั้นเขียนไว้เองว่า 503 = "ปัญหาอยู่ที่ข้อมูลต้นทาง ไม่ใช่บั๊ก ลองใหม่ทีหลังได้" — คำอธิบายที่ผิดความจริง เพราะปัญหาอยู่ที่คำขอของผู้เรียก

**หลักฐาน (รันจริง)**
```
รัน `docker compose --profile dev run --rm -v /tmp/vprobe:/app ... tests python /sp/t6_ws_and_dates.py`:
  POST /api/backtest start='banana' -> 503 {"detail":"ดึงราคา VOO ไม่สำเร็จ: ดึงข้อมูลราคา VOO (banana – banana) ไม่สำเร็จหลังลอง 3 ครั้ง: ผลว่าง"}
  POST /api/backtest start='2026-13-45' -> 503 {"detail":"ดึงราคา VOO ไม่สำเร็จ: ดึงข้อมูลราคา VOO (2026-13-45 – 2026-13-46) ไม่สำเร็จหลังลอง 3 ครั้ง: ผลว่าง"}
(log ระหว่างทางแสดง `1 Failed download: ['VOO']: ValueError("time data 'banana' does not match format '%Y-%m-%d'")` ซ้ำ 3 ครั้งต่อคำขอ)

และ openapi ก็ไม่ได้บอกรูปแบบไว้เลย:
  openapi BacktestRequest.start = {'type': 'string', 'title': 'Start'}
  openapi BacktestRequest.end   = {'type': 'string', 'title': 'End'}
```

**ผลกระทบ**
คนที่อ่าน /docs ไม่มีทางรู้ว่าต้องส่ง YYYY-MM-DD และเมื่อส่งผิดก็ได้คำตอบที่ชี้ไปผิดทิศ ("แหล่งข้อมูลมีปัญหา ลองใหม่ทีหลัง") จึงลองใหม่ซ้ำ ๆ ไม่มีวันสำเร็จ · แต่ละครั้งกินการยิงเน็ตจริง 3 รอบ ซึ่งเป็นเชื้อของ rate-limit ที่ระบบนี้เจ็บมาแล้ว · ขัดกฎของโปรเจกต์เรื่องแยก "ดึงไม่สำเร็จ" ออกจากสาเหตุอื่น (ที่นี่ผิดทิศตรงข้าม: อินพุตผิดถูกเล่าเป็นความล้มเหลวของแหล่งข้อมูล)

**แนวแก้ที่เสนอ**
เปลี่ยน `start`/`end` เป็น `datetime.date` ใน `BacktestRequest` (Pydantic จะให้ 422 พร้อมชี้ฟิลด์ และ openapi จะประกาศ `format: date` ให้เอง) หรือถ้าต้องคง `str` ให้ใส่ `field_validator` ที่ `date.fromisoformat()` + ตรวจ `start <= end` แบบเดียวกับที่ `SnapshotRequest._check_snapshot_date` ใน `networth_models.py` ทำไว้แล้ว — จะได้มีนิยามการตรวจวันที่แบบเดียวกันทั้งระบบ

**พบในรอบ** T6 — สัญญาข้อมูลระหว่างชั้น (service ↔ router ↔ Pydantic ↔ openapi ↔ JSON ↔ WebSocket)

---

## [MEDIUM] ETF ที่เป้าหมายถูกตั้งเป็น 0% หายจากแผน DCA เงียบ ๆ ทั้งที่ targets.py เขียนเหตุผลไว้ให้แล้ว

**ไฟล์** `/home/da00/code/Vaultis/analysis/financial_model.py:473`

**อาการ**
`get_target_weights_with_status()` (ของใหม่รอบนี้) คืน `notes` ภาษาไทยบอกว่าทำไม ticker ถึงได้ 0% แต่ `calculate_allocation()` เรียกผ่าน `get_target_weights()` ซึ่งทิ้ง `notes` ทิ้ง แล้ว `if base <= 0: continue` ตัด ticker นั้นออกจาก dict ผลลัพธ์โดยไม่เหลือร่องรอย ปลายทางบนหน้า Scorecard จึงพิมพ์คำโปรยยืนยันตรงกันข้าม

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v $PWD:/app -v /tmp/t7:/probe tests pytest -q -s /probe/test_zero_target.py  (ติดตาม 5 กอง, ตั้ง target_weights = {"VOO":0.4,"SCHD":0.3,"QQQM":0.3})
[targets] weights = {'VOO': 0.4, 'SCHD': 0.3, 'QQQM': 0.3, 'XLV': 0.0, 'GLDM': 0.0}
[targets] notes   = ['น้ำหนักที่ตั้งไว้ใช้ครบ 100% แล้ว — XLV, GLDM จึงได้ 0% ถ้าต้องการให้ถือด้วย ให้ลดน้ำหนักตัวอื่นลง']
[calculate_allocation] ได้เงิน: {'VOO': 2000, 'SCHD': 1500, 'QQQM': 1500}
[calculate_allocation] หายไปจากแผน: ['XLV', 'GLDM']
[calculate_allocation] มีคีย์บอกเหตุผลไหม: ['amount_thb','group','percent','score','target_percent','tilt']  ← ไม่มีช่องเหตุผลเลย
และ dashboard/app.py:4087 พิมพ์ไม่มีเงื่อนไขว่า "ซื้อทุกตัวที่มีข้อมูลตามแผน DCA — ... ไม่มีการเลือกตัวเดียวหรือตัดตัวไหนออก" (คำเตือน `target_without_money` ที่บรรทัด ~2893 กรอง `w > 0` จึงไม่ครอบกรณีนี้)
```

**ผลกระทบ**
ผู้ใช้ที่ตั้งน้ำหนักเองครบ 100% ให้บางกอง จะเห็นแผนเดือนนี้ไม่มี XLV/GLDM คู่กับประโยคที่ยืนยันว่า "ไม่มีการตัดตัวไหนออก" — หน้าจอพูดสิ่งที่ตัวเลขไม่รองรับ และเหตุผลที่ระบบรู้อยู่แล้วถูกทิ้งกลางทาง

**แนวแก้ที่เสนอ**
ให้ `calculate_allocation()` เรียก `get_target_weights_with_status()` แล้วส่ง `notes` + รายชื่อ ticker ที่เป้า = 0 กลับออกมากับผลลัพธ์ (เช่นคีย์ `zero_target_tickers` / `target_notes`) แล้วให้หน้า Scorecard แสดงแทนที่จะพิมพ์คำโปรยแบบไม่มีเงื่อนไข

**พบในรอบ** T7

---

## [MEDIUM] ผลลัพธ์ check_alerts() ที่ผิดสัญญาถูกแปลงเป็น "ตรวจแล้วไม่มีอะไร" ทั้งฝั่ง API และหน้าจอ (main.py กันไว้ที่เดียว)

**ไฟล์** `/home/da00/code/Vaultis/backend/services/alert_service.py:43`

**อาการ**
`main.py` มี `_price_alert_contract_error()` ที่ประกาศชัดว่าห้ามใช้ `result.get("checked", 0)` กับคีย์ที่สัญญาบอกว่ามีเสมอ เพราะ "คีย์หายแล้วกลายเป็น 0 อ่านออกมาเป็น 'ตรวจแล้วไม่มีอะไร' ซึ่งเป็นการกุข้อสรุป" แต่ `alert_service.check_alerts()` ใช้สำนวนนั้นครบทุกคีย์ (`.get("checked", 0)`, `.get("unchecked", [])`, `bool(result.get("store_error", False))`) และ `dashboard/app.py:_render_alert_check_result()` ก็อ่านด้วย `.get()` แบบเดียวกัน

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v $PWD:/app -v /tmp/t7:/probe tests pytest -q -s /probe/test_alert_contract.py  (stub `price_alert.check_alerts` ให้คืน {"success": True, "triggered": []} = ขาด checked/unchecked/store_error)
[main.py] 🚨 [price alert] ผลลัพธ์จาก check_alerts() ผิดสัญญา — ขาดคีย์ store_error, checked, unchecked
[/api/alerts/check] 200 {"data":{"success":true,"checked":0,"triggered":[],"unchecked":[],"store_error":false,"error":null,"daily_summary":""}}

docker ... python /probe/probe_dash_alert.py  (เรียก app._render_alert_check_result({"success": True, "triggered": []}))
  [info] ยังไม่มี Alert ที่ถึงเงื่อนไข
```

**ผลกระทบ**
ถ้า `price_alert.check_alerts()` เสียสัญญา (เช่นเพิ่มเส้นทาง return ใหม่ที่ลืมคีย์ หรือ refactor ในอนาคต) scheduler จะโวยถูกต้อง แต่ปุ่ม "ตรวจ Alert ตอนนี้" บนแดชบอร์ดและ `POST /api/alerts/check` จะยืนยันกับผู้ใช้ว่า "ยังไม่มี Alert ที่ถึงเงื่อนไข" ทั้งที่ระบบไม่ได้ตรวจอะไรเลย — คือความล้มเหลวที่ถูกอ่านเป็นคำยืนยัน

**แนวแก้ที่เสนอ**
ย้าย `_price_alert_contract_error()` จาก main.py ไปไว้ข้าง ๆ `alerts/price_alert.py` (แหล่งของสัญญา) แล้วให้ทั้ง `alert_service.check_alerts()` และ `dashboard._render_alert_check_result()` เรียกใช้: คีย์ไม่ครบ = โยน/แสดง "สรุปสถานะไม่ได้" ห้ามเติมค่าดีฟอลต์

**พบในรอบ** T7

---

## [MEDIUM] goal_service กลืน FxRateUnavailable/ราคาพัง แล้วรายงานสาเหตุผิดว่า "ยังไม่มีพอร์ตจริง/ราคา"

**ไฟล์** `/home/da00/code/Vaultis/backend/services/goal_service.py:57`

**อาการ**
`real_portfolio_assumptions()` ครอบทั้งบล็อกด้วย `except Exception: result = None` ซึ่งเดิมครอบแค่ "ไม่มีพอร์ต/ดึงราคาไม่ได้" แต่ตอนนี้ `tracker.get_portfolio_summary()` มีเส้นทางโยนใหม่ (`FxRateUnavailable` จาก `utils/fx` เมื่อดึงอัตราสดไม่ได้และค่าสำรองใน config อยู่นอกช่วง 20–50) ทุกกรณีจึงยุบเป็น `None` เหมือนกันหมด แล้วผู้เรียกที่บรรทัด 180 ติดป้ายว่า `"preset โปรไฟล์ {risk_profile} (ยังไม่มีพอร์ตจริง/ราคา)"

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v $PWD:/app -v /tmp/t7:/probe tests pytest -q -s /probe/test_report_probe.py::test_goal_service_swallows_fx_failure  (สมุดมี VOO+SCHD จริง, ราคาปกติ, บังคับ `fx._fetch_live -> None` + `default_fx_rate = 900.0`)
[goal_service.real_portfolio_assumptions เมื่อ FX พัง] -> None
เทียบกับ probe เส้นทางเดียวกันที่พิสูจน์ว่าต้นทางดังจริง:
  tracker.get_portfolio_summary()  -> FxRateUnavailable: ดึงอัตราแลกเปลี่ยน THB/USD สดไม่ได้ และค่าสำรองใน config.json (display.default_fx_rate = 900.0) อยู่นอกช่วงที่ใช้ได้ ...
```

**ผลกระทบ**
หน้า Goals จะคำนวณความน่าจะเป็นถึงเป้าหมายด้วยสมมติฐาน preset แล้วบอกผู้ใช้ว่าเหตุผลคือ "ยังไม่มีพอร์ตจริง/ราคา" ทั้งที่พอร์ตมีอยู่และราคาก็ดึงได้ ปัญหาจริงคืออัตราแลกเปลี่ยนที่ตั้งไว้ผิด — ผู้ใช้จึงไปแก้ผิดที่ ("ดึงไม่สำเร็จ" ถูกอ่านเป็น "ไม่มีข้อมูล")

**แนวแก้ที่เสนอ**
แยกกรณีในบล็อก try: `FxRateUnavailable` / `PriceDataUnavailableError` ต้องคืนเหตุผลของตัวเอง (เช่น `{"source": ..., "error": str(exc)}` หรือ raise ต่อ) แล้วให้ `_build_progress()` ติดป้าย `assumptions_source` ตามสาเหตุจริง ห้ามยุบทุก exception เป็น "ยังไม่มีพอร์ต"

**พบในรอบ** T7

---

## [MEDIUM] xfail 3 ตัวใน screener ยัง "ควรเป็นแดง" อยู่ (บั๊กยังไม่ถูกแก้) และช่อง errors ที่เพิ่มมาก็ไม่ได้ครอบเคสนี้ ต่างจากที่เหตุผลใน xfail เขียนไว้

**ไฟล์** `/home/da00/code/Vaultis/backend/screener/engine.py:102,154-157`

**อาการ**
คำถามของรอบนี้คือ "xfail ที่เหลือควรเขียวได้หรือยัง" — คำตอบคือยัง บั๊กยังเปิดอยู่จริง และหนักกว่าที่เหตุผใน marker บอก: พรีเซ็ตที่พิมพ์ชื่อ field ผิดไม่ได้แค่ "ไม่มีสัญญาณ" แต่ช่อง errors (ที่สาย B6 เพิ่มมาเพื่อแยก "ตรวจไม่ได้" ออกจาก "ไม่มีสัญญาณ") ก็ว่างเปล่าด้วย ⇒ ไม่มีสัญญาณใด ๆ บอกผู้ใช้ว่าพรีเซ็ตพัง

**หลักฐาน (รันจริง)**
```
ยืนยันว่ายัง xfail จริง ไม่ใช่ XPASS ที่ค้าง marker:
  docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app tests pytest -q -rsxX tests/test_screener_engine.py tests/test_price_alert_store.py
  -> XFAIL ...::test_unknown_field_raises / test_unknown_operator_raises / test_unknown_logic_raises  (`55 passed, 1 skipped, 3 xfailed`)

ยืนยันบั๊กในโค้ดจริง (สคริปต์ /scratch/screener_bug.py รันในคอนเทนเนอร์ กับ ScreenerEngine ตัวจริง):
  A) preset ที่พิมพ์ชื่อ field ผิด ('price_vs_ma_200' แทน 'price_vs_ma200') -> results: []  errors: []
  B) logic='XOR' (สะกดผิด) กฎผ่าน 1/2 -> ติดสัญญาณ: ['VOO']  (AND ต้องได้ [])

ต้นเหตุในซอร์ส:
  backend/screener/engine.py:102  `return False`  (ท้าย _evaluate_rule — field/operator ที่ไม่รู้จักตกมาที่นี่)
  backend/screener/engine.py:154-157  `if preset.logic == "AND": ... else: passed = any(...)`  (อะไรก็ตามที่ไม่ใช่ "AND" = OR)
```

**ผลกระทบ**
screener รัน 07:00 ทุกวันแล้วส่ง Telegram ถ้ามีสัญญาณ: (ก) พิมพ์ชื่อ field ผิดครั้งเดียว = พรีเซ็ตนั้นเงียบตลอดกาลและผู้ใช้อ่านว่า "ไม่มีอะไรต้องทำ" — ซึ่งเป็นอาการเดียวกับที่ FIX_PLAN.md 2.1 ระบุว่าห้ามเกิด (ข) สะกด logic ผิด = พรีเซ็ตกลับความหมายจาก AND เป็น OR ทั้งใบ ยิงสัญญาณซื้อจากกฎที่ผ่านแค่ข้อเดียว นอกจากนี้ marker ใช้ strict=False ทั้ง 3 ตัว ⇒ วันที่แก้บั๊กสำเร็จ pytest จะรายงาน XPASS ซึ่งไม่ทำให้ชุดเทสต์แดง ไม่มีใครรู้ว่าถึงเวลาถอด marker แล้ว

**แนวแก้ที่เสนอ**
แก้ engine.py ให้ `_evaluate_rule` โยน ValueError เมื่อ field หรือ operator ไม่รู้จัก (แทน `return False` บรรทัด 102) และให้ `run()` โยน ValueError เมื่อ preset.logic ไม่ใช่ AND/OR (แทนการตีความ else เป็น OR) — จากนั้นถอด @pytest.mark.xfail ทั้ง 3 ตัวออกให้เป็นเทสต์จริง ถ้ายังไม่พร้อมแก้ในรอบนี้ ให้เปลี่ยนเป็น strict=True อย่างน้อยชุดเทสต์จะแดงทันทีที่บั๊กหาย บังคับให้ถอด marker

**พบในรอบ** T8 — ความน่าเชื่อถือของชุดเทสต์

---

## [MEDIUM] แถบราคาเรียลไทม์บน dashboard ตายสนิทใน Docker — BACKEND_URL ที่เป็นชื่อโฮสต์ภายในถูกยัดเป็น WebSocket URL ฝั่งเบราว์เซอร์

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py:425-430 (+ /home/da00/code/Vaultis/docker-compose.yml:28)`

**อาการ**
`_ws_prices_url()` แปลง `BACKEND_URL` เป็น ws:// แล้วส่งเข้า `new WebSocket(...)` ที่รันใน **เบราว์เซอร์ของผู้ใช้** แต่ docker-compose.yml:28 ตั้ง `BACKEND_URL: http://backend:8000` ซึ่งเป็นชื่อ DNS ภายในเครือข่าย Docker เบราว์เซอร์บนโฮสต์จึง resolve ไม่ได้ ตัวแปรทางออก `VAULTIS_WS_URL` มีในโค้ดแต่ไม่ได้ตั้งไว้ที่ไหนเลย (ไม่มีใน docker-compose.yml และไม่มีใน .env.example)

**หลักฐาน (รันจริง)**
```
รันเบราว์เซอร์จริง (Chromium 1217 ผ่าน Playwright) โหลด http://127.0.0.1:8501/ แล้วอ่าน console:
  console errors: ["WebSocket connection to 'ws://backend:8000/ws/prices' failed: Error in connection establishment: net::ERR_NAME_NOT_RESOLVED"]
สกรีนช็อตหน้า Overview แสดงแถบ ticker ว่า: "VOO ⚠️ ดึงไม่ได้ (WS error) | SCHD ⚠️ ดึงไม่ได้ (WS error) | QQQM ⚠️ ดึงไม่ได้ (WS error) | XLV ⚠️ ดึงไม่ได้ (WS error) | GLDM ⚠️ ดึงไม่ได้ (WS error) | ยังไม่ได้รับข้อมูล · การเชื่อมต่อหลุด"
ยืนยัน env จริงในคอนเทนเนอร์:
  $ docker compose exec -T dashboard sh -lc 'echo "BACKEND_URL=$BACKEND_URL"; echo "VAULTIS_WS_URL=[$VAULTIS_WS_URL]"'
  BACKEND_URL=http://backend:8000
  VAULTIS_WS_URL=[]
  $ grep -n "VAULTIS_WS_URL" docker-compose.yml .env.example README.md   → ไม่พบสักบรรทัด
หมายเหตุ: backend เองไม่ได้พัง — ยิง WebSocket จากในเครือข่าย Docker ได้ปกติ (รับ 3 เฟรม ราคาครบ 5 ตัว อัปเดตทุก ~31 วิ)
```

**ผลกระทบ**
ฟีเจอร์ราคาเรียลไทม์ใช้ไม่ได้เลยในการรันแบบ Docker ซึ่งเป็นวิธีรันหลักตาม CLAUDE.md ผู้ใช้เห็น ⚠️ ทั้ง 5 ตัวตลอดเวลา ข้อดีคือ **fail loud ถูกต้อง** — `ws.onerror` เขียนทับเป็น "ดึงไม่ได้ (WS error)" ไม่ได้แสดงราคาค้างหรือราคาปลอม จึงไม่ละเมิดกฎห้ามกุตัวเลข แต่ฟีเจอร์เสียจริงและเสียตั้งแต่ค่าเริ่มต้น

**แนวแก้ที่เสนอ**
ใน docker-compose.yml เพิ่ม `VAULTIS_WS_URL: ws://127.0.0.1:8000/ws/prices` ให้เฉพาะ service `dashboard` (ห้ามใช้ค่าเดียวกับ BACKEND_URL เพราะคนละมุมมองเครือข่าย: BACKEND_URL ใช้จากในคอนเทนเนอร์ ส่วน WS URL ใช้จากเบราว์เซอร์) และเพิ่ม `VAULTIS_WS_URL` พร้อมคำอธิบายความต่างนี้ลง .env.example + ตาราง Environment Variables ใน CLAUDE.md

**พบในรอบ** T9 — integration test against the freshly-built containers (read-only; no repo files modified)

---

## [MEDIUM] กราฟ 10 อันใน 6 หน้าขึ้นคำว่า "undefined" เป็นชื่อกราฟ — _apply_plotly_dark_theme สร้าง title ที่ไม่มี text

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py:336-347 (_apply_plotly_dark_theme, บรรทัด 341)`

**อาการ**
`fig.update_layout(title_font=dict(...))` ทำให้ plotly สร้าง `layout.title` ขึ้นมาโดยมีแต่คีย์ `font` ไม่มีคีย์ `text` เมื่อ plotly.js เรนเดอร์ title ที่ `text === undefined` มันพิมพ์สตริง "undefined" ออกมาเป็นหัวกราฟ ทุกกราฟที่ผ่านฟังก์ชันนี้โดยไม่ตั้งชื่อของตัวเองจึงโดนหมด

**หลักฐาน (รันจริง)**
```
พิสูจน์สาเหตุในคอนเทนเนอร์:
  $ docker compose exec -T dashboard python  (px.line ธรรมดา แล้วค่อย update_layout(title_font=...))
  BEFORE theme -> layout.title = layout.Title()
  BEFORE 'title' in json: False
  AFTER  title_font -> layout.title = layout.Title({'font': {'color': '#fff', 'family': 'Inter'}})
  AFTER  layout.title in JSON: {"font": {"color": "#fff", "family": "Inter"}}
  AFTER  'text' key present: False
นับของจริงบนเบราว์เซอร์ (querySelectorAll('.gtitle')):
  Overview           charts=2 gtitle_undefined=2  titles=["undefined","undefined"]
  Scorecard          charts=2 gtitle_undefined=1  titles=["น้ำหนักเดือนนี้ (บาท)","undefined"]
  Technical Signals  charts=4 gtitle_undefined=4  titles=["undefined","undefined","undefined","undefined"]
  Correlation        charts=2 gtitle_undefined=2  titles=["undefined","undefined"]
  DCF Analysis       charts=2 gtitle_undefined=1  titles=["VOO Score Breakdown","undefined"]
  TOTAL undefined chart titles across pages: 10
สกรีนช็อตหน้า Technical Signals เห็นคำว่า "undefined" ตัวหนาอยู่เหนือกราฟแท่งเทียน VOO ชัดเจน
```

**ผลกระทบ**
ความน่าเชื่อถือของหน้าจอที่ใช้ตัดสินใจลงเงินจริง — หน้าแรก (Overview) และหน้า Technical Signals ซึ่งเป็นหน้าหลักขึ้นคำว่า "undefined" เป็นหัวกราฟ ไม่กระทบตัวเลข (ข้อมูลในกราฟถูกต้อง) แต่ดูเหมือนหน้าจอพัง

**แนวแก้ที่เสนอ**
ใน `_apply_plotly_dark_theme` อย่าเซ็ต `title_font` เดี่ยว ๆ ให้เซ็ตพร้อม text ที่มีอยู่เดิมแทน เช่น `fig.update_layout(title=dict(text=fig.layout.title.text or "", font=dict(color=..., family=...)))` หรือเซ็ต `title_font` ก็ต่อเมื่อ `fig.layout.title.text` ไม่ว่าง

**พบในรอบ** T9 — integration test against the freshly-built containers (read-only; no repo files modified)

---

## [MEDIUM] log ระดับ INFO ของแอปหายทั้งหมดใน Docker — สรุปงาน screener รายวันรวมถึงจำนวน "ตรวจไม่ได้" ไม่เคยโผล่ใน log

**ไฟล์** `/home/da00/code/Vaultis/backend/main.py (ไม่มีการตั้งค่า logging เลย) + /home/da00/code/Vaultis/backend/screener/engine.py:177-182`

**อาการ**
backend ไม่เรียก `logging.basicConfig`/`dictConfig` ที่ใดเลย และ docker-compose.yml:86 รัน uvicorn โดยไม่ส่ง `--log-level` uvicorn ตั้งค่าเฉพาะ logger ชื่อ `uvicorn*` เท่านั้น logger ของแอปจึง fall through ไปที่ root ซึ่งมีแต่ lastResort handler ระดับ WARNING ผลคือ `logger.info` ทุกจุดถูกทิ้งเงียบ รวมถึงบรรทัดสรุปของ screener ที่ตั้งใจเขียนไว้เพื่อรายงานจำนวนสัญลักษณ์ที่ "ตรวจไม่ได้"

**หลักฐาน (รันจริง)**
```
หลังยิง /api/screener/custom ไป 2 ครั้ง (ครั้งหนึ่งมี ticker ปลอมที่ล้มเหลวจริง):
  $ docker compose logs --no-color backend | grep -c 'Starting screener run'   → 0
  $ docker compose logs --no-color backend | grep -c 'Screener run complete'    → 0
  $ docker compose logs --no-color backend | grep -c 'ปฏิเสธคำขอ'                → 13   (WARNING ผ่าน)
  $ docker compose logs --no-color backend | grep -c 'scheduler started'         → 0   (backend/main.py:75)
  $ grep -rn 'basicConfig|dictConfig|setLevel' backend/main.py backend/*.py utils/*.py → ไม่พบ
ยืนยันว่า ERROR ผ่านได้: บรรทัด '[ZZZZ_NOT_A_TICKER] screener error: ...' + traceback ปรากฏใน log จริง
```

**ผลกระทบ**
งาน screener 07:00 ที่รันอัตโนมัติจะไม่ทิ้งร่องรอยใด ๆ ใน log เลยถ้าไม่มีตัวไหนล้มเหลว — แยกไม่ออกระหว่าง "งานรันแล้วไม่มีสัญญาณ" กับ "งานไม่ได้รัน" และบรรทัด `Screener run complete: %d/%d symbols passed, %d ตรวจไม่ได้` ที่เขียนไว้เพื่อกฎ C1 โดยเฉพาะ ก็ไม่มีวันถึงตาผู้ใช้ นอกจากนี้ยังยืนยันไม่ได้จาก log ว่า APScheduler ลงทะเบียนงานสำเร็จ

**แนวแก้ที่เสนอ**
เพิ่ม `logging.basicConfig(level=os.getenv("VAULTIS_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")` ตอนต้น `backend/main.py` (การใส่ `--log-level info` ให้ uvicorn ไม่ช่วย เพราะมันตั้งเฉพาะ logger ของ uvicorn) และทำแบบเดียวกันใน main.py ของ scheduler ถ้ายังไม่มี

**พบในรอบ** T9 — integration test against the freshly-built containers (read-only; no repo files modified)

---

## [MEDIUM] Streamlit AppTest ทำให้ interpreter ตายด้วย SIGSEGV ที่หน้า Scorecard — เขียนเทสต์เรนเดอร์ dashboard ไม่ได้เลย

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py:4115 (st.dataframe(alloc_df.style.format(...)) ในหน้า Scorecard)`

**อาการ**
รัน `AppTest.from_file("/app/dashboard/app.py")` โดยตั้ง `session_state["page"]="Scorecard"` แล้วโปรเซสตายด้วย Segmentation fault ใน pyarrow `convert_column` ระหว่างแปลง display values ของ Styler เป็น Arrow (สาย st.dataframe → marshall_styler → _marshall_display_values → convert_pandas_df_to_arrow_bytes) เกิดซ้ำ 100% ตายก่อนพิมพ์ผลรอบแรกด้วยซ้ำ

**หลักฐาน (รันจริง)**
```
$ docker compose exec -T dashboard python - < /tmp/t9/sc_only.py   (วน AppTest หน้า Scorecard 3 รอบ)
  Fatal Python error: Segmentation fault
  Current thread ...:
    File "/usr/local/lib/python3.12/site-packages/pyarrow/pandas_compat.py", line 638 in convert_column
    File "/usr/local/lib/python3.12/site-packages/streamlit/dataframe_util.py", line 815 in convert_pandas_df_to_arrow_bytes
    File "/usr/local/lib/python3.12/site-packages/streamlit/elements/lib/pandas_styler_utils.py", line 239 in _marshall_display_values
    File "/app/dashboard/app.py", line 4115 in render_scorecard_page
  EXITCODE=139        (128+11 = SIGSEGV)
ไล่ตัดตัวแปรแล้ว: (ก) รันตรรกะเดียวกันนอก AppTest ผ่านหมด — convert display df สำเร็จ 2792 bytes; (ข) รันใน worker thread ก็ผ่าน ({'n': 2792}) ⇒ ไม่ใช่เรื่อง thread
ยืนยันว่า **โปรดักชันไม่โดน**: เปิดหน้า Scorecard ด้วยเบราว์เซอร์จริงเรนเดอร์ครบ (ตารางจัดสรร VOO 1,800฿ / SCHD 1,300฿ / QQQM 1,000฿ / XLV 500฿ / GLDM 400฿) และคอนเทนเนอร์ไม่รีสตาร์ต: StartedAt ไม่เปลี่ยน Restarts=0 Status=running
เวอร์ชันที่เกี่ยวข้อง: numpy 1.26.4 + pyarrow 25.0.0 + pandas 2.2.3
```

**ผลกระทบ**
ชุดเทสต์ 1138 ตัวที่ผ่านอยู่ **ไม่ได้รับผลกระทบ** (grep แล้วไม่มีไฟล์เทสต์ไหนใช้ AppTest/streamlit.testing) แต่แปลว่า dashboard ไม่มี test coverage ระดับเรนเดอร์เลยสักหน้า และถ้าใครพยายามเพิ่มด้วยวิธีมาตรฐานของ Streamlit จะเจอ interpreter ตายแทน error ที่อ่านรู้เรื่อง — เป็นกับดักที่เสียเวลาสอบสวนนาน

**แนวแก้ที่เสนอ**
เนื่องจากโปรดักชันไม่พัง ให้จัดเป็นปัญหา dependency: ลองอัปเป็น numpy 2.x (pyarrow 25 wheel สร้างบน numpy 2) หรือ pin pyarrow ลงให้ตรงยุค numpy 1.26 แล้วรัน AppTest ซ้ำเพื่อยืนยัน ถ้าแก้ได้ค่อยเพิ่มเทสต์ smoke ครบ 13 หน้าเข้า suite ระหว่างนี้ใส่หมายเหตุไว้ใน CLAUDE.md ว่า AppTest ใช้กับ repo นี้ไม่ได้และเพราะอะไร

**พบในรอบ** T9 — integration test against the freshly-built containers (read-only; no repo files modified)

---

## [MEDIUM] CLAUDE.md + README บอกว่า APScheduler รัน "daily screener only" แต่จริง ๆ มีงานรายเดือนที่ยิง Telegram ด้วย

**ไฟล์** `/home/da00/code/Vaultis/CLAUDE.md`

**อาการ**
หัวข้อ "Scheduled Jobs" เขียน `1. APScheduler (inside FastAPI process) — daily screener only` และหัวข้อ Architecture เขียน `backend/main.py  App init; APScheduler daily screener at 07:00` · แต่ `backend/main.py:73` ลงทะเบียน `generate_and_save_report` เป็น cron วันที่ 1 เวลา 08:00 ซึ่งเรียก `_send_telegram()` จริง · หัวข้อเดียวกันยังขึ้นต้นว่า "Two separate scheduling systems" แล้วไล่รายการ 3 ข้อ

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app -e VAULTIS_DB_PATH=/tmp/t10.db tests python - <<'PY'  (TestClient เปิด lifespan แล้วพิมพ์ scheduler.get_jobs())
ผลจริง:
  JOB: 2a6c578c... | backend.screener.scheduler_job:run_daily_screener | cron[hour='7', minute='0']
  JOB: 966a4342... | backend.services.report_service:generate_and_save_report | cron[day='1', hour='8', minute='0']
  health: 200 {'status': 'ok', 'service': 'Vaultis Backend'}
```

**ผลกระทบ**
ใครก็ตามที่รัน `uvicorn backend.main:app` ทิ้งไว้ (หรือ `docker compose up -d`) จะได้รายงานรายเดือนยิงเข้า Telegram ทุกวันที่ 1 โดยเอกสารไม่ได้บอก — และคนที่อ่านเอกสารเพื่อหา "อะไรบ้างที่ส่งออกไปข้างนอกโดยอัตโนมัติ" จะนับไม่ครบ (ค่าใช้จ่าย LLM ไม่มี เพราะ user_initiated ดีฟอลต์ False — ยืนยันแล้วที่ report_service.py:471)

**แนวแก้ที่เสนอ**
แก้ CLAUDE.md 2 จุด: Architecture → `APScheduler: daily screener 07:00 + monthly report วันที่ 1 08:00 (ส่ง Telegram)` และหัวข้อ Scheduled Jobs → เปลี่ยน "Two separate scheduling systems" เป็น "Three" พร้อมระบุงานรายเดือน · README.md:72 ต้องแก้ประโยคเดียวกัน

**พบในรอบ** T10

---

## [MEDIUM] งาน sentiment รายสัปดาห์ใน GitHub Actions ตายมาตลอด — ยิง secrets จริงทุกวันจันทร์แล้ว return ทันที ⇒ ตาราง sentiment ว่างเปล่าถาวรใน production

**ไฟล์** `/home/da00/code/Vaultis/.github/workflows/scheduler.yml`

**อาการ**
step "Run Sentiment Analysis" ส่ง ANTHROPIC_API_KEY / NEWSAPI_KEY / DATABASE_URL / REDDIT_* เข้าไป แต่ **ไม่ได้ตั้ง `VAULTIS_LLM_AUTO`** ที่ `run_sentiment_job()` บังคับ (analysis/sentiment_analyzer.py:266-272 → `if not auto_enabled(): print(...); return`) ⇒ ทุกรอบข้ามตัวเองก่อนแตะฐานข้อมูล

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app -e DATABASE_URL='postgresql+psycopg2://t10:t10@t10-pg:5432/t10' tests bash -lc 'unset VAULTIS_LLM_AUTO; python -c "...run_sentiment_job()"'
ผลจริง:
  VAULTIS_LLM_AUTO = None
  DATABASE_URL ตั้งไว้ = True
  [sentiment job] ข้ามการวิเคราะห์ sentiment — LLM ปิดอยู่เพื่อคุมค่าใช้จ่าย (ตั้ง VAULTIS_LLM_AUTO=1 ถ้าต้องการเปิด)
```

**ผลกระทบ**
`sentiment_results` / `sentiment_summary` ไม่มีวันมีข้อมูลจากงานอัตโนมัติ ⇒ `/api/sentiment/{symbol}` ตอบ 404 ตลอดกาล และกล่องบริบทในหน้า AI Advisor ขึ้นข้อความ "ยังไม่มีข้อมูลในฐาน — รอ scheduled job รอบถัดไป" (dashboard/app.py:2464) ซึ่งเป็นคำสัญญาที่ไม่มีวันเกิด · ระหว่างนั้น CI ยังส่ง secret 5 ตัวเข้า step ที่ไม่ทำอะไรทุกสัปดาห์

**แนวแก้ที่เสนอ**
เลือกทางเดียวแล้วเขียนให้ตรง: (ก) ถ้าตั้งใจปิด — ลบ step นี้ + secrets ออกจาก workflow แล้วแก้ข้อความใน dashboard เป็น "งาน sentiment ปิดอยู่ (ต้องตั้ง VAULTIS_LLM_AUTO=1 และรันเอง)" ไม่ใช่ "รอรอบถัดไป" หรือ (ข) ถ้าตั้งใจเปิด — เพิ่ม `VAULTIS_LLM_AUTO: "1"` ใน env ของ step นั้น และระบุใน CLAUDE.md ว่างานนี้เป็นข้อยกเว้นที่ผู้ใช้ยอมจ่าย

**พบในรอบ** T10

---

## [MEDIUM] หน้า Settings เขียนทับ "หน้าเริ่มต้น" ของผู้ใช้เงียบ ๆ — page_options เป็นลิสต์ซ้ำที่ตกหล่น Correlation กับ News

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py`

**อาการ**
`page_options` (บรรทัด 772-784, 11 รายการ) เป็นสำเนามือของ `NAV_ITEMS` (บรรทัด 157, 13 รายการ) ที่ drift ไปแล้ว · selectbox ใช้ `index=page_options.index(cur) if cur in page_options else 0` ⇒ ถ้า `config.json` มี `default_page` เป็น "News"/"Correlation" (ค่าที่แถบข้างและ `NAV_ITEMS` รองรับ) selectbox จะเด้งไป "Overview" แล้วปุ่ม "บันทึก Settings" เขียนค่านั้นทับลงดิสก์ แม้ผู้ใช้ตั้งใจแก้แค่งบ DCA

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app tests python - (ก็อป config.json ไป /tmp/t10_config.json ชี้ CONFIG_PATH ไปที่สำเนา ตั้ง default_page=News แล้วเดินตามลอจิกของ selectbox + save_config จริง)
ผลจริง:
  ก่อนบันทึก: default_page = News
  ค่าที่ selectbox คืน = Overview
  หลังกด 'บันทึก Settings': default_page = Overview
และการเทียบลิสต์ (ast บน dashboard/app.py):
  NAV_ITEMS (13) == ปุ่มจริงในแถบข้าง (13) ✔
  page_options (11)
  อยู่ในแถบข้างแต่ตั้งเป็นหน้าเริ่มต้นไม่ได้: ['Correlation', 'News']
```

**ผลกระทบ**
ค่าที่ผู้ใช้ตั้งไว้หายโดยไม่มีคำเตือน (config.json อยู่บน bind mount จริงของ Docker) และหน้า Correlation/News ตั้งเป็นหน้าเริ่มต้นไม่ได้เลย · ขัดกฎ "นิยามมีที่เดียว" โดยตรง — มีลิสต์หน้าจอ 3 ชุด (NAV_GROUPS/NAV_ITEMS, ปุ่มฮาร์ดโค้ดใน _render_custom_sidebar, page_options)

**แนวแก้ที่เสนอ**
ลบ `page_options` ทิ้งแล้วใช้ `NAV_ITEMS` ตรง ๆ ในหน้า Settings · และให้ `_render_custom_sidebar()` วนสร้างปุ่มจาก `NAV_GROUPS` แทนการฮาร์ดโค้ด 13 ปุ่ม เพื่อให้เหลือแหล่งเดียวจริง ๆ · เพิ่มเทสต์ตรึงว่า ตัวเลือกในหน้า Settings == NAV_ITEMS == ปุ่มที่เรนเดอร์

**พบในรอบ** T10

---

## [LOW] K1: หน้าต่างมองย้อนของ get_networth_change (months=3) ไม่มีเทสต์ — เทสต์ทั้งสองตัว stub get_history ทิ้ง

**ไฟล์** `/home/da00/code/Vaultis/backend/services/report_service.py`

**อาการ**
`get_networth_change()` เรียก `networth_service.get_history(db, months=3)` โดยคอมเมนต์บอกว่า "months=3 ครอบเดือนก่อนหน้าได้แน่" แต่ TestNetWorthBaseline ทั้ง test_single_snapshot_has_no_baseline และ test_real_baseline_still_computes monkeypatch `get_history` เป็น lambda ที่คืนลิสต์ตายตัว ⇒ ค่าตัดของจริง (`_months_back`) ไม่เคยถูกเรียกจากเทสต์ของ K1 เลย

**หลักฐาน (รันจริง)**
```
mutation: `get_history(db, months=3)` → `get_history(db, months=1)`
$ pytest -q tests/test_report_service.py → 32 passed
$ pytest -q (ทั้งชุด, mutation นี้ + mutation STALE_SNAPSHOT_DAYS) → 1297 passed, 5 deselected, 3 xfailed

โค้ดตัดจริงอยู่ที่ networth_service.get_history: cutoff = _months_back(today, months) แล้ว filter snapshot_date >= cutoff
```

**ผลกระทบ**
ถ้าใครลดหน้าต่างนี้ (หรือ _months_back เปลี่ยนความหมาย) รายงานรายเดือนจะพูดว่า "ยังไม่มีเดือนก่อนหน้าให้เทียบ" ทั้งที่ผู้ใช้มี snapshot เดือนก่อนอยู่จริง — เป็นการซ่อนข้อมูลเงียบ ๆ ในทิศเดียวกับรูที่ K1/M-R2 เพิ่งปิด

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ที่ใช้ฐานจริง (fixture db เหมือน tests/test_networth_reporting.py): บันทึก snapshot วันนี้ + snapshot วันที่ 1 ของเดือนก่อน แล้ว assert has_baseline is True ผ่าน get_networth_change(db) จริง ๆ ไม่ stub get_history

**พบในรอบ** T1 — หักล้างการเก็บรู K1–K8 ด้วย mutation testing (คัดลอก repo ไป /tmp/t1/repo แล้วย้อนโค้ดที่นั่น · repo จริงไม่ถูกแตะเลย)

---

## [LOW] K2: เส้นแบ่ง STALE_SNAPSHOT_DAYS = 90 ไม่มีเทสต์ — ขยายเป็น 180 แล้วทั้งชุดยังเขียว

**ไฟล์** `/home/da00/code/Vaultis/backend/services/networth_service.py`

**อาการ**
K2 ข้อ 4 ทำให้ snapshot_stale พูดว่า "ไม่รู้" ได้ (None) ซึ่งมีเทสต์ครบ แต่ตัวเลข 90 วันที่แบ่ง fresh/stale ไม่มีเทสต์แตะขอบเลย: เทสต์ใช้แค่ 0 วัน, 5 วัน (fresh) และ 200 วัน, >2000 วัน (stale)

**หลักฐาน (รันจริง)**
```
mutation: `stale = days > STALE_SNAPSHOT_DAYS` → `stale = days > STALE_SNAPSHOT_DAYS * 2`
$ pytest -q tests/test_networth_snapshot_truth.py tests/test_networth_reporting.py tests/test_etf_snapshot_stale.py tests/test_fx_source.py
→ แดงเฉพาะ 2 ตัวของ fx (จาก mutation อีกตัวที่ใส่พร้อมกัน) ไม่มีเทสต์ networth ตัวใดแดง
$ pytest -q (ทั้งชุด) → 1297 passed, 5 deselected, 3 xfailed
```

**ผลกระทบ**
snapshot อายุ 91–180 วันจะถูกรายงานเป็น fresh/snapshot_stale=False ได้โดยไม่มีอะไรจับ — ผู้ใช้เห็นเงินสด/หนี้สินเก่าครึ่งปีโดยไม่มีธงเตือน

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ขอบสองตัวใน TestStaleCanSayUnknown: days = STALE_SNAPSHOT_DAYS ต้อง fresh และ days = STALE_SNAPSHOT_DAYS + 1 ต้อง stale (อ่านค่าคงที่จากโมดูล ไม่ฮาร์ดโค้ด 90)

**พบในรอบ** T1 — หักล้างการเก็บรู K1–K8 ด้วย mutation testing (คัดลอก repo ไป /tmp/t1/repo แล้วย้อนโค้ดที่นั่น · repo จริงไม่ถูกแตะเลย)

---

## [LOW] K1: ข้ออ้าง "ประกอบพรอมป์ไว้ใน try เดียวกับ LLM โดยตั้งใจ" ไม่มีเทสต์ตรึง — ย้ายออกนอก try แล้วเทสต์เขียวหมด

**ไฟล์** `/home/da00/code/Vaultis/backend/services/report_service.py`

**อาการ**
docstring ของ generate_narrative_with_source ระบุชัดว่าเอา `_build_prompt` ไว้ใน try เพื่อกันไม่ให้ข้อผิดพลาดตอนประกอบพรอมป์ฆ่าเส้นทางฟรี แต่เมื่อ `_usd_txt`/`_networth_txt`/`_screener_txt` แข็งแล้ว เทสต์ทุกตัวผ่านได้ทั้งสองแบบ ⇒ ตาข่ายชั้นนี้ไม่ถูกวัด

**หลักฐาน (รันจริง)**
```
mutation: ย้าย `user_msg = _build_prompt(all_data, month)` ออกไปอยู่หน้า `try:`
$ pytest -q tests/test_report_service.py → 32 passed
$ pytest -q (ทั้งชุด) → 1297 passed, 5 deselected, 3 xfailed

probe (all_data ที่ goals ขาดคีย์ 'total' ซึ่งใช้เฉพาะใน _build_prompt):
  MUTATED : DIED: KeyError 'total'
  PRISTINE: OK source = plain | 📊 สรุปการเงินเดือน 2026-08 (จากโมเดล — ไม่ใช้ AI)  + log "AI เขียนบทสรุปไม่สำเร็จ: 'total'"
```

**ผลกระทบ**
วันที่ contract ของ _aggregate_data เปลี่ยน (คีย์หาย/เปลี่ยนชื่อ) รายงานรายเดือนจะตายทั้งฉบับแทนที่จะถอยไปเส้นทางฟรีพร้อมหมายเหตุ — คืออาการ K1 กลับมาในรูปแบบใหม่ โดยไม่มีเทสต์เตือน

**แนวแก้ที่เสนอ**
เพิ่มเทสต์: ป้อน all_data ที่ขาดคีย์ซึ่งใช้เฉพาะในพรอมป์ (เช่น goals['total']) แล้ว assert ว่า generate_narrative_with_source คืน source='plain' พร้อมหมายเหตุ ไม่ใช่โยน exception

**พบในรอบ** T1 — หักล้างการเก็บรู K1–K8 ด้วย mutation testing (คัดลอก repo ไป /tmp/t1/repo แล้วย้อนโค้ดที่นั่น · repo จริงไม่ถูกแตะเลย)

---

## [LOW] [ข้อสังเกตนอกโค้ด] ไฟล์คลัง alert จริงหายไประหว่างรอบนี้ เหลือแต่ price_alerts.json.lock

**ไฟล์** `/home/da00/code/Vaultis/alerts/data/price_alerts.json`

**อาการ**
ตอนผมคัดลอก repo ไป /tmp ตอนเริ่มงาน ไฟล์ยังอยู่ (19 ไบต์ เนื้อหา {"alerts": []} mtime 09:00) ตอนนี้ในโฟลเดอร์จริงเหลือแค่ .gitkeep กับ price_alerts.json.lock (0 ไบต์ mtime 21:00) ไฟล์ข้อมูลหายไปโดยไม่มี .bak และไม่มี .tmp ค้าง — และไม่มีโค้ดบรรทัดไหนในโปรเจกต์ที่ลบไฟล์นี้ (grep unlink/os.remove เจอแค่ tmp_path.unlink ใน _save_alerts)

**หลักฐาน (รันจริง)**
```
$ ls -la /home/da00/code/Vaultis/alerts/data/
  .gitkeep (295) · price_alerts.json.lock (0, Aug 7 21:00)   ← ไม่มี price_alerts.json
$ ls -la /tmp/t1/pristine/alerts/data/   (สำเนาตอนเริ่มงาน)
  .gitkeep (295) · price_alerts.json (19, Aug 7 09:00)
$ cat /tmp/t1/pristine/alerts/data/price_alerts.json → {"alerts": []}
$ docker logs --tail 60 vaultis-scheduler → "[price alert] ไม่มี alert ค้างให้ตรวจ (อ่านคลัง alert ได้ปกติ)" (รอบ 21:00)
$ docker ps → vaultis-scheduler/backend/dashboard Up 29 นาที (สแตกจริงของผู้ใช้รันอยู่และ mount ./alerts/data)
$ grep -rn 'unlink|os.remove' --include=*.py . | grep -v test → มีแค่ alerts/price_alert.py:138 tmp_path.unlink
ยืนยันว่าไม่ใช่ผม: การรันเทสต์ทุกครั้งใช้ -v /tmp/t1/repo:/app และ service `tests` ไม่มี volumes ของ host เลย — สำเนาที่ผม mount ยังมีไฟล์ครบ (ls /tmp/t1/repo/alerts/data → price_alerts.json 19 ไบต์ mtime เดิม) และ .docker-data/vaultis.db ในสำเนาก็ยัง mtime Aug 6 21:06
```

**ผลกระทบ**
เนื้อหาที่หายเป็นลิสต์ว่าง จึงไม่มี alert ของจริงสูญ แต่ไฟล์นี้ gitignored = กู้จาก git ไม่ได้ ถ้าครั้งหน้าหายตอนมี alert อยู่จะสูญถาวร และสถานะเปลี่ยนจาก ok/0 เป็น missing ซึ่งเป็นคนละความหมายในโค้ด (get_store_status)

**แนวแก้ที่เสนอ**
ผมไม่แตะไฟล์จริงตามข้อห้าม — ให้ผู้ใช้ตัดสินใจเอง ข้อเสนอ: ตรวจว่ามีเซสชัน/สคริปต์อื่นเขียนทับโฟลเดอร์นี้อยู่หรือไม่ แล้วสร้าง alerts/data/price_alerts.json กลับเป็น {"alerts": []} ด้วยมือ (หรือปล่อยไว้ เพราะ _load_alerts ตีความ "ไม่มีไฟล์" ว่ายังไม่เคยตั้ง alert ได้ถูกต้องอยู่แล้ว)

**พบในรอบ** T1 — หักล้างการเก็บรู K1–K8 ด้วย mutation testing (คัดลอก repo ไป /tmp/t1/repo แล้วย้อนโค้ดที่นั่น · repo จริงไม่ถูกแตะเลย)

---

## [LOW] ด่านงบใหม่ของ rebalance_with_new_money ปล่อย NaN/inf ผ่าน แล้วไปตายด้วยข้อความอังกฤษที่ชี้สาเหตุผิด

**ไฟล์** `/home/da00/code/Vaultis/portfolio/cashflow_rebalance.py:55-59`

**อาการ**
ด่านถูกเขียนใหม่รอบนี้จาก `budget_thb <= 0` เป็น `budget_thb < UNIT_THB` แต่ยังใช้การเปรียบเทียบล้วน ๆ ซึ่ง NaN เทียบอะไรก็ False เสมอ ⇒ NaN/inf ผ่านด่านไปตายที่ int(budget_thb // UNIT_THB) บรรทัด 86

**หลักฐาน (รันจริง)**
```
$ docker … tests python — วนงบหลายค่าเข้า rebalance_with_new_money({"VOO":10000,"SCHD":5000}, {"VOO":0.5,"SCHD":0.5}, b):
budget=nan       ValueError: cannot convert float NaN to integer
budget=inf       ValueError: cannot convert float NaN to integer
budget=0.0       ValueError: งบต้องอย่างน้อย 100 บาท (แผนปัดเป็นหลักร้อย งบน้อยกว่านี้แจกไม่ลงสักกอง)
budget=-100.0    ValueError: งบต้องอย่างน้อย 100 บาท …
budget=99.9      ValueError: งบต้องอย่างน้อย 100 บาท …
budget=100.0     plan={'SCHD': {'amount_thb': 100, …}}  unalloc=0.0
```

**ผลกระทบ**
dashboard/app.py:3833 จับด้วย `except Exception as exc: st.warning(f"ใช้โหมดดึงเข้าเป้าไม่ได้: {exc} …")` ผู้ใช้จึงเห็นข้อความอังกฤษ "cannot convert float NaN to integer" ที่บอกว่าเป็น NaN ทั้งที่ค่าจริงอาจเป็น inf — ผิดกฎ "ข้อความผู้ใช้เป็นภาษาไทย" และชี้สาเหตุผิด (ทางเข้าปกติมาจาก st.number_input จึงยากจะเกิด แต่ด่านนี้ถูกเขียนใหม่รอบนี้และตั้งใจให้ครอบงบที่ใช้ไม่ได้ทุกแบบ)

**แนวแก้ที่เสนอ**
เปลี่ยนด่านเป็น `if not math.isfinite(budget) or budget < UNIT_THB:` โดยแปลง float(budget_thb) ก่อนหนึ่งครั้ง แล้วใช้ตัวแปรเดียวกันตลอดฟังก์ชัน (ตอนนี้ float(budget_thb) ถูกเรียกซ้ำ 3 ที่) — สำนวนเดียวกับที่ debt_models.FiniteFloat และ portfolio/targets._read_custom_weights ใช้อยู่แล้ว

**พบในรอบ** T2 — ล่ารูที่การแก้รอบนี้สร้างขึ้นเอง (money path + ค่าที่ "มีอยู่แต่ใช้ไม่ได้")

---

## [LOW] fillna(0.0) ใหม่บนค่าธรรมเนียม ทำให้อัตราแลกเปลี่ยนที่คำนวณย้อนสูงเกินจริง ~0.15% และค่าธรรมเนียมติดลบหายเงียบ

**ไฟล์** `/home/da00/code/Vaultis/portfolio/tracker.py:214`

**อาการ**
`_recorded_fee_thb()` ที่เพิ่งเพิ่มปิดท้ายด้วย `return fee.where(fee >= 0).fillna(0.0)` — เป็น `fillna(0` บนเส้นทางเงินตรง ๆ ตามรายการต้องห้าม  ผลไปเข้า `_derive_fx_from_amount()` เป็น `(amount_thb - fee) / (shares * price_usd)` แล้วถูกใช้เติมคอลัมน์ `fx_rate_thb` จริงที่บรรทัด 678 สองกรณีถูกยุบเป็นค่าเดียวกัน: แถวที่ **ไม่ได้บันทึก** ค่าธรรมเนียม (ยอมรับได้ตามที่ docstring อธิบายไว้) กับแถวที่บันทึกเป็น **ค่าติดลบ** = ข้อมูลผิดชัด ๆ ซึ่งถูกกลืนเป็น 0 โดยไม่เข้า `_collect_skipped_rows` หรือ `_collect_inconsistent_rows` เลย ผู้ใช้จึงไม่มีทางรู้ว่ามีเลขผิดในสมุด

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -e PYTHONPATH=/app -v /home/da00/code/Vaultis:/app -w /app tests python /tmp/probe1_run.py

  แถวทดสอบ: shares=1.0, price_usd=100.0, amount_thb=3400.0, fee_thb = [5.1, NaN, -5.1]
  fee ที่อ่านได้:   [5.1, 0.0, 0.0]
  fx ที่คำนวณย้อน: [33.949, 34.0, 34.0]

(อัตราจริงเมื่อค่าธรรมเนียมมีอยู่ = 33.949 · อีกสองแถวได้ 34.0 = สูงเกิน 0.15% พอดีตาม fees.DIME_FEE_RATE และแถว fee ติดลบให้ผลเท่ากับแถวที่ไม่ได้บันทึกเลย)

ขอบเขตผลกระทบจริงตอนนี้ (อ่านอย่างเดียว ไม่แก้ไฟล์):
  awk -F, ... portfolio/data/transactions.csv → rows=0 empty_fee=0 empty_fx=0
  (สมุดจริงมีแต่หัวตาราง ยังไม่มีแถวข้อมูล จึงยังไม่กระทบเงินของผู้ใช้)
```

**ผลกระทบ**
อัตราแลกเปลี่ยนที่ระบบ "คำนวณย้อนได้" คลาดสูงกว่าความจริง 0.15% บนแถวที่ไม่มี `fee_thb` แล้วไหลต่อไปทุกตัวเลขฐานบาท (ต้นทุน, มูลค่า, P&L, XIRR)  หน้าจอบอกแค่ว่า "แถวนี้ใช้อัตราที่คำนวณย้อน" ผ่าน `_collect_derived_fx_rows` แต่ไม่บอกว่าสมมติค่าธรรมเนียมเป็น 0 · ส่วนแถวที่ค่าธรรมเนียมติดลบคือข้อมูลผิดที่ถูกทำให้เงียบสนิท ซึ่งเข้าข่าย "ตัดข้อมูลทิ้งเงียบ" ตามกฎข้อ 2  ตอนนี้ยังไม่มีผลจริงเพราะสมุดว่าง แต่จะมีทันทีที่ผู้ใช้เริ่มบันทึกและเว้นช่อง fee

**แนวแก้ที่เสนอ**
แยกสองกรณีออกจากกัน: (ก) `fee_thb` เป็นค่าว่าง = ไม่ได้บันทึก → คงพฤติกรรมเดิม (สมมติ 0) ได้ แต่ควรติดหมายเหตุลงใน `_DERIVED_FX_ROWS_ATTR` ว่า "อัตรานี้คิดโดยสมมติค่าธรรมเนียม 0 อาจสูงกว่าจริง ~0.15%" เพื่อให้ผู้ใช้เห็นสมมติฐาน (ข) `fee_thb < 0` = ข้อมูลผิด → อย่ากลืนเป็น 0 ให้ปล่อยเป็น NaN แล้วส่งแถวนั้นเข้า `_collect_inconsistent_rows()` ซึ่งมีทางแสดงผลอยู่แล้ว จะได้ดังตามกฎ fail-loud เหมือนที่ `unusable_fx` ทำกับอัตรานอกช่วง 20–50

**พบในรอบ** T4 — กวาด git diff หาสำนวนที่กฎห้ามซึ่งเพิ่งเพิ่มเข้ามาใหม่

---

## [LOW] analysis/financial_model.py — clamp ของ _score_tilt (0–100) ไม่มีเทสต์ตรึง ถอดออกแล้ว tilt ทะลุกรอบ 0.6–1.4 ได้เงียบ ๆ

**ไฟล์** `analysis/financial_model.py`

**อาการ**
เปลี่ยน `clamped = max(0.0, min(100.0, total_pct))` เป็น `clamped = total_pct` แล้วชุดเทสต์เต็มยังเขียว 1297 passed · `tests/test_ab_backtest.py:183 test_score_tilt_weights_within_bounds_on_realistic_history` ทดสอบขอบเขตเฉพาะบนอนุกรมราคาจริง (ซึ่ง total_pct อยู่ใน 0–100 อยู่แล้ว) ไม่มีเทสต์ไหนป้อนค่านอกช่วงเข้า `_score_tilt()` โดยตรง · เทียบกับ M11 (เปลี่ยนค่า TILT_MIN/TILT_MAX เอง) ที่โดนจับ 3 ตัวทันที — ตาข่ายจับ "ค่าคงที่ถูกแก้" แต่ไม่จับ "guard ที่บังคับใช้ค่าคงที่นั้นหายไป"

**หลักฐาน (รันจริง)**
```
$ /tmp/mut/tools/run_one.sh M12   # `clamped = max(0.0, min(100.0, total_pct))` -> `clamped = total_pct`
MUTATION_APPLIED M12 analysis/financial_model.py :: remove tilt clamp
1297 passed, 5 deselected, 3 xfailed, 21 warnings in 80.15s (0:01:20)
=== EXIT=0 ===

$ /tmp/mut/tools/demo_pair.sh M12
--- M12 BASE (ต้นฉบับ) ---
  _score_tilt(150): 1.4
  _score_tilt(-40): 0.6
  alloc(total_pct=150 vs 50, 10000thb): {'AAA': (5800, 1.4), 'BBB': (4200, 1.0)}
--- M12 MUTANT ---
  _score_tilt(150): 1.7999999999999998
  _score_tilt(-40): 0.27999999999999997
  alloc(total_pct=150 vs 50, 10000thb): {'AAA': (6400, 1.8), 'BBB': (3600, 1.0)}
```

**ผลกระทบ**
severity ต่ำเพราะ **วันนี้ยังเข้าไม่ถึง**: ตรวจแล้วว่า total_pct ที่ผลิตในระบบมาจาก `score_from_prices()` ทางเดียว และองค์ประกอบทุกตัวมีเพดานของตัวเอง (`_trend_score` 0–40, `_timing_score` 0–30, `_momentum_score` ≤ mom_max, `_dividend_score` 0–10 หารด้วย max_score ที่หดตามกัน) จึงอยู่ใน [0,100] เสมอ · แต่ `calculate_allocation(scores, budget, ...)` รับ dict จากผู้เรียกภายนอกได้ (ai_advisor.py:325, dashboard/app.py:4082) ถ้าวันหน้ามีคะแนนถ่วงน้ำหนักตัวใหม่ที่เกิน 100 หรือติดลบ กรอบ tilt 0.6–1.4 ที่ CLAUDE.md ประกาศว่าเป็นนโยบายจัดสรร DCA ("ทุก ETF ที่มีข้อมูลได้เงินเสมอ") จะพังโดยไม่มีเทสต์ตัวไหนแดง — ค่า 0.28 แปลว่าเกือบไม่ซื้อ ซึ่งเป็น market timing ที่นโยบายห้ามไว้

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ตรงที่ tests/test_money_math.py (หรือ test_ab_backtest.py): parametrize `_score_tilt` ด้วย `[-100, -0.1, 0, 50, 100, 100.1, 1e9, float('nan')]` แล้ว assert `TILT_MIN <= tilt <= TILT_MAX` ทุกตัว (NaN ควรตัดสินใจให้ชัดว่าจะ raise หรือคืน TILT_MIN — ตอนนี้ `max(0.0, min(100.0, nan))` คืน nan เงียบ ๆ)

**พบในรอบ** T5 — mutation testing 50 จุดบนเส้นทางเงินและโค้ดที่เพิ่งแก้ (สำเนา repo ที่ /tmp ไม่แตะไฟล์ใน repo)

---

## [LOW] analysis/financial_model.py — เกณฑ์ 200 วันเทรดไม่มีเทสต์ตรึง ลดเป็น 100 แล้วชุดเทสต์ยังเขียว (สาเหตุที่รายงานเปลี่ยนไป)

**ไฟล์** `analysis/financial_model.py`

**อาการ**
เปลี่ยน `if len(closes) < 200:` ใน `score_from_prices()` เป็น `< 100` แล้วชุดเทสต์เต็มยังเขียว 1297 passed · ทั้งสองเวอร์ชันยัง fail loud (ValueError) แต่ **ข้อความสาเหตุเปลี่ยน** เพราะ mutant ไปตกที่ด่าน NaN ของ MA/RSI แทน

**หลักฐาน (รันจริง)**
```
$ /tmp/mut/tools/run_one.sh M16   # `if len(closes) < 200:` -> `if len(closes) < 100:`
MUTATION_APPLIED M16 analysis/financial_model.py :: min history 200 -> 100 bars
1297 passed, 5 deselected, 3 xfailed, 21 warnings in 79.02s (0:01:19)
=== EXIT=0 ===

$ /tmp/mut/tools/demo_pair.sh M16   # ป้อนอนุกรมราคา 150 แท่ง
--- M16 BASE (ต้นฉบับ) ---
  score_from_prices(150 bars): RAISED ValueError: XXX: ข้อมูลราคาน้อยกว่า 200 วันเทรด
--- M16 MUTANT ---
  score_from_prices(150 bars): RAISED ValueError: XXX: คำนวณตัวชี้วัด MA/RSI ไม่ได้
```

**ผลกระทบ**
ต่ำ เพราะไม่มีตัวเลขปลอมหลุดออกไป (MA200 เป็น NaN ทำให้ด่านถัดไป raise อยู่ดี) แต่สาเหตุที่ผู้ใช้เห็นเปลี่ยนจาก "ข้อมูลย้อนหลังไม่พอ" (แก้ได้ด้วยการรอ/เปลี่ยนช่วงดึง) เป็น "คำนวณตัวชี้วัดไม่ได้" (ฟังเหมือนระบบพัง) ข้อความนี้ถูกส่งต่อเป็น `_no_data(ticker, str(exc))` ขึ้นหน้าจอและเข้า prompt ของ AI จริง — "บอกสาเหตุผิด" อยู่ในตระกูลเดียวกับกฎข้อ 2 ของโปรเจกต์

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ boundary: 199 แท่ง ต้องได้ ValueError ที่มีคำว่า "200" และ 200 แท่งพอดีต้องคำนวณผ่าน (มี total_pct เป็นตัวเลข) — ตรึงทั้งเลขและข้อความสาเหตุ ไม่ใช่แค่ "raise อะไรก็ได้"

**พบในรอบ** T5 — mutation testing 50 จุดบนเส้นทางเงินและโค้ดที่เพิ่งแก้ (สำเนา repo ที่ /tmp ไม่แตะไฟล์ใน repo)

---

## [LOW] /api/sentiment/{symbol}: ช่อง NULL ในฐานถูกแปลงเป็น 0 / "neutral" ด้วยสำนวน `or` — "ไม่รู้" กลายเป็นตัวเลขและคำตัดสิน

**ไฟล์** `backend/routers/sentiment.py:41`

**อาการ**
`_summary_to_response()` ใช้ `int(row.total_articles or 0)`, `float(row.avg_confidence or 0.0)`, `float(row.score or 0.0)`, `str(row.overall_sentiment or "neutral")` ทุกช่อง ⇒ แถวที่คอลัมน์เป็น NULL (เช่น job เขียนไม่ครบ หรือ schema เก่า) ออกมาเป็น sentiment ที่สมบูรณ์แบบ: 0 บทความ, ความเชื่อมั่น 0.0, คะแนน 0.0, ทิศทาง "neutral" โดยไม่มีช่องไหนบอกว่าค่าเหล่านี้ถูกเดาขึ้นมา · สำนวน `or` ยังกลืน `0`/`0.0` ที่เป็นคำตอบจริงเข้ากับ NULL ด้วย (แยกไม่ออก)

**หลักฐาน (รันจริง)**
```
รัน `docker compose --profile dev run --rm -v /tmp/vprobe:/app ... tests pytest -q -s /sp/test_t6_sentiment.py` (แถวจำลองที่ทุกคอลัมน์เป็น None):
  NULL row ->  {'symbol': 'VOO', 'total_articles': 0, 'positive': 0, 'negative': 0, 'neutral': 0, 'avg_confidence': 0.0, 'overall_sentiment': 'neutral', 'score': 0.0, 'created_at': datetime.datetime(2026, 8, 1, 0, 0), 'cached': False}
```

**ผลกระทบ**
ตรงกับข้อห้ามใน CLAUDE.md เป๊ะ ("ห้าม `return 0` / `return "neutral"`") — แม้ sentiment จะเป็นบริบทข้าง ๆ ที่ไม่เข้าเลขคะแนน/DCA แต่กล่องบริบทในหน้า AI Advisor จะแสดง "neutral, ความเชื่อมั่น 0%" เป็นข้อเท็จจริง ทั้งที่ความจริงคือแถวนั้นไม่มีข้อมูล ผู้ใช้แยก "ตลาดเฉย ๆ" กับ "ยังไม่มีผลวิเคราะห์" ไม่ออก

**แนวแก้ที่เสนอ**
ให้ `SentimentResponse` ประกาศฟิลด์ตัวเลขเป็น `Optional` แล้วส่ง `None` ตรง ๆ เมื่อ DB เป็น NULL (ห้ามใช้ `or`) และเพิ่มธงบอกว่าแถวนี้ไม่ครบ — หรือถ้าถือว่า NULL = แถวเสีย ก็ปฏิเสธด้วย 503/404 พร้อมเหตุผล ดีกว่าแปลงเป็นศูนย์เงียบ ๆ · ถ้าต้องคงค่าเริ่มต้น ให้เช็ค `is None` แทน `or` เพื่อไม่กลืน 0 ที่เป็นคำตอบจริง

**พบในรอบ** T6 — สัญญาข้อมูลระหว่างชั้น (service ↔ router ↔ Pydantic ↔ openapi ↔ JSON ↔ WebSocket)

---

## [LOW] /api/cashflow/scenario: inf/NaN ในคำขอทำให้ตอบ 422 ด้วยข้อความอังกฤษของ JSON encoder และรูปร่าง body ขัดกับที่ openapi ประกาศ

**ไฟล์** `backend/routers/cashflow.py:66`

**อาการ**
`TransactionItem.amount` เป็น `float` เปล่า Pydantic รับ `inf`/`nan` เข้ามาได้ (Starlette แกะ body ด้วย `json.loads` ของ Python ซึ่งรับทั้ง `1e400` และ literal `NaN`) ค่าจึงไหลเข้าไปคำนวณจนสุดทาง แล้วไปตายตอน `JSONResponse(content=result.model_dump())` render (`allow_nan=False`) · ตัว `except ValueError` ของ router ซึ่งตั้งใจไว้ดัก "ข้อมูลไม่พอพยากรณ์" ดันไปดัก ValueError ของ JSON encoder แล้วแปลงเป็น 422 พร้อม detail เป็นสตริงอังกฤษดิบ ทั้งที่ openapi ประกาศ 422 = `HTTPValidationError` ที่ `detail` เป็น **array** ของ object

**หลักฐาน (รันจริง)**
```
รัน `docker compose --profile dev run --rm -v /tmp/vprobe:/app ... tests python /sp/t6_inf_input.py`:
  --- 1) cashflow/scenario: amount = 1e400 (json -> inf) ---
     -> 422 {"detail":"Out of range float values are not JSON compliant: inf"}
  --- 2) cashflow/scenario: amount = NaN ---
     -> 422 {"detail":"Out of range float values are not JSON compliant: nan"}

เทียบกับ endpoint ที่ดักถูก:
  --- 5) dca/simulate: weights = NaN --- -> 400 {"detail":"เกิดข้อผิดพลาดในการจำลอง DCA: ผลรวมของ weights ต้องมากกว่า 0"}
```

**ผลกระทบ**
ไม่ล่มและไม่กุตัวเลข (ดีกว่าข้อบน) แต่ (ก) ข้อความไม่ใช่ภาษาไทยและไม่บอกว่าฟิลด์ไหนผิด ผู้ใช้แก้ไม่ถูก (ข) ไคลเอนต์ที่ parse 422 ตาม openapi (`detail[0].loc`) จะพังเพราะได้สตริงแทน array (ค) `except ValueError -> 422` ที่กว้างเกินไปหมายความว่าความล้มเหลว **ตอน serialize คำตอบ** ถูกเล่าเป็นความผิดของอินพุต ซึ่งจะกลบบั๊กจริงในอนาคตที่ทำให้ผลลัพธ์เป็น inf ด้วยเหตุอื่น

**แนวแก้ที่เสนอ**
ปฏิเสธที่ประตูแทน: ใส่ `field_validator` บน `TransactionItem.amount` (และฟิลด์ `float` อื่นใน `cashflow_models.py`) ที่ต้องผ่าน `math.isfinite()` ⇒ ได้ 422 มาตรฐานพร้อม `loc` ชี้แถวที่ผิด · แล้วเปลี่ยน `except ValueError` ให้ดักเฉพาะ ValueError ที่มาจาก `cashflow_service.build_forecast_response` (เช่น ห่อด้วยชนิด exception ของตัวเอง) ไม่ให้คร่อมช่วง serialize

**พบในรอบ** T6 — สัญญาข้อมูลระหว่างชั้น (service ↔ router ↔ Pydantic ↔ openapi ↔ JSON ↔ WebSocket)

---

## [LOW] POST /api/ai/advice งบต่ำกว่า 100 บาท → 500 ขณะที่ /api/analysis/full กรณีเดียวกันตอบ 422

**ไฟล์** `/home/da00/code/Vaultis/backend/routers/ai.py:98`

**อาการ**
รอบนี้ `calculate_allocation()` เปลี่ยนจากคืน `{}` เป็นโยน `ValueError` เมื่องบ < `ALLOCATION_UNIT_THB` (100) · `/api/analysis/full` เพิ่มด่าน 422 รับไว้แล้ว แต่ `AiAdviceRequest.budget_thb` (backend/schemas.py:66) ยังเป็นแค่ `gt=0` และ `get_monthly_advice()` ห่อทุก exception เป็น `RuntimeError` ซึ่ง router แปลงเป็น 500

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v $PWD:/app -v /tmp/t7:/probe tests pytest -q -s /probe/test_ai_budget_probe.py  (stub build_etf_scores/macro/Discord — ไม่ยิงเน็ตและไม่เรียก LLM)
  POST /api/ai/advice budget=    50 -> 500  {"detail":"เกิดข้อผิดพลาดในการวิเคราะห์ Advisor: งบ 50 บาท น้อยกว่าหน่วยจัดสรรขั้นต่ำ 100 บาท — จัดสรรไม่ได้ (ไม่เกี่ยวกับความพร้อมของข้อมูล)"}
  POST /api/ai/advice budget=  5000 -> 200  {"data":{"budget_thb":5000.0,...}}
```

**ผลกระทบ**
ค่าที่ผู้เรียกกรอกผิดถูกรายงานเป็น "เซิร์ฟเวอร์พัง" (500) แทน 422 · และในของจริง `build_etf_scores()` จะยิง yfinance ครบทุกกองก่อนถึงจะไปตายที่บรรทัดจัดสรร = เสียเวลา/โควตาฟรี ๆ (ข้อความรายละเอียดยังเป็นไทยอ่านออก จึงไม่ถึงขั้นร้ายแรง)

**แนวแก้ที่เสนอ**
ใส่ `ge=ALLOCATION_UNIT_THB` ใน `AiAdviceRequest.budget_thb` หรือเพิ่มด่าน 422 หน้า router แบบเดียวกับ `/api/analysis/full` (บรรทัด 88-95 ของ backend/routers/analysis.py) — นิยาม 100 บาทอยู่ที่ `financial_model.ALLOCATION_UNIT_THB` ที่เดียวอยู่แล้ว

**พบในรอบ** T7

---

## [LOW] หน้า Portfolio อ่าน display.default_fx_rate ตรง ๆ (ผิดกฎ CLAUDE.md) และกิ่ง fallback เป็น dead code

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py:3321`

**อาการ**
`render_portfolio_page()` ทำ `default_fx_rate = float(config["display"]["default_fx_rate"])` แล้วใช้เป็นค่าสำรองของ `get_today_fx_rate_thb()` ด้วยเงื่อนไข `if not today_fx_rate or today_fx_rate <= 0` ทั้งที่ CLAUDE.md ระบุว่า "Never read `default_fx_rate` directly" · หลัง B9 `get_today_fx_rate_thb()` ไม่มีทางคืน 0/None อีกแล้ว — คืนค่าที่อยู่ในช่วง 20–50 เสมอ หรือโยน `FxRateUnavailable` เงื่อนไขนี้จึงไม่มีวันเป็นจริง และค่าที่อ่านมาตรงนี้ยังข้าม band check ของ `_config_fallback()` ไปด้วย

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v $PWD:/app -v /tmp/t7:/probe tests pytest -q -s /probe/test_fx_probe.py
  tracker.get_today_fx_rate_thb()  -> FxRateUnavailable: ...อยู่นอกช่วงที่ใช้ได้ 20–50...   (ค่าสำรองพัง = โยน ไม่ใช่คืน 0)
  [คุมกลุ่ม FX สดปกติ] fx_source: {'fx_rate_thb': 33.0, 'fx_is_live': True}   (ค่าปกติ = อยู่ใน band เสมอ)
grep -n "default_fx_rate" dashboard/app.py → 3321 (อ่านตรง), 3327 (ใช้เป็น fallback)
```

**ผลกระทบ**
โค้ดตายที่อ่านแล้วเข้าใจผิดว่ายังมีเส้นทางสำรองของ FX อยู่บนหน้า Portfolio และเป็นทางเข้าที่สองสู่ `default_fx_rate` ซึ่งกฎของโปรเจกต์สั่งให้มีทางเดียว (`utils/fx`) — ถ้าใครแก้ `get_today_fx_rate_thb()` ให้คืน 0 ในอนาคต ค่าที่ไม่ผ่าน band จะไหลเข้าฟอร์มบันทึกธุรกรรมทันที

**แนวแก้ที่เสนอ**
ลบตัวแปร `default_fx_rate` กับเงื่อนไขที่บรรทัด 3326-3327 ออก แล้วครอบ `get_today_fx_rate_thb()` ด้วย try/except `FxRateUnavailable` → `st.error()` ภาษาไทย (แบบเดียวกับที่ `_render_execute_list()` ที่บรรทัด ~3912 จัดการ `fx.is_live`)

**พบในรอบ** T7

---

## [LOW] alerts/price_alert.py: กติกา "เขียนคลัง alert เฉพาะตอนมีอะไรเปลี่ยนจริง" ไม่มีเทสต์ตรึง

**ไฟล์** `/home/da00/code/Vaultis/alerts/price_alert.py:550-552`

**อาการ**
ถอดเงื่อนไข `if triggered_items:` ที่ครอบ `_save_alerts(alerts)` ออก (= เขียนไฟล์ทับทุกครั้งที่ตรวจ แม้ไม่มี alert ไหนติด) แล้วเทสต์ผ่านครบ 1296 ตัว

**หลักฐาน (รันจริง)**
```
มิวแทนต์ M14 บนสำเนา /tmp/mut (baseline สะอาด = 1296 passed):
  แก้ `        if triggered_items:\n            _save_alerts(alerts)` -> `        _save_alerts(alerts)`
  ผล: `1296 passed, 1 skipped, 5 deselected, 3 xfailed, 21 warnings in 57.45s` — ไม่มี FAILED (รอดชีวิต)

คอมเมนต์ในซอร์สระบุเจตนาไว้เอง: "เขียนเฉพาะตอนที่มีอะไรเปลี่ยนจริง — การเขียนทุกครั้งคือความเสี่ยงไฟล์เสียโดยไม่ได้อะไร" แต่ไม่มีเทสต์บังคับ
```

**ผลกระทบ**
alerts/data/price_alerts.json เป็นแหล่งเดียวของ price alert ตาม CLAUDE.md ถ้าเงื่อนไขนี้หลุด scheduler จะเขียนทับไฟล์ทุกรอบการตรวจ เพิ่มโอกาสไฟล์เสียตอนถูกฆ่ากลางคัน โดยไม่ได้ประโยชน์อะไรกลับมา — ความเสี่ยงต่ำแต่กติกาที่โค้ดเขียนคอมเมนต์บังคับตัวเองไว้ควรมีตาข่าย

**แนวแก้ที่เสนอ**
เพิ่มเทสต์ใน tests/test_price_alert_store.py: ตั้ง alert ที่ไม่ติด (ราคาห่างเป้า) บันทึก mtime_ns + เนื้อไฟล์ก่อนเรียก check_alerts() แล้ว assert ว่าไฟล์ไม่ถูกแตะเลย — ใช้แพตเทิร์นเดียวกับ TestRealStoreUntouched ที่มีอยู่แล้วในไฟล์นั้น

**พบในรอบ** T8 — ความน่าเชื่อถือของชุดเทสต์

---

## [LOW] PDF รายงานรายเดือน: อีโมจิ 🔒 กลายเป็นกล่องสี่เหลี่ยม (tofu) เพราะฟอนต์ Garuda ไม่มีอีโมจิ

**ไฟล์** `/home/da00/code/Vaultis/utils/pdf_export.py (ใช้ Garuda สำหรับข้อความไทย) — ข้อความมาจาก analysis/llm.AI_DISABLED_MESSAGE`

**อาการ**
หน้า 3 (AI Advisor Summary) ขึ้นต้นบรรทัด AI Commentary ด้วยกล่องสี่เหลี่ยมว่างแทนอีโมจิ 🔒 (U+1F512) เพราะ Garuda.ttf มีแต่กลิฟไทย/ละติน ไม่มี emoji

**หลักฐาน (รันจริง)**
```
$ docker compose exec -T backend python -c 'pdf_export.generate_monthly_report("2026-08", 5000.0, include_ai=False)'  → PDF bytes: 43596
$ pdffonts report_t9.pdf
  AAAAAA+Garuda-Bold   TrueType  WinAnsi  emb=yes
  AAAAAA+Garuda        TrueType  WinAnsi  emb=yes
$ pdftoppm -r 600 -png -f 3 -l 3 ... (ครอปบล็อก AI Commentary)
  เห็นกล่องสี่เหลี่ยมว่าง 1 ตัวก่อนข้อความ "บทวิเคราะห์ AI ปิดอยู่เพื่อคุมค่าใช้จ่าย —" ชัดเจนที่ 600dpi
ส่วนอักษรไทยเองถูกต้องทั้งหมด: pdftotext ได้ไทย 188 ตัวอักษร, U+FFFD = 0, U+25A1 = 0 และเรนเดอร์ 600dpi เห็นวรรณยุกต์ซ้อนถูกตำแหน่งครบ (ปุ่ม/ห์/หน้า/เว็บ/เพื่อ/ให้)
```

**ผลกระทบ**
เอกสารที่ผู้ใช้อาจส่งต่อ/เก็บไว้มีกล่องสี่เหลี่ยมแปลกปลอม ดูเหมือนฟอนต์ไทยพัง ทั้งที่ไทยปกติดี — เข้าใจผิดได้ว่ารายงานเสีย

**แนวแก้ที่เสนอ**
ถอดอีโมจินำหน้าออกก่อนเขียนลง PDF (เพิ่มการกรองช่วง emoji ใน `_pdf_text`/`_markup` ของ utils/pdf_export.py) หรือแทนที่ด้วยข้อความล้วนเช่น "[ปิดอยู่]" — อย่าไปลบอีโมจิที่ต้นทาง AI_DISABLED_MESSAGE เพราะหน้าเว็บแสดงได้ปกติ

**พบในรอบ** T9 — integration test against the freshly-built containers (read-only; no repo files modified)

---

## [LOW] /api/screener/custom คืน matched_rules เป็นสตริงว่างเมื่อผู้เรียกไม่ได้ใส่ description

**ไฟล์** `/home/da00/code/Vaultis/backend/routers/screener.py:66 + /home/da00/code/Vaultis/backend/screener/engine.py:153`

**อาการ**
router สร้าง `ScreenerRule(description=str(rule.get("description", "")))` แล้ว engine เก็บ `matched = [r.description for r, passed in rule_results if passed]` เมื่อ rule dict ไม่มีคีย์ description ผลลัพธ์จึงเป็น `"matched_rules": [""]` — รายการเหตุผลที่มีสมาชิกแต่ไม่มีเนื้อหา

**หลักฐาน (รันจริง)**
```
ไม่ใส่ description:
  POST /api/screener/custom {"symbols":[...], "rules":[{"field":"rsi","operator":"lt","value":70}]}
  → {"symbol":"VOO", "matched_rules": [""], "signal_strength": 7.0, ...}  (ทั้ง 5 ตัวเหมือนกันหมด)
ใส่ description:
  POST /api/screener/custom {"rules":[{"field":"rsi","operator":"lt","value":90,"description":"RSI ต่ำกว่า 90"}]}
  → matched_rules of first: ['RSI ต่ำกว่า 90']
```

**ผลกระทบ**
ผู้เรียก/หน้าจอที่วนแสดง matched_rules จะได้บุลเล็ตเปล่า ๆ บอกไม่ได้ว่าผ่านเพราะกฎข้อไหน ระดับต่ำเพราะ description เป็นเมทาดาทาที่ผู้เรียกส่งเอง และตัวเลข/สัญญาณไม่ผิด

**แนวแก้ที่เสนอ**
ใน engine.py:153 กรองค่าว่างทิ้ง หรือใส่ค่าแทนที่อ่านรู้เรื่องจากตัวกฎเอง เช่น `r.description or f"{r.field} {r.operator} {r.value}"` เพื่อให้เหตุผลมีเนื้อหาเสมอ

**พบในรอบ** T9 — integration test against the freshly-built containers (read-only; no repo files modified)

---

## [LOW] ป้ายหมวดใน sidebar ทับปุ่มนำทาง

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py:349-395 (_render_custom_sidebar) + CSS ใน _inject_premium_theme`

**อาการ**
ป้ายหมวด (`<p class="nav-group">`) วางทับตัวอักษรของปุ่มบรรทัดถัดไป: "VAULTIS" ทับ "MAIN", "ANALYSIS" ทับ "Backtest", "AI & ALERTS" ทับ "AI Advisor", "SYSTEM" ทับ "Settings"

**หลักฐาน (รันจริง)**
```
เห็นชัดในสกรีนช็อตจากเบราว์เซอร์จริงทั้ง /tmp/t9/shot-overview.png และ /tmp/t9/p-Technical_Signals.png (viewport 1500x1200) — ข้อความหมวดกับข้อความปุ่มซ้อนกันจนอ่านลำบาก ปุ่มยังกดได้ปกติ (คลิกครบ 13 หน้าสำเร็จ)
```

**ผลกระทบ**
แค่ความสวยงาม/อ่านยาก ไม่กระทบการใช้งานหรือตัวเลข

**แนวแก้ที่เสนอ**
ปรับ CSS ของ `.nav-group` — ตั้ง `margin-bottom` ให้พอ และเลี่ยง negative margin / absolute positioning ที่ทำให้ซ้อนกับ container ของ st.button

**พบในรอบ** T9 — integration test against the freshly-built containers (read-only; no repo files modified)

---

## [LOW] "Backend Router Map" ใน CLAUDE.md ครอบคลุมแค่ 11 จาก 18 prefix ที่มีจริง — 8 กลุ่ม endpoint ไม่ถูกพูดถึงในเอกสารเลย

**ไฟล์** `/home/da00/code/Vaultis/CLAUDE.md`

**อาการ**
ตาราง "Backend Router Map" ประกาศตัวเองว่าเป็นแผนที่ router แต่ลิสต์แค่ 11 prefix · README.md บรรทัด 82 เขียน "รวม router ทั้งหมด: ETF, backtest, forecast, etf_analysis, portfolio, analysis, alerts, ai, sentiment, screener, websocket" ซึ่งก็ตกไป 8 ไฟล์เช่นกัน ทั้งที่ขึ้นต้นด้วยคำว่า "ทั้งหมด"

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app -e VAULTIS_DB_PATH=/tmp/t10.db tests python - (ดึง prefix จาก backend.main.app.routes เทียบกับตารางใน CLAUDE.md)
ผลจริง:
  prefix ที่มีจริง (18): /api/ai, /api/alerts, /api/analysis, /api/backtest, /api/cashflow, /api/dca, /api/debt, /api/emergency-fund, /api/etf, /api/forecast, /api/goals, /api/macro, /api/networth, /api/portfolio, /api/reports, /api/screener, /api/sentiment, /api/transactions
  CLAUDE.md ลิสต์ไว้ (11)
  prefix ที่เอกสารไม่ได้พูดถึงเลย: ['/api/cashflow', '/api/dca', '/api/debt', '/api/emergency-fund', '/api/goals', '/api/macro', '/api/networth', '/api/reports']
```

**ผลกระทบ**
คนที่อ่าน CLAUDE.md เพื่อหา endpoint (รวมถึง Claude Code เอง ซึ่ง CLAUDE.md คือ context หลัก) จะไม่รู้ว่า `/api/debt`, `/api/networth`, `/api/goals`, `/api/reports` มีอยู่ ⇒ เสี่ยงสร้าง route ซ้ำ ซึ่งเป็นบั๊กที่ tests/test_route_uniqueness.py ถูกเขียนขึ้นมากันพอดี

**แนวแก้ที่เสนอ**
เติม 8 แถวที่ขาดในตาราง Backend Router Map (goals, reports, networth, cashflow, debt, emergency_fund, rebalance→/api/portfolio/rebalance, และ /api/dca /api/macro ที่อยู่ใน analysis.py) · แก้ README บรรทัด 82 ให้ครบหรือเปลี่ยนคำว่า "ทั้งหมด" · พิจารณาเพิ่ม assertion ใน tests/test_docs_and_deps.py ว่าทุก prefix ที่ app ประกาศต้องปรากฏใน CLAUDE.md

**พบในรอบ** T10

---

## [LOW] คำสั่งในบล็อก Commands ของ CLAUDE.md (`pytest tests/test_screener.py`) ไม่รันเทสต์สักตัวแล้ว

**ไฟล์** `/home/da00/code/Vaultis/CLAUDE.md`

**อาการ**
CLAUDE.md ยกตัวอย่าง `pytest tests/test_screener.py   # single file` แต่หลังติดมาร์ก `network` + `addopts = -m "not network"` ใน pytest.ini ไฟล์นี้ถูก deselect ทั้งไฟล์ · เทสต์ 0 ตัวถูกรัน (เช่นเดียวกับ test_backtest.py / test_forecast.py / test_etf_analysis.py)

**หลักฐาน (รันจริง)**
```
docker compose --profile dev run --rm -v /home/da00/code/Vaultis:/app tests bash -lc 'pytest -q tests/test_screener.py'
ผลจริง: `2 deselected, 1 warning in 0.37s`
exit code = 5 (NO_TESTS_COLLECTED) · 3 ไฟล์ network อื่นรวมกันก็ exit 5 เช่นกัน
(อ้างอิง: pytest -q --collect-only -m network → 5/1305 tests collected)
```

**ผลกระทบ**
คนที่ทำตามเอกสารเห็นคำสั่งจบเงียบ ๆ แล้วเข้าใจว่า "screener ผ่าน" ทั้งที่ไม่มีอะไรรัน — เป็นความมั่นใจปลอมชนิดเดียวกับที่ MEMORY.md เตือนไว้เรื่อง image ที่ไม่ mount ทับ

**แนวแก้ที่เสนอ**
เปลี่ยนตัวอย่างใน CLAUDE.md เป็นไฟล์ที่รันได้จริง (เช่น `pytest tests/test_money_math.py`) และเพิ่มบรรทัดอธิบายว่า 4 ไฟล์ network ต้องใช้ `pytest -m network tests/test_screener.py`

**พบในรอบ** T10

---

## [LOW] dashboard/app.py มี UTF-8 BOM อยู่หน้าไฟล์ (ไฟล์เดียวใน repo)

**ไฟล์** `/home/da00/code/Vaultis/dashboard/app.py`

**อาการ**
ไบต์แรกของไฟล์คือ EF BB BF · Python import ได้ปกติ (ตัวอ่านไฟล์ของ interpreter ตัด BOM ให้) แต่เครื่องมือใด ๆ ที่อ่านด้วย `read_text(encoding="utf-8")` แล้ว `ast.parse` จะพังทันที

**หลักฐาน (รันจริง)**
```
for f in $(git ls-files '*.py' '*.md' '*.json' '*.yml'); do head -c3 "$f" | od -An -tx1 | grep -q 'ef bb bf' && echo "BOM: $f"; done
ผลจริง: `BOM: dashboard/app.py` (ไฟล์เดียว)
และตอนผมพยายาม ast.parse ครั้งแรก: `SyntaxError: invalid non-printable character U+FEFF` ที่บรรทัด 1
```

**ผลกระทบ**
ไม่กระทบรันไทม์ แต่ทำให้ linter / codemod / เทสต์ที่ตรวจโครงสร้างไฟล์ (เช่นเทสต์ตรึงรายการหน้าจอที่ควรมีตามข้อ page_options) เขียนยากขึ้นและพังแบบงง ๆ ถ้าไม่ใช้ utf-8-sig

**แนวแก้ที่เสนอ**
ตัด BOM ออก (`sed -i '1s/^\xEF\xBB\xBF//'` หรือบันทึกใหม่เป็น UTF-8 ธรรมดา) — บรรทัด `# -*- coding: utf-8 -*-` ที่ตามมาทำหน้าที่นั้นอยู่แล้ว

**พบในรอบ** T10

---

