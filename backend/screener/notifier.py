from __future__ import annotations

import asyncio
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from analysis.llm import LLMDisabledError, chat_text
from backend.screener.models import ScreenerResult

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

SYSTEM_PROMPT = """
You are Vaultis AI screener analyst.
Always respond in Thai.
Be concise — max 300 words.
ตัวเลขและสัญญาณคำนวณมาแล้ว — อธิบายเท่านั้น ห้ามคำนวณใหม่
End with: "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน"
""".strip()


DISCLAIMER = "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน"


class ScreenerNotifier:
    def _plain_summary(self, results: list[ScreenerResult], preset_name: str) -> str:
        """สรุปแบบไม่ใช้ AI — ข้อมูลเดียวกัน ไม่มีค่าใช้จ่าย."""
        lines = [f"พบ {len(results)} สัญญาณจาก preset: {preset_name}", ""]
        for row in sorted(results, key=lambda r: r.signal_strength, reverse=True):
            rules = ", ".join(row.matched_rules) or "-"
            lines.append(f"• {row.symbol} ${row.price:,.2f} | ความแรง {row.signal_strength:.1f}/10 | {rules}")
        lines.append("")
        lines.append(DISCLAIMER)
        return "\n".join(lines)

    async def build_ai_summary(
        self,
        results: list[ScreenerResult],
        preset_name: str,
        user_initiated: bool = False,
    ) -> str:
        """คำอธิบายจาก AI (มีค่าใช้จ่าย) — งานอัตโนมัติจะได้สรุปแบบไม่ใช้ AI แทน."""
        if not results:
            return f"ไม่พบสัญญาณที่เข้าเงื่อนไขในวันนี้\n\n{DISCLAIMER}"

        lines = [f"วันนี้พบ {len(results)} สัญญาณจาก preset: {preset_name}", ""]
        for row in results:
            lines.append(
                f"- symbol: {row.symbol}, price: {row.price:.2f}, "
                f"signal_strength: {row.signal_strength}, matched_rules: {', '.join(row.matched_rules)}"
            )
        lines.append("")
        lines.append("อธิบายแต่ละ symbol และจัดลำดับความน่าสนใจ")
        user_message = "\n".join(lines)

        def _call_llm() -> str:
            return chat_text(
                SYSTEM_PROMPT,
                user_message,
                max_tokens=1000,
                temperature=0.2,
                user_initiated=user_initiated,
            )

        try:
            text = await asyncio.to_thread(_call_llm)
            if not text:
                raise RuntimeError("empty AI summary")
            return text
        except LLMDisabledError:
            # ปกติของงานอัตโนมัติ — ส่งสรุปจากตัวเลขแทน ไม่ใช่ error
            return self._plain_summary(results, preset_name)
        except Exception as e:
            print(f"[screener_notifier] build_ai_summary error: {e}")
            return self._plain_summary(results, preset_name)

    async def send_telegram(self, results: list[ScreenerResult], ai_summary: str) -> bool:
        """ส่งสัญญาณเข้า Telegram; ถ้าไม่ได้ตั้งค่า/ล้มเหลว จะ fallback ไป Discord.

        AUDIT.md M14: เดิมถ้าไม่ได้ตั้ง Telegram สัญญาณจะหายเงียบ (log อย่างเดียว)
        และไม่เคยเช็ค HTTP status ที่ Telegram ตอบกลับด้วย
        """
        if not results:
            return False

        header = "🔍 *Vaultis Screener Alert*"
        table_header = "*symbol | price | strength | signals*"
        rows = []
        for r in results:
            signals = ", ".join(r.matched_rules) if r.matched_rules else "-"
            rows.append(f"`{r.symbol} | {r.price:.2f} | {r.signal_strength:.1f} | {signals}`")
        message = "\n".join([header, table_header, *rows, "", ai_summary])

        token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
        if token and chat_id:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
            try:
                resp = await asyncio.to_thread(requests.post, url, json=payload, timeout=15)
                if resp.status_code < 400:
                    return True
                print(f"[screener_notifier] telegram {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"[screener_notifier] send_telegram error: {e}")
        else:
            print("[screener_notifier] ไม่ได้ตั้ง TELEGRAM_BOT_TOKEN/CHAT_ID — ใช้ Discord แทน")

        return await self._send_discord_fallback(results, ai_summary)

    async def _send_discord_fallback(self, results: list[ScreenerResult], ai_summary: str) -> bool:
        from alerts.notifier import send_discord_webhook
        from utils.config import load_config

        webhook_url = str(load_config()["notifications"]["discord_webhook_url"]).strip()
        if not webhook_url:
            print("[screener_notifier] ไม่มีช่องทางแจ้งเตือนเลย — สัญญาณ screener ไม่ถูกส่ง")
            return False

        lines = [
            f"• {r.symbol} ${r.price:.2f} | strength {r.signal_strength:.1f} | {', '.join(r.matched_rules) or '-'}"
            for r in results
        ]
        description = "\n".join([*lines, "", ai_summary])[:3900]
        result = await asyncio.to_thread(
            send_discord_webhook,
            webhook_url=webhook_url,
            title="🔍 Vaultis Screener Alert",
            description=description,
            is_positive=True,
            embed_color=0x3498DB,
        )
        if not result.get("success"):
            print(f"[screener_notifier] discord fallback failed: {result.get('error')}")
        return bool(result.get("success"))
