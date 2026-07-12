"""AI summary generator for backtest results (Claude Haiku 4.5 → Groq fallback)."""

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


def generate_summary(result: dict, symbol: str, user_initiated: bool = False) -> str:
    strategy_used = str(result.get("strategy_used", "unknown"))
    strategy_th = _STRATEGY_TH.get(strategy_used, strategy_used)

    user_msg = f"""สรุปผลการ Backtest สำหรับ {symbol}:

- กลยุทธ์ที่ใช้จริง: {strategy_th}
- Total Return (Strategy): {result['total_return']:.2f}%
- Benchmark Return (Buy & Hold): {result['benchmark_return']:.2f}%
- Sharpe Ratio: {result['sharpe_ratio']:.4f}
- Max Drawdown: {result['max_drawdown']:.2f}%
- Win Rate: {result['win_rate']:.2f}%
- จำนวน Trades: {result['num_trades']}
- ชนะ Benchmark: {'ใช่' if result['outperformed'] else 'ไม่ใช่'}

โปรดอธิบาย:
1. strategy ให้ผลตอบแทนเป็นยังไง (อ้างกลยุทธ์ที่ใช้จริงตามด้านบน)
2. เทียบกับ Buy and Hold ดีกว่าหรือแย่กว่า
3. Sharpe Ratio บอกอะไร
4. ควรปรับปรุงอะไร

เตือนด้วยว่าผลย้อนหลังไม่รับประกันผลในอนาคต และปิดท้ายด้วย disclaimer เสมอ"""

    return chat_text(
        SYSTEM_PROMPT, user_msg, max_tokens=1000, temperature=0.2, user_initiated=user_initiated
    )
