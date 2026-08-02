# -*- coding: utf-8 -*-
"""คุมว่า get_news_with_status แยก "ไม่มีข่าว" ออกจาก "ดึงข่าวไม่สำเร็จ" ได้จริง.

หน้า News อ่านผลจากฟังก์ชันนี้ตรง ๆ ถ้ามันกลืนความล้มเหลวเป็นลิสต์ว่าง ผู้ใช้จะเห็น
"ไม่มีข่าว" ทั้งที่แหล่งข่าวล่ม — ความล้มเหลวเงียบแบบเดียวกับ AUDIT.md C1
"""

import pytest
import requests

from analysis import news_fetcher as nf


def _article(url: str, kind: str, published_at: str = "") -> dict:
    return {
        "title": f"หัวข้อ {url}",
        "description": "",
        "url": url,
        "published_at": published_at,
        "source": kind,
        "kind": kind,
    }


@pytest.fixture
def _offline(monkeypatch):
    """ทุกแหล่งปิดเงียบ + ไม่แตะ .env จริง แล้วให้แต่ละเทสต์เปิดเฉพาะแหล่งที่สนใจ."""
    monkeypatch.setattr(nf, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("NEWSAPI_KEY", "")
    monkeypatch.setattr(
        nf, "fetch_yahoo_rss_status", lambda _s: ([], nf._source_status("Yahoo Finance", nf.KIND_NEWS, nf.STATUS_OFF))
    )
    monkeypatch.setattr(
        nf, "fetch_google_news_status",
        lambda _s: ([], nf._source_status("Google News", nf.KIND_NEWS, nf.STATUS_OFF)),
    )
    monkeypatch.setattr(
        nf, "fetch_newsapi_status", lambda _s, _k: ([], nf._source_status("NewsAPI", nf.KIND_NEWS, nf.STATUS_OFF))
    )
    monkeypatch.setattr(
        nf, "fetch_reddit_status", lambda _s: ([], nf._source_status("Reddit", nf.KIND_SOCIAL, nf.STATUS_OFF))
    )
    monkeypatch.setattr(
        nf, "fetch_stocktwits_status", lambda _s: ([], nf._source_status("StockTwits", nf.KIND_SOCIAL, nf.STATUS_OFF))
    )


def _status_of(result: dict, name: str) -> dict:
    return next(s for s in result["sources"] if s["name"] == name)


class TestSourceStatusPerFetcher:
    def test_rss_failure_is_error_not_empty_list(self, monkeypatch):
        def _boom(*_a, **_k):
            raise requests.RequestException("connection reset")

        monkeypatch.setattr(nf.requests, "get", _boom)
        items, status = nf.fetch_rss_status("https://example.com/feed", source_name="Yahoo Finance")

        assert items == []
        assert status["status"] == nf.STATUS_ERROR
        assert "RequestException" in status["detail"]

    def test_missing_newsapi_key_is_off_not_error(self):
        """ไม่ได้ตั้ง key = ปิดอยู่ ไม่ใช่พัง — ต้องไม่ขึ้นคำเตือนสีแดงให้ผู้ใช้ตกใจ."""
        items, status = nf.fetch_newsapi_status("VOO", "")

        assert items == []
        assert status["status"] == nf.STATUS_OFF
        assert "NEWSAPI_KEY" in status["detail"]

    def test_newsapi_200_with_error_payload_is_error(self, monkeypatch):
        """NewsAPI ตอบ 200 พร้อม status=error ได้ (โควตาหมด) — ห้ามอ่านเป็น 'ไม่มีข่าว'."""

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"status": "error", "message": "Your API key has been disabled"}

        monkeypatch.setattr(nf.requests, "get", lambda *a, **k: _Resp())
        items, status = nf.fetch_newsapi_status("VOO", "key-123")

        assert items == []
        assert status["status"] == nf.STATUS_ERROR
        assert "disabled" in status["detail"]

    def test_missing_reddit_credentials_is_off(self, monkeypatch):
        monkeypatch.setattr(nf, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setenv("REDDIT_CLIENT_ID", "")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "")

        items, status = nf.fetch_reddit_status("VOO")

        assert items == []
        assert status["status"] == nf.STATUS_OFF

    def test_stocktwits_unexpected_payload_is_error(self, monkeypatch):
        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"unexpected": True}

        monkeypatch.setattr(nf.requests, "get", lambda *a, **k: _Resp())
        items, status = nf.fetch_stocktwits_status("VOO")

        assert items == []
        assert status["status"] == nf.STATUS_ERROR

    def test_successful_fetch_reports_count(self, monkeypatch):
        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"messages": [{"id": i, "body": f"$VOO {i}"} for i in range(3)]}

        monkeypatch.setattr(nf.requests, "get", lambda *a, **k: _Resp())
        items, status = nf.fetch_stocktwits_status("VOO")

        assert len(items) == 3
        assert status["status"] == nf.STATUS_OK
        assert status["count"] == 3


class TestThinWrappersStillBehave:
    """``fetch_*`` เดิมต้องคืนลิสต์เหมือนเดิม (sentiment job และเทสต์เก่าเรียกตัวนี้)."""

    def test_wrappers_return_only_items(self, monkeypatch):
        monkeypatch.setattr(nf, "fetch_yahoo_rss_status", lambda _s: ([_article("a", "news")], {}))
        monkeypatch.setattr(nf, "fetch_newsapi_status", lambda _s, _k: ([_article("b", "news")], {}))
        monkeypatch.setattr(nf, "fetch_reddit_status", lambda _s: ([_article("c", "social")], {}))
        monkeypatch.setattr(nf, "fetch_stocktwits_status", lambda _s: ([_article("d", "social")], {}))

        assert [a["url"] for a in nf.fetch_yahoo_rss("VOO")] == ["a"]
        assert [a["url"] for a in nf.fetch_newsapi("VOO", "k")] == ["b"]
        assert [a["url"] for a in nf.fetch_reddit("VOO")] == ["c"]
        assert [a["url"] for a in nf.fetch_stocktwits("VOO")] == ["d"]


class TestFailureIsNotEmptiness:
    def test_all_news_sources_failed_when_every_live_news_source_errors(self, monkeypatch, _offline):
        monkeypatch.setattr(
            nf,
            "fetch_yahoo_rss_status",
            lambda _s: ([], nf._source_status("Yahoo Finance", nf.KIND_NEWS, nf.STATUS_ERROR, 0, "timeout")),
        )

        result = nf.get_news_with_status("VOO")

        assert result["items"] == []
        assert result["has_error"] is True
        assert result["all_news_sources_failed"] is True

    def test_sources_that_are_off_do_not_count_as_failure(self, monkeypatch, _offline):
        """ไม่ได้ตั้ง key ทุกแหล่ง = ไม่มีอะไรพัง แค่ไม่มีข่าว — ห้ามรายงานว่าล้มเหลว."""
        result = nf.get_news_with_status("VOO")

        assert result["has_error"] is False
        assert result["all_news_sources_failed"] is False

    def test_working_news_source_clears_the_flag(self, monkeypatch, _offline):
        monkeypatch.setattr(
            nf,
            "fetch_yahoo_rss_status",
            lambda _s: ([_article("n1", "news")], nf._source_status("Yahoo Finance", nf.KIND_NEWS, nf.STATUS_OK, 1)),
        )
        monkeypatch.setattr(
            nf,
            "fetch_newsapi_status",
            lambda _s, _k: ([], nf._source_status("NewsAPI", nf.KIND_NEWS, nf.STATUS_ERROR, 0, "quota")),
        )

        result = nf.get_news_with_status("VOO")

        assert result["all_news_sources_failed"] is False
        assert result["has_error"] is True  # ยังต้องบอกว่า NewsAPI พัง รายการจึงไม่ครบ

    def test_one_broken_source_does_not_kill_the_others(self, monkeypatch, _offline):
        monkeypatch.setattr(
            nf,
            "fetch_yahoo_rss_status",
            lambda _s: ([], nf._source_status("Yahoo Finance", nf.KIND_NEWS, nf.STATUS_ERROR, 0, "boom")),
        )
        monkeypatch.setattr(
            nf,
            "fetch_stocktwits_status",
            lambda _s: ([_article("s1", "social")], nf._source_status("StockTwits", nf.KIND_SOCIAL, nf.STATUS_OK, 1)),
        )

        result = nf.get_news_with_status("VOO")

        assert [a["url"] for a in result["items"]] == ["s1"]
        assert _status_of(result, "StockTwits")["status"] == nf.STATUS_OK
        assert _status_of(result, "Yahoo Finance")["status"] == nf.STATUS_ERROR


class TestRankingMatchesGetNews:
    def test_news_before_social_and_counts_are_reported(self, monkeypatch, _offline):
        monkeypatch.setattr(
            nf,
            "fetch_yahoo_rss_status",
            lambda _s: (
                [_article("n1", "news", "2026-07-20T00:00:00Z")],
                nf._source_status("Yahoo Finance", nf.KIND_NEWS, nf.STATUS_OK, 1),
            ),
        )
        monkeypatch.setattr(
            nf,
            "fetch_stocktwits_status",
            lambda _s: (
                [_article("s1", "social", "2026-07-28T00:00:00Z")],
                nf._source_status("StockTwits", nf.KIND_SOCIAL, nf.STATUS_OK, 1),
            ),
        )

        result = nf.get_news_with_status("voo")

        assert [a["url"] for a in result["items"]] == ["n1", "s1"]
        assert result["news_count"] == 1
        assert result["social_count"] == 1
        assert result["symbol"] == "VOO"

    def test_social_volume_cannot_push_news_out(self, monkeypatch, _offline):
        monkeypatch.setattr(
            nf,
            "fetch_yahoo_rss_status",
            lambda _s: (
                [_article(f"n{i}", "news") for i in range(5)],
                nf._source_status("Yahoo Finance", nf.KIND_NEWS, nf.STATUS_OK, 5),
            ),
        )
        monkeypatch.setattr(
            nf,
            "fetch_stocktwits_status",
            lambda _s: (
                [_article(f"s{i}", "social") for i in range(50)],
                nf._source_status("StockTwits", nf.KIND_SOCIAL, nf.STATUS_OK, 50),
            ),
        )

        result = nf.get_news_with_status("VOO")

        assert len(result["items"]) == nf._MAX_ARTICLES
        assert result["news_count"] == 5
        assert [a["kind"] for a in result["items"][:5]] == ["news"] * 5
