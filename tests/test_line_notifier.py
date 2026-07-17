# -*- coding: utf-8 -*-
"""ทดสอบช่องทาง LINE (Roadmap Phase 5 ข้อ 16) — ต้อง fail-soft เมื่อไม่ได้ตั้งค่า."""

import pytest

from alerts import line_notifier


@pytest.fixture(autouse=True)
def _clear_line_env(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINE_TARGET_ID", raising=False)


def test_skipped_when_not_configured():
    result = line_notifier.send_line_message("ทดสอบ")
    assert result["success"] is False
    assert result.get("skipped") is True
    assert line_notifier.line_configured() is False


def test_sends_when_configured(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token-123")
    monkeypatch.setenv("LINE_TARGET_ID", "U-abc")

    captured: dict = {}

    class _Response:
        def raise_for_status(self):
            pass

    def _fake_post(url, headers=None, json=None, timeout=10):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _Response()

    monkeypatch.setattr(line_notifier.requests, "post", _fake_post)
    result = line_notifier.send_line_message("สวัสดี Vaultis")
    assert result == {"success": True}
    assert captured["url"] == line_notifier.LINE_PUSH_URL
    assert captured["headers"]["Authorization"] == "Bearer token-123"
    assert captured["json"]["to"] == "U-abc"
    assert captured["json"]["messages"][0]["text"] == "สวัสดี Vaultis"


def test_send_failure_reported_not_raised(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token-123")
    monkeypatch.setenv("LINE_TARGET_ID", "U-abc")

    def _boom(url, headers=None, json=None, timeout=10):
        raise ConnectionError("no network")

    monkeypatch.setattr(line_notifier.requests, "post", _boom)
    result = line_notifier.send_line_message("x")
    assert result["success"] is False
    assert "no network" in result["error"]
    assert result.get("skipped") is None
