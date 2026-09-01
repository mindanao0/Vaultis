"""Integration test: ScreenerEngine + CrossoverDetector บนราคาจริง (ต่อเน็ตจริง).

ติด ``@pytest.mark.network`` เพราะยิง yfinance จริง — ถูกกันออกจากการรันปกติ
(``addopts = -m "not network"`` ใน pytest.ini) เรียกกลับมาด้วย ``pytest -m network``

AUDIT_2026-08-06 ข้อ 0-B: เดิมไฟล์นี้เขียน ``asyncio.run(test())`` ไว้ที่ระดับโมดูล
จึงยิงเน็ต **ตอน collect** ⇒ เน็ตล่มเมื่อไหร่ ชุดเทสต์ทั้งชุดล่มก่อนได้รันสักตัว
(`Interrupted: 2 errors during collection`) และตัวเทสต์เองไม่มี assert สักบรรทัด
(พิมพ์ผลออกจอเฉย ๆ) ห้ามเอาทั้งสองอย่างกลับมา

ตรรกะ AND/OR ของ engine ทดสอบแบบไม่ต้องต่อเน็ตที่ ``tests/test_screener_engine.py``
ไฟล์นี้เหลือหน้าที่เดียว: ยืนยันว่าเส้นทางข้อมูลจริงยังต่อกันติด
"""

import pytest
import yfinance

from backend.screener.crossover_detector import CrossoverDetector
from backend.screener.engine import ScreenerEngine
from backend.screener.models import ScreenerResult
from backend.screener.presets import PRESETS, get_preset

pytestmark = pytest.mark.network

_SYMBOLS = ["VOO", "QQQM", "SCHD", "XLV", "GLDM"]


def test_all_presets_run_on_live_prices():
    engine = ScreenerEngine()

    for preset_name in PRESETS:
        preset = get_preset(preset_name)
        results = engine.run(_SYMBOLS, preset)

        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, ScreenerResult)
            assert r.symbol in _SYMBOLS
            assert r.preset_name == preset_name
            assert 0.0 <= r.signal_strength <= 10.0
            assert r.matched_rules, "ผลลัพธ์ที่ไม่มีกฎไหน match ไม่ควรถูกรายงานว่าเป็นสัญญาณ"
            assert r.price > 0, "ราคาต้องเป็นราคาจริง ห้ามเป็น 0 จากข้อมูลที่ดึงไม่ได้"

        # เรียงจากแรงไปเบา — หน้าจอและ notifier พึ่งลำดับนี้
        strengths = [r.signal_strength for r in results]
        assert strengths == sorted(strengths, reverse=True)


def test_crossover_detector_on_live_prices():
    detector = CrossoverDetector()
    df = yfinance.download("VOO", period="1y", interval="1d", progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    assert not df.empty, "ดึงราคา VOO ไม่ได้ — เทสต์นี้ต้องต่อเน็ตจริง"

    assert detector.detect_macd_cross(df) in {"bullish", "bearish", None}
    # ใช้ `in (True, False)` ไม่ใช่ isinstance — detector บางตัวคืน numpy.bool_
    assert detector.detect_golden_cross(df) in (True, False)
    assert detector.detect_bb_squeeze(df) in (True, False)
    assert detector.detect_volume_spike(df) in (True, False)
