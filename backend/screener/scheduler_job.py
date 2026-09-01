import asyncio
import logging

from backend.screener.engine import ScreenerEngine
from backend.screener.history_service import ScreenerHistoryService
from backend.screener.notifier import ScreenerNotifier
from backend.screener.presets import get_preset

logger = logging.getLogger(__name__)

SYMBOLS = ["VOO", "QQQM", "SCHD", "XLV", "GLDM"]
# เพิ่ม overbought_warning: ผู้ใช้ถือของจริง ต้องได้คำเตือนฝั่งแพงด้วย ไม่ใช่แค่สัญญาณซื้อ (AUDIT.md M14)
PRESETS_TO_RUN = ["oversold_momentum", "golden_cross_alert", "bb_breakout_watch", "overbought_warning"]


async def run_daily_screener():
    """สแกนรายวัน 07:00 — ถูกเรียกโดย APScheduler บน event loop เดียวกับ uvicorn.

    AUDIT_2026-08-06 ข้อ B6:
    * **B6.1** ``engine`` ยิง ``yfinance.download()`` แบบ sync ตรง ๆ ถ้าเรียกบน loop
      API ทั้งตัวค้างตลอดที่สแกน (วัดได้ 0.95 วิรวดเดียว จากสตับที่ช้ากว่าของจริงมาก)
      — กฎเดียวกับที่ ``backend/routers/websocket.py`` เขียนกำกับไว้แล้ว
      จึงต้องผลักงาน blocking ออกไปที่เธรดด้วย ``asyncio.to_thread``
    * **B6.2** เดิมวน 4 พรีเซ็ตแล้วให้ engine ดึงราคาใหม่ทุกรอบ = 20 คำขอต่อเช้า
      สำหรับ 5 สัญลักษณ์ ตอนนี้ดึงครั้งเดียวแล้วส่งเฟรมเดิมเข้าไปทุกพรีเซ็ต
    """
    engine = ScreenerEngine()
    notifier = ScreenerNotifier()
    history = ScreenerHistoryService()

    frames, errors = await asyncio.to_thread(engine.fetch_frames, SYMBOLS)
    usable = [s for s in SYMBOLS if s in frames]
    if errors:
        # "ดึงไม่สำเร็จ" ≠ "ไม่มีสัญญาณ" — ต้องดังออกมา ไม่ใช่หายไปจากรายงานเฉย ๆ
        logger.error(
            "[screener] ดึงราคาไม่สำเร็จ %d/%d สัญลักษณ์: %s",
            len(errors),
            len(SYMBOLS),
            "; ".join(errors),
        )

    all_results = []
    for preset_name in PRESETS_TO_RUN:
        preset = get_preset(preset_name)
        results = await asyncio.to_thread(engine.run, usable, preset, frames)
        errors.extend(results.errors)
        if results:
            all_results.extend(results)
            await history.save_results(results, preset_name)

    if all_results:
        summary = await notifier.build_ai_summary(all_results, "daily_scan")
        if errors:
            summary = f"{summary}\n\n⚠️ ตรวจไม่ได้ {len(errors)} รายการ: {'; '.join(errors)}"
        await notifier.send_telegram(all_results, summary)
        print(f"[screener] sent {len(all_results)} signals to Telegram")
    elif errors:
        print(f"[screener] ไม่มีสัญญาณวันนี้ — แต่ตรวจไม่ได้ {len(errors)} รายการ: {'; '.join(errors)}")
    else:
        print("[screener] no signals today")
