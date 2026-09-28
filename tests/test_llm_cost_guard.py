# -*- coding: utf-8 -*-
"""เทสต์ระบบคุมค่าใช้จ่าย LLM.

หลักที่ต้องคุ้มครอง: **ไม่มีเส้นทางอัตโนมัติใดเรียก LLM ได้** โดยที่ผู้ใช้ไม่ได้กดเอง
(เดิม jobs/daily_check ยิง AI ทุกวันทำการ ~22 ครั้ง/เดือน และ screener ยิงทุกวัน 07:00)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from analysis import llm


@pytest.fixture(autouse=True)
def _no_real_api(monkeypatch):
    """ถ้ามีการเรียก provider จริง = เทสต์ fail (พิสูจน์ว่าไม่มีเงินไหลออก)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    def _explode(*args, **kwargs):
        raise AssertionError("เรียก provider จริง! ต้องถูกบล็อกก่อนถึงตรงนี้")

    monkeypatch.setattr(llm, "_chat_anthropic", _explode)


class TestChatTextGate:
    def test_blocked_by_default(self, monkeypatch):
        monkeypatch.delenv("VAULTIS_LLM_AUTO", raising=False)
        with pytest.raises(llm.LLMDisabledError):
            llm.chat_text("system", "user")

    def test_blocked_when_auto_flag_off(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_LLM_AUTO", "0")
        with pytest.raises(llm.LLMDisabledError):
            llm.chat_text("system", "user")

    def test_user_initiated_reaches_provider(self, monkeypatch):
        """กดปุ่มเอง = ผ่าน gate แล้วไปถึง provider (ในเทสต์ provider ถูกแทนด้วยตัวระเบิด)."""
        monkeypatch.delenv("VAULTIS_LLM_AUTO", raising=False)
        with pytest.raises(RuntimeError, match="เรียก provider จริง") as exc_info:
            llm.chat_text("system", "user", user_initiated=True)
        assert not isinstance(exc_info.value, llm.LLMDisabledError), "ไม่ควรถูก gate บล็อก"

    def test_auto_flag_on_allows_automatic_calls(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_LLM_AUTO", "1")
        with pytest.raises(RuntimeError, match="เรียก provider จริง") as exc_info:
            llm.chat_text("system", "user")
        assert not isinstance(exc_info.value, llm.LLMDisabledError)

    @pytest.mark.parametrize("value", ["true", "TRUE", "yes", "on", "1"])
    def test_auto_flag_accepts_common_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("VAULTIS_LLM_AUTO", value)
        assert llm.auto_enabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
    def test_auto_flag_rejects_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("VAULTIS_LLM_AUTO", value)
        assert llm.auto_enabled() is False


class TestSingleProvider:
    """ถอด Groq ออกแล้ว (2026-08-02) — ห้ามมีเส้นทาง fallback กลับมาเงียบ ๆ."""

    def test_no_groq_path_left(self):
        for attr in ("_chat_groq", "_groq_available", "GROQ_MODEL"):
            assert not hasattr(llm, attr), f"{attr} ต้องถูกถอดออกแล้ว"

    def test_missing_key_fails_loudly(self, monkeypatch):
        """ไม่มีคีย์ = โยน error ที่อ่านออก ไม่ใช่คืนข้อความปลอมหรือเงียบ."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("VAULTIS_LLM_AUTO", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            llm.chat_text("system", "user", user_initiated=True)

    def test_cost_log_price_table_covers_the_active_model(self):
        """ตารางราคาต้องครอบคลุมโมเดลที่ตั้งไว้ ไม่งั้น log จะรายงานต้นทุนผิด."""
        assert llm.ANTHROPIC_MODEL in llm._MODEL_PRICES_USD_PER_MTOK


class TestAutomaticPathsAreFree:
    """เส้นทางอัตโนมัติต้องทำงานต่อได้ **โดยไม่เรียก LLM** และยังให้ตัวเลขครบ."""

    def test_monthly_advice_without_click_uses_no_llm(self, monkeypatch):
        monkeypatch.delenv("VAULTIS_LLM_AUTO", raising=False)

        from analysis import ai_advisor

        scores = [
            {"ticker": "VOO", "data_ok": True, "total_pct": 70.0, "price": 690.0,
             "ma50": 680.0, "ma200": 650.0, "rsi": 55.0, "signal": "Strong Buy"},
            {"ticker": "GLDM", "data_ok": True, "total_pct": 30.0, "price": 80.0,
             "ma50": 85.0, "ma200": 88.0, "rsi": 43.0, "signal": "Neutral"},
        ]
        monkeypatch.setattr(ai_advisor, "get_tickers", lambda: ["VOO", "GLDM"])
        monkeypatch.setattr("analysis.financial_model.build_etf_scores", lambda t: scores)
        monkeypatch.setattr("analysis.macro.get_macro_snapshot", lambda: {"vix": 15.0})
        import pandas as pd

        monkeypatch.setattr("portfolio.tracker.get_portfolio_summary", lambda: pd.DataFrame())
        monkeypatch.setattr(ai_advisor, "load_config", lambda: {"notifications": {"discord_webhook_url": ""}})

        result = ai_advisor.get_monthly_advice(budget_thb=5000, send_discord=False)

        assert result["ai_used"] is False, "งานอัตโนมัติต้องไม่เรียก AI"
        # ตัวเลขทุกอย่างต้องยังครบ — นี่คือสิ่งที่ใช้ตัดสินใจจริง
        assert result["allocation"], "แผนจัดสรรต้องยังคำนวณให้"
        assert sum(i["amount_thb"] for i in result["allocation"].values()) <= 5000
        assert "ปิดอยู่" in result["advice_text"]

    def test_monthly_advice_with_click_calls_llm(self, monkeypatch):
        monkeypatch.delenv("VAULTIS_LLM_AUTO", raising=False)

        from analysis import ai_advisor

        scores = [
            {"ticker": "VOO", "data_ok": True, "total_pct": 70.0, "price": 690.0,
             "ma50": 680.0, "ma200": 650.0, "rsi": 55.0, "signal": "Strong Buy"},
        ]
        monkeypatch.setattr(ai_advisor, "get_tickers", lambda: ["VOO"])
        monkeypatch.setattr("analysis.financial_model.build_etf_scores", lambda t: scores)
        monkeypatch.setattr("analysis.macro.get_macro_snapshot", lambda: {})
        import pandas as pd

        monkeypatch.setattr("portfolio.tracker.get_portfolio_summary", lambda: pd.DataFrame())
        monkeypatch.setattr(ai_advisor, "load_config", lambda: {"notifications": {"discord_webhook_url": ""}})
        monkeypatch.setattr(ai_advisor, "chat_text", lambda *a, **k: "คำอธิบายจาก AI")

        result = ai_advisor.get_monthly_advice(
            budget_thb=5000, send_discord=False, user_initiated=True
        )
        assert result["ai_used"] is True
        assert result["advice_text"] == "คำอธิบายจาก AI"

    def test_screener_notifier_falls_back_to_plain_summary(self, monkeypatch):
        """screener รันทุกวัน 07:00 — ต้องได้สรุปจากตัวเลข ไม่ใช่ error และไม่เสียเงิน."""
        import asyncio

        from backend.screener.models import ScreenerResult
        from backend.screener.notifier import ScreenerNotifier

        monkeypatch.delenv("VAULTIS_LLM_AUTO", raising=False)
        results = [
            ScreenerResult(
                symbol="VOO", matched_rules=["RSI < 35"], price=690.0,
                signal_strength=8.5, preset_name="oversold", timestamp="2026-07-12",
            )
        ]
        summary = asyncio.run(ScreenerNotifier().build_ai_summary(results, "oversold"))
        assert "VOO" in summary
        assert "690" in summary
        assert "ไม่ใช่คำแนะนำการลงทุน" in summary

    def test_sentiment_job_skips_without_flag(self, monkeypatch, capsys):
        """งาน sentiment รายสัปดาห์เรียก LLM หลายครั้ง — ต้องข้ามถ้าไม่เปิด flag."""
        monkeypatch.delenv("VAULTIS_LLM_AUTO", raising=False)
        from analysis.sentiment_analyzer import run_sentiment_job

        run_sentiment_job(["VOO"])
        assert "ข้าม" in capsys.readouterr().out


class TestMonthlyAiSwitch:
    """VAULTIS_MONTHLY_AI เปิด AI ให้แผน DCA ต้นเดือนงานเดียว — ดีฟอลต์ต้องไม่จ่าย."""

    def _spy_advice(self, monkeypatch):
        import main

        calls: list[dict] = []

        def fake(**kwargs):
            calls.append(kwargs)
            return {"ai_used": False, "allocation": {}, "discord_result": {"skipped": True}}

        monkeypatch.setattr(main, "get_monthly_advice", fake)
        monkeypatch.setattr(main, "load_config", lambda: {"dca": {"monthly_budget_thb": 5000, "day_of_month": 1}})
        return main, calls

    def test_monthly_job_does_not_pay_by_default(self, monkeypatch):
        monkeypatch.delenv("VAULTIS_MONTHLY_AI", raising=False)
        main, calls = self._spy_advice(monkeypatch)
        main.generate_monthly_ai_advisor_and_notify()
        assert calls and calls[0]["user_initiated"] is False

    @pytest.mark.parametrize("value", ["1", "true", "on"])
    def test_monthly_job_pays_when_switched_on(self, monkeypatch, value):
        monkeypatch.setenv("VAULTIS_MONTHLY_AI", value)
        main, calls = self._spy_advice(monkeypatch)
        main.generate_monthly_ai_advisor_and_notify()
        assert calls and calls[0]["user_initiated"] is True

    @pytest.mark.parametrize("value", ["", "0", "no"])
    def test_switch_off_values(self, monkeypatch, value):
        from analysis import ai_advisor

        monkeypatch.setenv("VAULTIS_MONTHLY_AI", value)
        assert ai_advisor.monthly_ai_enabled() is False

    def test_dca_reminder_never_requests_explanation(self, monkeypatch):
        """reminder ใช้แค่ allocation — เปิด AI ทุกสวิตช์แล้วก็ต้องไม่ขอคำอธิบาย."""
        monkeypatch.setenv("VAULTIS_MONTHLY_AI", "1")
        monkeypatch.setenv("VAULTIS_LLM_AUTO", "1")
        main, calls = self._spy_advice(monkeypatch)
        from datetime import datetime

        monkeypatch.setattr(main, "_now_bangkok", lambda: datetime(2026, 9, 30, 8, 0, tzinfo=main.BANGKOK_TZ))
        monkeypatch.setattr(main, "get_today_fx_rate_thb", lambda: 33.0)
        monkeypatch.setattr(main, "send_dca_reminder", lambda **k: {"success": True})
        main.check_and_send_dca_reminder("https://discord.invalid/webhook")
        assert calls and calls[0]["explain"] is False

    def test_explain_false_skips_llm_even_with_auto_flag(self, monkeypatch):
        monkeypatch.setenv("VAULTIS_LLM_AUTO", "1")

        from analysis import ai_advisor

        scores = [{"ticker": "VOO", "data_ok": True, "total_pct": 70.0, "price": 690.0,
                   "ma50": 680.0, "ma200": 650.0, "rsi": 55.0, "signal": "Strong Buy"}]
        monkeypatch.setattr(ai_advisor, "get_tickers", lambda: ["VOO"])
        monkeypatch.setattr("analysis.financial_model.build_etf_scores", lambda t: scores)
        monkeypatch.setattr("analysis.macro.get_macro_snapshot", lambda: {})
        import pandas as pd

        monkeypatch.setattr("portfolio.tracker.get_portfolio_summary", lambda: pd.DataFrame())
        monkeypatch.setattr(ai_advisor, "load_config", lambda: {"notifications": {"discord_webhook_url": ""}})

        def _boom(*a, **k):
            raise AssertionError("explain=False ต้องไม่เรียก LLM")

        monkeypatch.setattr(ai_advisor, "chat_text", _boom)
        result = ai_advisor.get_monthly_advice(budget_thb=5000, send_discord=False, explain=False)
        assert result["ai_used"] is False
        assert result["allocation"]
