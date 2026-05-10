"""Groq AI summary generator for backtest results."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

GROQ_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "คุณเป็น quant analyst อธิบายผลการ backtest เป็นภาษาไทย กระชับ ไม่เกิน 200 คำ"
)


def generate_summary(result: dict, symbol: str) -> str:
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    user_msg = f"""สรุปผลการ Backtest สำหรับ {symbol}:

- Total Return (Strategy): {result['total_return']:.2f}%
- Benchmark Return (Buy & Hold): {result['benchmark_return']:.2f}%
- Sharpe Ratio: {result['sharpe_ratio']:.4f}
- Max Drawdown: {result['max_drawdown']:.2f}%
- Win Rate: {result['win_rate']:.2f}%
- จำนวน Trades: {result['num_trades']}
- ชนะ Benchmark: {'ใช่' if result['outperformed'] else 'ไม่ใช่'}

โปรดอธิบาย:
1. strategy ให้ผลตอบแทนเป็นยังไง
2. เทียบกับ Buy and Hold ดีกว่าหรือแย่กว่า
3. Sharpe Ratio บอกอะไร
4. ควรปรับปรุงอะไร

ปิดท้ายด้วย disclaimer เสมอ"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
        max_tokens=400,
    )

    return response.choices[0].message.content
