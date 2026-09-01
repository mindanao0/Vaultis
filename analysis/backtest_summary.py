"""AI summary generator for backtest results (Claude Sonnet 5 ผ่าน analysis/llm.py)."""

from __future__ import annotations

from analysis.llm import chat_text

SYSTEM_PROMPT = (
    "คุณเป็น quant analyst อธิบายผลการ backtest เป็นภาษาไทย กระชับ ไม่เกิน 200 คำ\n"
    "ตัวเลขทั้งหมดคำนวณมาแล้ว — ห้ามคำนวณใหม่หรือสร้างตัวเลขที่ไม่มีในข้อมูล"
)

_STRATEGY_TH = {
    "rsi_macd_3day_window": "RSI oversold + MACD bullish cross (หน้าต่าง 3 วัน)",
    "rsi_only_fallback": "RSI อย่างเดียว (fallback เพราะเงื่อนไขรวมไม่เกิดสัญญาณเลย)",
}

_NO_DATA_TH = "ไม่มีข้อมูล (กลยุทธ์ไม่เคยเข้าเทรดจึงไม่นิยาม)"

# ``None`` = ไม่นิยาม ห้ามแปลงเป็น "ไม่ใช่" เพราะ "ไม่ชนะดัชนี" กับ "เทียบไม่ได้"
# เป็นคนละข้อสรุปกัน (AUDIT_2026-08-06 B3.1)
_OUTPERFORMED_TH = {True: "ใช่", False: "ไม่ใช่", None: "เทียบไม่ได้ (ไม่มีเทรด)"}


def _fmt(value: float | None, digits: int, suffix: str = "") -> str:
    """เลขที่ไม่นิยามต้องอ่านออกว่าไม่มีข้อมูล — ห้ามให้ LLM เห็นเป็น 0.00%."""
    if value is None:
        return _NO_DATA_TH
    return f"{value:.{digits}f}{suffix}"


def generate_summary(result: dict, symbol: str, user_initiated: bool = False) -> str:
    strategy_used = str(result.get("strategy_used", "unknown"))
    strategy_th = _STRATEGY_TH.get(strategy_used, strategy_used)
    detail = result.get("detail")
    detail_line = f"\n- หมายเหตุจากระบบ: {detail}" if detail else ""

    user_msg = f"""สรุปผลการ Backtest สำหรับ {symbol}:

- กลยุทธ์ที่ใช้จริง: {strategy_th}
- Total Return (Strategy): {_fmt(result['total_return'], 2, '%')}
- Benchmark Return (Buy & Hold): {_fmt(result['benchmark_return'], 2, '%')}
- Sharpe Ratio: {_fmt(result['sharpe_ratio'], 4)}
- Max Drawdown: {_fmt(result['max_drawdown'], 2, '%')}
- Win Rate: {_fmt(result['win_rate'], 2, '%')}
- จำนวน Trades: {result['num_trades']}
- ชนะ Benchmark: {_OUTPERFORMED_TH[result['outperformed']]}{detail_line}

ช่องที่เขียนว่า "{_NO_DATA_TH}" คือไม่มีตัวเลข ห้ามอ่านเป็น 0 และห้ามเดาค่าแทน

โปรดอธิบาย:
1. strategy ให้ผลตอบแทนเป็นยังไง (อ้างกลยุทธ์ที่ใช้จริงตามด้านบน)
2. เทียบกับ Buy and Hold ดีกว่าหรือแย่กว่า
3. Sharpe Ratio บอกอะไร
4. ควรปรับปรุงอะไร

เตือนด้วยว่าผลย้อนหลังไม่รับประกันผลในอนาคต และปิดท้ายด้วย disclaimer เสมอ"""

    return chat_text(
        SYSTEM_PROMPT, user_msg, max_tokens=1000, temperature=0.2, user_initiated=user_initiated
    )
