from __future__ import annotations

import asyncio
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from groq import Groq

from backend.screener.models import ScreenerResult

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

GROQ_MODEL = "llama-3.3-70b-versatile"
SYSTEM_PROMPT = """
You are Vaultis AI screener analyst.
Always respond in Thai.
Be concise — max 300 words.
End with: "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน"
""".strip()


class ScreenerNotifier:
    async def build_ai_summary(self, results: list[ScreenerResult], preset_name: str) -> str:
        if not results:
            return "ไม่พบสัญญาณที่เข้าเงื่อนไขในวันนี้\n\nข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน"

        lines = [f"วันนี้พบ {len(results)} สัญญาณจาก preset: {preset_name}", ""]
        for row in results:
            lines.append(
                f"- symbol: {row.symbol}, price: {row.price:.2f}, "
                f"signal_strength: {row.signal_strength}, matched_rules: {', '.join(row.matched_rules)}"
            )
        lines.append("")
        lines.append("อธิบายแต่ละ symbol และจัดลำดับความน่าสนใจ")
        user_message = "\n".join(lines)

        def _call_groq() -> str:
            api_key = (os.getenv("GROQ_API_KEY") or "").strip()
            if not api_key or api_key == "your_key_here":
                raise ValueError("missing GROQ_API_KEY")
            client = Groq(api_key=api_key)
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                temperature=0.1,
                max_tokens=500,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
            )
            return (response.choices[0].message.content or "").strip()

        try:
            text = await asyncio.to_thread(_call_groq)
            if not text:
                raise RuntimeError("empty AI summary")
            return text
        except Exception as e:
            print(f"[screener_notifier] build_ai_summary error: {e}")
            return "ไม่สามารถสร้าง AI summary ได้ในขณะนี้\n\nข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน"

    async def send_telegram(self, results: list[ScreenerResult], ai_summary: str):
        if not results:
            return

        token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
        if not token or not chat_id:
            print("[screener_notifier] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")
            return

        header = "🔍 *Vaultis Screener Alert*"
        table_header = "*symbol | price | strength | signals*"
        rows = []
        for r in results:
            signals = ", ".join(r.matched_rules) if r.matched_rules else "-"
            rows.append(f"`{r.symbol} | {r.price:.2f} | {r.signal_strength:.1f} | {signals}`")

        message = "\n".join([header, table_header, *rows, "", ai_summary])
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}

        try:
            await asyncio.to_thread(requests.post, url, json=payload, timeout=15)
        except Exception as e:
            print(f"[screener_notifier] send_telegram error: {e}")
