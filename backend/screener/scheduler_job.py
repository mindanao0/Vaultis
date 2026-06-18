from backend.screener.engine import ScreenerEngine
from backend.screener.history_service import ScreenerHistoryService
from backend.screener.notifier import ScreenerNotifier
from backend.screener.presets import get_preset


async def run_daily_screener():
    symbols = ["VOO", "QQQM", "SCHD", "XLV", "GLDM"]
    presets_to_run = ["oversold_momentum", "golden_cross_alert", "bb_breakout_watch"]

    engine = ScreenerEngine()
    notifier = ScreenerNotifier()
    history = ScreenerHistoryService()

    all_results = []
    for preset_name in presets_to_run:
        preset = get_preset(preset_name)
        results = engine.run(symbols, preset)
        if results:
            all_results.extend(results)
            await history.save_results(results, preset_name)

    if all_results:
        summary = await notifier.build_ai_summary(all_results, "daily_scan")
        await notifier.send_telegram(all_results, summary)
        print(f"[screener] sent {len(all_results)} signals to Telegram")
    else:
        print("[screener] no signals today")
