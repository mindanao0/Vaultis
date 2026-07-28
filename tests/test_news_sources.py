# -*- coding: utf-8 -*-
"""คุมว่า get_news ดึงเฉพาะแหล่งที่ผูกกับสัญลักษณ์ และแยกข่าวจริงออกจากโซเชียล.

บั๊กเดิม: ยัดฟีดข่าวเศรษฐกิจไทย (Settrade/Thairath) เข้าไปทุกสัญลักษณ์
→ ข่าวชุดเดียวกันกลายเป็น "ข่าวของ" ทั้ง 5 ETF พร้อมกัน
"""

import pytest

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
def _no_network(monkeypatch):
    """ตัดทุกแหล่งเป็นค่าว่าง แล้วให้แต่ละเทสต์เปิดเฉพาะแหล่งที่สนใจ."""
    monkeypatch.setattr(nf, "fetch_yahoo_rss", lambda _s: [])
    monkeypatch.setattr(nf, "fetch_newsapi", lambda _s, _k: [])
    monkeypatch.setattr(nf, "fetch_reddit", lambda _s: [])
    monkeypatch.setattr(nf, "fetch_stocktwits", lambda _s: [])


class TestSourcesAreSymbolScoped:
    def test_every_source_receives_the_requested_symbol(self, monkeypatch, _no_network):
        seen: list[str] = []
        monkeypatch.setattr(nf, "fetch_yahoo_rss", lambda s: seen.append(f"yahoo:{s}") or [])
        monkeypatch.setattr(nf, "fetch_newsapi", lambda s, _k: seen.append(f"newsapi:{s}") or [])
        monkeypatch.setattr(nf, "fetch_reddit", lambda s: seen.append(f"reddit:{s}") or [])
        monkeypatch.setattr(nf, "fetch_stocktwits", lambda s: seen.append(f"stocktwits:{s}") or [])

        nf.get_news("QQQM")

        assert seen == ["yahoo:QQQM", "newsapi:QQQM", "reddit:QQQM", "stocktwits:QQQM"]

    def test_no_general_thai_rss_feeds_remain(self):
        """ฟีดข่าวทั่วไปต้องไม่กลับเข้ามาใน path ราย ticker อีก."""
        assert not hasattr(nf, "SETTRADE_RSS")
        assert not hasattr(nf, "THAIRATH_RSS")

    def test_yahoo_url_is_built_per_symbol(self):
        assert "s=QQQM" in nf.YAHOO_RSS_TEMPLATE.format(symbol="QQQM")

    def test_empty_symbol_yields_no_yahoo_call(self):
        assert nf.fetch_yahoo_rss("") == []
        assert nf.fetch_yahoo_rss("   ") == []


class TestNewsRankedAboveSocial:
    def test_news_comes_first_even_when_social_is_newer(self, monkeypatch, _no_network):
        monkeypatch.setattr(
            nf, "fetch_yahoo_rss", lambda _s: [_article("n1", "news", "2026-07-20T00:00:00Z")]
        )
        monkeypatch.setattr(
            nf, "fetch_stocktwits", lambda _s: [_article("s1", "social", "2026-07-28T00:00:00Z")]
        )

        out = nf.get_news("VOO")

        assert [a["url"] for a in out] == ["n1", "s1"]

    def test_within_a_kind_newest_first(self, monkeypatch, _no_network):
        monkeypatch.setattr(
            nf,
            "fetch_yahoo_rss",
            lambda _s: [
                _article("old", "news", "2026-07-01T00:00:00Z"),
                _article("new", "news", "2026-07-27T00:00:00Z"),
            ],
        )

        assert [a["url"] for a in nf.get_news("VOO")] == ["new", "old"]

    def test_missing_date_sorts_last_but_is_kept(self, monkeypatch, _no_network):
        monkeypatch.setattr(
            nf,
            "fetch_yahoo_rss",
            lambda _s: [_article("nodate", "news", ""), _article("dated", "news", "2026-07-27T00:00:00Z")],
        )

        assert [a["url"] for a in nf.get_news("VOO")] == ["dated", "nodate"]


class TestDedupAndCap:
    def test_duplicate_urls_removed(self, monkeypatch, _no_network):
        monkeypatch.setattr(nf, "fetch_yahoo_rss", lambda _s: [_article("same", "news")])
        monkeypatch.setattr(nf, "fetch_newsapi", lambda _s, _k: [_article("same", "news")])

        assert len(nf.get_news("VOO")) == 1

    def test_items_without_url_dropped(self, monkeypatch, _no_network):
        monkeypatch.setattr(nf, "fetch_yahoo_rss", lambda _s: [_article("", "news")])

        assert nf.get_news("VOO") == []

    def test_capped_at_30(self, monkeypatch, _no_network):
        monkeypatch.setattr(
            nf, "fetch_stocktwits", lambda _s: [_article(f"s{i}", "social") for i in range(50)]
        )

        assert len(nf.get_news("VOO")) == nf._MAX_ARTICLES == 30

    def test_real_news_not_pushed_out_by_social_volume(self, monkeypatch, _no_network):
        """โพสต์โซเชียลจำนวนมากต้องไม่เบียดข่าวจริงตกขอบ."""
        monkeypatch.setattr(
            nf, "fetch_yahoo_rss", lambda _s: [_article(f"n{i}", "news") for i in range(5)]
        )
        monkeypatch.setattr(
            nf, "fetch_stocktwits", lambda _s: [_article(f"s{i}", "social") for i in range(50)]
        )

        out = nf.get_news("VOO")
        assert sum(1 for a in out if a["kind"] == "news") == 5
        assert [a["kind"] for a in out[:5]] == ["news"] * 5


class TestKindLabels:
    def test_stocktwits_and_reddit_are_social_not_news(self, monkeypatch):
        """โพสต์รายย่อยต้องไม่ถูกนับเป็นข่าว — ผู้อ่านผลต้องแยกออก."""
        monkeypatch.setattr(
            nf.requests,
            "get",
            lambda *a, **k: type(
                "R",
                (),
                {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {"messages": [{"id": 1, "body": "$VOO to the moon"}]},
                },
            )(),
        )
        rows = nf.fetch_stocktwits("VOO")
        assert rows and all(r["kind"] == nf.KIND_SOCIAL for r in rows)
