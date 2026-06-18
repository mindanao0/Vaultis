import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.etf_info_service import ETFInfoService
from backend.services.technical_service import TechnicalService
from backend.services.analysis_service import AnalysisService


async def test():
    symbols = ["VOO", "QQQM", "SCHD", "XLV", "GLDM"]
    info_svc = ETFInfoService()
    tech_svc = TechnicalService()
    analysis_svc = AnalysisService()

    for symbol in symbols:
        print(f"\n=== {symbol} ===")
        info = await info_svc.get_info(symbol)
        tech = await tech_svc.get_technical(symbol)
        signal = analysis_svc.compute_overall_signal(tech)
        print(f"Price: {info.price}")
        print(f"RSI: {tech.rsi}")
        print(f"Signal: {tech.signal}")
        print(f"Overall: {signal}")
        print(f"Golden Cross: {tech.golden_cross}")
        assert info.symbol == symbol
        print(f"✅ {symbol} passed")


asyncio.run(test())
