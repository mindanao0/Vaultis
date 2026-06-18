import asyncio

import yfinance

from backend.screener.crossover_detector import CrossoverDetector
from backend.screener.engine import ScreenerEngine
from backend.screener.presets import PRESETS, get_preset


async def test():
    symbols = ["VOO", "QQQM", "SCHD", "XLV", "GLDM"]
    engine = ScreenerEngine()

    print("=== Testing all presets ===")
    for preset_name in PRESETS:
        preset = get_preset(preset_name)
        results = engine.run(symbols, preset)
        print(f"\nPreset: {preset_name}")
        if results:
            for r in results:
                print(f"  ✅ {r.symbol} | strength={r.signal_strength} | {r.matched_rules}")
        else:
            print("  — no signals")

    print("\n=== Testing CrossoverDetector ===")
    detector = CrossoverDetector()
    df = yfinance.download("VOO", period="1y", interval="1d", progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    print(f"MACD cross: {detector.detect_macd_cross(df)}")
    print(f"Golden cross: {detector.detect_golden_cross(df)}")
    print(f"BB squeeze: {detector.detect_bb_squeeze(df)}")
    print(f"Volume spike: {detector.detect_volume_spike(df)}")
    print("✅ All tests passed")


asyncio.run(test())
