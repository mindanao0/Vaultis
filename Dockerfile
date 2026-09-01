# Vaultis — image เดียว ใช้ร่วมกันทั้ง 3 service (backend / dashboard / scheduler)
# ต่างกันแค่ command ใน docker-compose.yml
#
# python 3.12 (ไม่ใช่ 3.11 แบบเดิม): ชุดเทสต์ 301 ตัวถูกยืนยันบน 3.12
# และ requirements.txt pin ไว้ทั้งหมด — เวอร์ชัน interpreter ต้องตรงกับที่ทดสอบ
#
# หมายเหตุขนาด image: build-essential **น่าจะตัดออกได้** — ตรวจ 2026-07-28 พบว่า
# ทุกแพ็กเกจที่มี native extension (numpy scipy pyarrow numba psycopg2 matplotlib …)
# มี manylinux wheel ครบ และแพ็กเกจที่เหลือเป็น sdist แค่ 2 ตัวคือ ta กับ vectorbt
# ซึ่งเป็น pure Python ทั้งคู่ (0 ไฟล์ .so หลังติดตั้ง) จึงไม่มีอะไรต้องคอมไพล์
# แต่ยังไม่ตัดออกเพราะ **ยังไม่เคย build จริง** — ให้ build ผ่านสักครั้งก่อน
# แล้วค่อยลองตัด (ระวัง prophet/cmdstanpy ที่รันไบนารี Stan ต้องมี libstdc++/libgomp)

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Bangkok \
    # ข้อมูลทั้งหมดอยู่ใต้ /data ซึ่งถูก mount เป็น volume — ไม่อยู่ใน image layer
    VAULTIS_DB_PATH=/data/vaultis.db \
    # container รันเป็น uid ของ host (ดู docker-compose.yml) ซึ่งเขียน site-packages
    # และ /root ไม่ได้ → ต้องชี้ทุก cache ไปที่ /tmp (mode 1777 เขียนได้ทุก uid)
    # ไม่งั้น vectorbt ล่มตั้งแต่ import: numba @njit(cache=True) หา locator ไม่ได้
    # (RuntimeError: cannot cache function 'set_seed_nb') — เจอตอนทดสอบจริง 2026-07-28
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    NUMBA_CACHE_DIR=/tmp/numba-cache \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

# ติดตั้ง dependencies ก่อน copy โค้ด — แก้โค้ดแล้ว rebuild ไม่ต้องลง deps ใหม่
COPY requirements.txt ./
RUN pip install -r requirements.txt

# fonts-tlwg-garuda (328 KB): ฟอนต์ไทยของรายงาน PDF — **จำเป็น ไม่ใช่ของตกแต่ง**
# reportlab ที่ไม่มีฟอนต์ไทยจะ *ไม่* error แต่สลับไป ZapfDingbats แล้ววาด ■ หนึ่งตัว
# ต่ออักษรไทยหนึ่งตัว (AUDIT_2026-08-06 ข้อ H6) utils/pdf_export.py จึงยอมพิมพ์ไทย
# เฉพาะเมื่อหาฟอนต์เจอ — ไม่มีฟอนต์ = แทนด้วยหมายเหตุอังกฤษ แปลว่าผู้ใช้จ่ายค่า AI
# แล้วอ่านบทวิเคราะห์ที่ซื้อมาไม่ได้ทั้งหน้า
# ลงเฉพาะตระกูล Garuda (ให้ Garuda.ttf + Garuda-Bold.ttf ตรงกับที่ _SYSTEM_FONT_GLOBS
# มองหาเป็นอันดับแรก) ไม่ใช่ metapackage fonts-thai-tlwg ทั้งชุดที่ใหญ่กว่าโดยไม่ได้ใช้
#
# **วางไว้หลัง pip โดยตั้งใจ**: ฟอนต์เป็นเลเยอร์ 328 KB ที่แทบไม่เปลี่ยน ถ้าเอาไปรวมกับ
# apt ด้านบนจะทำให้ cache ของ `pip install -r requirements.txt` (prophet/vectorbt/numba)
# ตายทั้งชั้น = rebuild ครั้งถัดไปลง deps ใหม่หมดเพื่อฟอนต์ตัวเดียว
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-tlwg-garuda \
    && rm -rf /var/lib/apt/lists/*


# --- ภาพสำหรับใช้งานจริง ---
FROM base AS runtime

COPY . .

# โฟลเดอร์ข้อมูลถูก .dockerignore กันไว้ ต้องสร้างเปล่า ๆ ให้โค้ดเขียนได้
# (tracker.py / price_alert.py สร้าง parent dir เองอยู่แล้ว แต่สร้างไว้ก่อนกันพลาด)
RUN mkdir -p /data /app/portfolio/data /app/alerts/data

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]


# --- ภาพสำหรับรันเทสต์ (docker compose --profile dev run tests) ---
FROM base AS dev

COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY . .
RUN mkdir -p /data /app/portfolio/data /app/alerts/data

CMD ["pytest", "-q"]
