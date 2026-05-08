"""ETF overall signal rules and Groq-backed Thai summaries."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import Groq

from ..models.etf_models import ETFAnalysis, TechnicalIndicators

ROOT_DIR = Path(__file__).resolve().parents[2]

_GROQ_MODEL = "llama-3.3-70b-versatile"
_SYSTEM_PROMPT = """
You are Vaultis AI, an ETF investment analyst.
Always respond in Thai.
Never give direct buy/sell advice.
Always end with disclaimer: "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน"
""".strip()

_DISCLAIMER = "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน"


def _cell(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class AnalysisService:
    def compute_overall_signal(self, technical: TechnicalIndicators) -> str:
        sig = (technical.signal or "").strip().lower()
        rsi = technical.rsi
        ma50 = technical.ma50
        ma200 = technical.ma200

        if sig == "bullish" and technical.golden_cross and rsi is not None and rsi < 65:
            return "strong_buy"
        if (
            sig == "bullish"
            and ma50 is not None
            and ma200 is not None
            and ma50 > ma200
        ):
            return "buy"
        if sig == "bearish" and technical.death_cross:
            return "sell"
        if sig == "bearish" and rsi is not None and rsi < 30:
            return "strong_sell"
        return "hold"

    def _etf_dataset_lines(self, analysis: ETFAnalysis) -> list[str]:
        info = analysis.info
        t = analysis.technical
        lines: list[str] = []
        lines.append(f"symbol\t{_cell(analysis.symbol)}")
        lines.append(f"profile\t{_cell(info.profile)}")
        lines.append(f"overall_signal\t{_cell(analysis.overall_signal)}")
        lines.append("")
        lines.append("=== Technical ===")
        lines.append(f"price\t{_cell(t.price)}")
        lines.append(f"rsi\t{_cell(t.rsi)}")
        lines.append(f"macd\t{_cell(t.macd)}")
        lines.append(f"macd_signal\t{_cell(t.macd_signal)}")
        lines.append(f"macd_hist\t{_cell(t.macd_hist)}")
        lines.append(f"ma50\t{_cell(t.ma50)}")
        lines.append(f"ma200\t{_cell(t.ma200)}")
        lines.append(f"golden_cross\t{_cell(t.golden_cross)}")
        lines.append(f"death_cross\t{_cell(t.death_cross)}")
        lines.append(f"signal\t{_cell(t.signal)}")
        lines.append("")
        lines.append("=== Fundamental (ETF metrics) ===")
        lines.append(f"expense_ratio\t{_cell(info.expense_ratio)}")
        lines.append(f"dividend_yield\t{_cell(info.dividend_yield)}")
        lines.append(f"ytd_return\t{_cell(info.ytd_return)}")
        lines.append(f"beta\t{_cell(info.beta)}")
        return lines

    def _response_outline_lines(self, compare_mode: bool) -> list[str]:
        lines: list[str] = []
        lines.append("")
        lines.append("โครงสร้างคำตอบ (ภาษาไทย):")
        lines.append("1. ETF นี้คืออะไร เหมาะกับนักลงทุนแบบไหน")
        lines.append("2. สถานะ technical ตอนนี้")
        lines.append("3. สัญญาณโดยรวม + เหตุผล")
        next_n = 4
        if compare_mode:
            lines.append(
                f"{next_n}. เปรียบเทียบกับ ETF อื่นในพอร์ต (เช่น core เทียบ sector/dividend/commodity) "
                "โดยสรุปลักษณะและความเสี่ยงเชิงเปรียบเทียบ ไม่ชี้ให้ซื้อหรือขายรายตัว"
            )
            next_n += 1
        lines.append(
            f"{next_n}. ปิดท้ายด้วย disclaimer ตาม system prompt "
            '(ข้อความ "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน")'
        )
        return lines

    def _build_user_message(
        self,
        analysis: ETFAnalysis,
        compare_mode: bool,
        *,
        additional_analyses: list[ETFAnalysis] | None = None,
    ) -> str:
        lines: list[str] = []
        header = (
            "ชุด ETF สำหรับเปรียบเทียบ" if compare_mode else "ข้อมูล ETF สำหรับวิเคราะห์"
        )
        lines.append(f"{header} (ใช้เฉพาะข้อมูลด้านล่าง)")
        lines.append("")
        if additional_analyses:
            lines.append(f"=== หลัก: {analysis.symbol} ===")
        lines.extend(self._etf_dataset_lines(analysis))
        if additional_analyses:
            for other in additional_analyses:
                lines.append("")
                lines.append(f"=== เพิ่มเติม: {other.symbol} ===")
                lines.extend(self._etf_dataset_lines(other))
        lines.extend(self._response_outline_lines(compare_mode))
        return "\n".join(lines)

    def _call_groq(self, user_content: str) -> str:
        load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key or api_key == "your_key_here":
            raise ValueError("missing GROQ_API_KEY")

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=_GROQ_MODEL,
            temperature=0.1,
            max_tokens=600,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    async def get_ai_summary(
        self,
        analysis: ETFAnalysis,
        compare_mode: bool = False,
        *,
        additional_analyses: list[ETFAnalysis] | None = None,
    ) -> str:
        user_content = self._build_user_message(
            analysis,
            compare_mode=compare_mode,
            additional_analyses=additional_analyses,
        )
        try:
            text = await asyncio.to_thread(self._call_groq, user_content)
        except Exception:
            text = (
                "ไม่สามารถเรียกบริการวิเคราะห์ AI ได้ในขณะนี้ "
                "กรุณาตรวจสอบ GROQ_API_KEY และการเชื่อมต่ออินเทอร์เน็ต"
            )

        if _DISCLAIMER not in text:
            sep = "\n\n" if text else ""
            text = f"{text}{sep}{_DISCLAIMER}"
        return text
