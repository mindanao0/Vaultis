# -*- coding: utf-8 -*-
"""คุมหน้า News ฝั่ง dashboard: ความล้มเหลวต้องไม่ถูก cache และเวลาต้องไม่ถูกเดา."""

import pytest

app = pytest.importorskip("dashboard.app")


def _result(all_failed: bool, items: list | None = None, sources: list | None = None) -> dict:
    return {
        "symbol": "VOO",
        "items": items or [],
        "sources": sources or [],
        "news_count": 0,
        "social_count": 0,
        "has_error": all_failed,
        "all_news_sources_failed": all_failed,
    }


class TestFailureIsNotCached:
    """cache 30 นาทีต้องไม่แช่ความล้มเหลวไว้เป็น "ไม่มีข่าว" (AUDIT.md C1)."""

    def test_raises_when_every_news_source_failed(self, monkeypatch):
        app.cached_news.clear()
        monkeypatch.setattr(
            app,
            "get_news_with_status",
            lambda _s: _result(
                True,
                sources=[{"name": "Yahoo Finance", "kind": "news", "status": "error", "count": 0, "detail": "timeout"}],
            ),
        )

        with pytest.raises(app.NewsSourcesUnavailable) as exc:
            app.cached_news("VOO")

        assert "Yahoo Finance" in str(exc.value)

    def test_next_call_retries_instead_of_serving_the_failure(self, monkeypatch):
        app.cached_news.clear()
        calls: list[str] = []

        def _flaky(symbol: str) -> dict:
            calls.append(symbol)
            if len(calls) == 1:
                return _result(True, sources=[{"name": "Yahoo Finance", "kind": "news", "status": "error", "count": 0, "detail": "boom"}])
            return _result(False, items=[{"title": "ข่าวจริง", "url": "https://x", "kind": "news", "source": "Yahoo"}])

        monkeypatch.setattr(app, "get_news_with_status", _flaky)

        with pytest.raises(app.NewsSourcesUnavailable):
            app.cached_news("VOO")
        assert app.cached_news("VOO")["items"][0]["title"] == "ข่าวจริง"
        assert calls == ["VOO", "VOO"]

    def test_partial_failure_still_returns_what_it_got(self, monkeypatch):
        """แหล่งหนึ่งพังแต่ยังมีข่าว = คืนของที่ได้ (หน้าจอจะเตือนเรื่องแหล่งที่พังเอง)."""
        app.cached_news.clear()
        monkeypatch.setattr(
            app,
            "get_news_with_status",
            lambda _s: _result(False, items=[{"title": "a", "url": "https://a", "kind": "news"}]) | {"has_error": True},
        )

        assert len(app.cached_news("SCHD")["items"]) == 1


class TestTimeIsNeverInvented:
    def test_utc_is_shown_in_bangkok_time(self):
        assert app._format_news_time("2026-07-28T12:38:00Z") == "28/07/2026 19:38"

    def test_naive_timestamp_is_treated_as_utc(self):
        assert app._format_news_time("2026-07-28T12:38:00") == "28/07/2026 19:38"

    @pytest.mark.parametrize("raw", ["", "   ", "เมื่อวาน", "not-a-date"])
    def test_unparseable_time_shows_dash_not_now(self, raw):
        assert app._format_news_time(raw) == "-"


class TestHeadlineRendering:
    def test_brackets_do_not_break_the_link(self):
        link = app._news_link({"title": "[Update] VOO ปรับพอร์ต", "url": "https://x/1"})

        assert link == "[(Update) VOO ปรับพอร์ต](https://x/1)"

    def test_item_without_url_is_plain_text(self):
        assert app._news_link({"title": "ไม่มีลิงก์", "url": ""}) == "ไม่มีลิงก์"

    def test_missing_title_is_labelled_not_blank(self):
        assert app._news_link({"title": "", "url": "https://x"}) == "[(ไม่มีหัวข้อ)](https://x)"
