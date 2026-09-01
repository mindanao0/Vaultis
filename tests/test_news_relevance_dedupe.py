# -*- coding: utf-8 -*-
"""FIX_PLAN ข้อ 2.6 — ข่าวต้องเกี่ยวกับกองจริง ไม่ซ้ำ และ "ดึงไม่ได้" ต้องไม่กลายเป็น "ไม่มีข่าว".

สามอาการที่ปิดในคอมมิตนี้ ทุกตัวเลขมาจากการยิงแหล่งจริง 2026-08-08:

1. **ไม่มีตัวกรองความเกี่ยวข้องเลย** — NewsAPI ค้นทั้งเนื้อบทความ ``q=XLV`` จึงคืน
   **12 จาก 20 ชิ้นที่ไม่เกี่ยว** (``XLV`` เป็นเลขโรมัน 45 ด้วย) รวมข่าวหอเกียรติยศ
   Packers, ข่าว Dr. Fauci/Aaron Rodgers และข่าวคริปโต · Google News (คำค้น
   ``"<SYM> ETF"``) เหลือของที่เกี่ยวจริง 54–85% แล้วแต่กอง
2. **ลบซ้ำเทียบ url ดิบ** จับข่าวซ้ำข้ามแหล่งไม่ได้เลย — Google News คืนลิงก์ทึบ
   (``news.google.com/rss/articles/CBMi…``) คนละอันกับลิงก์ตรงของ Yahoo บทความเดียว
   จึงกินหลายช่องจาก 30 ช่อง และตอนเข้า sentiment ถูกนับเป็นหลายเสียงจนพลิกป้ายได้
3. **``fetch_rss_status`` คืน ``ok count=0`` เมื่อฟีดตอบ 200 แต่เนื้อเป็น HTML**
   (หน้า consent/error/rate-limit) — ``feedparser`` ไม่ throw กับ HTML มันคืน
   ``entries=[]`` เฉย ๆ ⇒ "ดึงไม่สำเร็จ" กลายเป็น "ไม่มีข่าว" ตรง ๆ ผิดกฎ C1

**เส้นแบ่งที่สำคัญที่สุดของไฟล์นี้: กรองเฉพาะแหล่งที่ค้นด้วยข้อความอิสระ**
ฟีด Yahoo ยิงด้วย ``?s=<SYM>`` และสตรีม StockTwits ผูกกับ symbol อยู่แล้วโดยโครงสร้าง
พาดหัวข่าวของกองมักไม่พิมพ์ตัวย่อซ้ำ วัดจริง: กรองข้อความทับฟีด Yahoo ตัดข่าวจริงทิ้ง
VOO 13→4, SCHD 20→8, QQQM 10→2 — นั่นคือการทำลายข้อมูล ไม่ใช่การเพิ่มความแม่น
"""

from __future__ import annotations

import pytest

from analysis import news_fetcher as nf


def _item(title: str = "", description: str = "", url: str = "http://x/1", **extra):
    row = {
        "title": title,
        "description": description,
        "url": url,
        "published_at": "2026-08-08T00:00:00Z",
        "source": "s",
        "kind": nf.KIND_NEWS,
    }
    row.update(extra)
    return row


# =========================================================================== #
# 1. ความเกี่ยวข้อง
# =========================================================================== #
class TestRelevance:
    @pytest.mark.parametrize(
        "title",
        [
            "Earl Dotson, Tramon Williams join elite in Packers Hall of Fame",
            "Dr. Fauci's attorney says Aaron Rodgers should 'stick with football.'",
            "Corporate crypto accounts on HTX face a complete dead end",
        ],
    )
    def test_ข่าวที่ไม่เกี่ยวจริงต้องถูกปฏิเสธ(self, title):
        """สามพาดหัวนี้คือของจริงที่ ``q=XLV`` คืนมาเมื่อ 2026-08-08."""
        assert not nf.is_relevant("XLV", _item(title))

    def test_ตัวย่อในพาดหัวคือหลักฐานว่าเกี่ยว(self):
        assert nf.is_relevant("XLV", _item("XLV vs IBBQ: which healthcare ETF wins?"))

    def test_ตัวย่อในคำโปรยก็นับ(self):
        assert nf.is_relevant("VOO", _item("Fund flows update", "Money moved into VOO last week"))

    def test_ตัวย่อต้องเป็นคำเต็มไม่ใช่สตริงย่อยกลางคำ(self):
        """``VOO`` ฝังอยู่ใน ``VOOG`` ซึ่งเป็นคนละกอง — เทียบแบบสตริงย่อยจะรับผิดตัว."""
        assert not nf.is_relevant("VOO", _item("VOOG hits a new high"))
        assert not nf.is_relevant("VOO", _item("The AVOOD index rebalanced"))

    def test_ชื่อกองและคำกลุ่มอุตสาหกรรมก็นับว่าเกี่ยว(self):
        assert nf.is_relevant("XLV", _item("Healthcare stocks rally on earnings"))
        assert nf.is_relevant("VOO", _item("Vanguard cuts fees across its lineup"))
        assert nf.is_relevant("QQQM", _item("Nasdaq 100 rebalance shakes up weights"))

    def test_ตัวพิมพ์เล็กใหญ่ไม่มีผล(self):
        assert nf.is_relevant("VOO", _item("voo posts record inflows"))
        assert nf.is_relevant("voo", _item("VOO posts record inflows"))

    def test_สัญลักษณ์ที่ไม่มีในตารางคำพ้องใช้ตัวย่ออย่างเดียว(self):
        assert nf.is_relevant("VTI", _item("VTI vs VOO: which total-market fund?"))
        assert not nf.is_relevant("VTI", _item("Vanguard cuts fees across its lineup")), (
            "คำพ้องของกองอื่นต้องไม่ทำให้กองที่ไม่มีในตารางผ่านมั่ว ๆ"
        )

    def test_ไม่มีสัญลักษณ์ก็ตัดสินว่าเกี่ยวไม่ได้(self):
        assert not nf.is_relevant("", _item("VOO news"))
        assert not nf.is_relevant("   ", _item("VOO news"))

    def test_ตัวกรองรายงานจำนวนที่ตัดออก(self):
        kept, dropped = nf._filter_relevant(
            "XLV", [_item("XLV rises"), _item("Packers Hall of Fame"), _item("Healthcare rally")]
        )
        assert [a["title"] for a in kept] == ["XLV rises", "Healthcare rally"]
        assert dropped == 1


class TestOnlyFreeTextSourcesAreFiltered:
    """ฟีดที่ผูกกับสัญลักษณ์อยู่แล้วห้ามถูกกรอง — ไม่งั้นข่าวจริงหายไปกว่าครึ่ง."""

    OFF_TOPIC_LOOKING = _item(
        "Are Wall Street Analysts Bullish on Cardinal Health Stock?", url="http://y/1"
    )

    def test_ฟีด_yahoo_ไม่ถูกกรอง(self, monkeypatch):
        """พาดหัวไม่มีทั้ง 'XLV' และคำพ้อง แต่ฟีดยิงด้วย ?s=XLV จึงเกี่ยวตั้งแต่ URL."""
        monkeypatch.setattr(
            nf,
            "fetch_rss_status",
            lambda url, kind=nf.KIND_NEWS, source_name="RSS": (
                [self.OFF_TOPIC_LOOKING],
                nf._source_status(source_name, kind, nf.STATUS_OK, 1),
            ),
        )
        items, status = nf.fetch_yahoo_rss_status("XLV")
        assert len(items) == 1, "กรองฟีด Yahoo = ตัดข่าวจริงทิ้ง (วัดจริง VOO 13→4)"
        assert status["filtered"] == 0

    def test_google_news_ถูกกรองเพราะเป็นคำค้นอิสระ(self, monkeypatch):
        monkeypatch.setattr(
            nf,
            "fetch_rss_status",
            lambda url, kind=nf.KIND_NEWS, source_name="RSS": (
                [_item("XLV vs IBBQ", url="http://g/1"), _item("Packers Hall of Fame", url="http://g/2")],
                nf._source_status(source_name, kind, nf.STATUS_OK, 2),
            ),
        )
        items, status = nf.fetch_google_news_status("XLV")
        assert [a["title"] for a in items] == ["XLV vs IBBQ"]
        assert status["fetched"] == 2 and status["count"] == 1 and status["filtered"] == 1
        assert "ไม่เกี่ยว" in status["detail"]

    def test_google_news_ที่ล้มเหลวต้องไม่ถูกแปลงเป็น_ok(self, monkeypatch):
        monkeypatch.setattr(
            nf,
            "fetch_rss_status",
            lambda url, kind=nf.KIND_NEWS, source_name="RSS": (
                [],
                nf._source_status(source_name, kind, nf.STATUS_ERROR, 0, "boom"),
            ),
        )
        _items, status = nf.fetch_google_news_status("XLV")
        assert status["status"] == nf.STATUS_ERROR and status["detail"] == "boom"

    def test_stocktwits_ไม่ถูกกรอง(self, monkeypatch):
        """สตรีมผูกกับ symbol อยู่แล้ว และโพสต์สั้น ๆ มักไม่พิมพ์ตัวย่อซ้ำ."""

        class _Resp:
            status_code = 200
            headers = {"Content-Type": "application/json"}

            def raise_for_status(self):
                return None

            def json(self):
                return {"messages": [{"id": 1, "body": "buying the dip today"}]}

        monkeypatch.setattr(nf.requests, "get", lambda *a, **k: _Resp())
        items, status = nf.fetch_stocktwits_status("XLV")
        assert len(items) == 1 and status["filtered"] == 0


class TestNewsapiFiltersAndReports:
    @staticmethod
    def _resp(articles):
        class _Resp:
            status_code = 200
            headers = {"Content-Type": "application/json"}

            def raise_for_status(self):
                return None

            def json(self):
                return {"status": "ok", "articles": articles}

        return _Resp()

    def test_ตัดของไม่เกี่ยวแล้วบอกจำนวน(self, monkeypatch):
        monkeypatch.setattr(
            nf.requests,
            "get",
            lambda *a, **k: self._resp(
                [
                    {"title": "XLV leads healthcare", "url": "http://a/1"},
                    {"title": "Packers Hall of Fame", "url": "http://a/2"},
                    {"title": "Crypto accounts face dead end", "url": "http://a/3"},
                ]
            ),
        )
        items, status = nf.fetch_newsapi_status("XLV", "k")
        assert [a["title"] for a in items] == ["XLV leads healthcare"]
        assert status["fetched"] == 3 and status["count"] == 1 and status["filtered"] == 2
        assert "2" in status["detail"]

    def test_ไม่มีอะไรถูกตัดก็ไม่ต้องมีข้อความ(self, monkeypatch):
        monkeypatch.setattr(
            nf.requests, "get", lambda *a, **k: self._resp([{"title": "XLV rises", "url": "http://a/1"}])
        )
        _items, status = nf.fetch_newsapi_status("XLV", "k")
        assert status["filtered"] == 0 and status["detail"] == ""

    def test_ตัดหมดยังเป็น_ok_ไม่ใช่_error(self, monkeypatch):
        """ตัดทิ้งหมด ≠ ดึงไม่สำเร็จ — แหล่งตอบปกติ แค่ไม่มีอะไรเกี่ยวกับกองนี้."""
        monkeypatch.setattr(
            nf.requests, "get", lambda *a, **k: self._resp([{"title": "Packers", "url": "http://a/1"}])
        )
        items, status = nf.fetch_newsapi_status("XLV", "k")
        assert items == [] and status["status"] == nf.STATUS_OK
        assert status["fetched"] == 1 and status["filtered"] == 1


# =========================================================================== #
# 2. ลบซ้ำ
# =========================================================================== #
class TestUrlNormalization:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("https://www.fool.com/x/1", "https://fool.com/x/1"),
            ("https://fool.com/x/1", "https://fool.com/x/1/"),
            ("https://fool.com/x/1", "https://fool.com/x/1#section"),
            ("https://fool.com/x/1", "https://fool.com/x/1?utm_source=rss&utm_medium=feed"),
            ("https://barchart.com/story/1", "https://barchart.com/story/1?.tsrc=rs"),
            ("https://fool.com/x/1?a=1&b=2", "https://fool.com/x/1?b=2&a=1"),
        ],
    )
    def test_รูปที่ต่างกันแต่เป็นบทความเดียวกัน(self, a, b):
        assert nf._normalize_url(a) == nf._normalize_url(b)

    def test_บทความคนละอันต้องไม่ถูกยุบรวม(self):
        assert nf._normalize_url("https://fool.com/x/1") != nf._normalize_url("https://fool.com/x/2")

    def test_พารามิเตอร์ที่มีความหมายต้องไม่ถูกตัด(self):
        assert nf._normalize_url("https://site.com/a?id=7") != nf._normalize_url("https://site.com/a?id=8")

    def test_คลี่_google_news_redirect_รูปเก่า(self):
        wrapped = "https://news.google.com/rss/articles/x?url=https%3A%2F%2Ffool.com%2Fx%2F1&hl=en"
        assert nf._normalize_url(wrapped) == nf._normalize_url("https://fool.com/x/1")

    def test_ลิงก์ทึบของ_google_news_ยังใช้ได้ไม่ระเบิด(self):
        """รูปสมัยใหม่ไม่มี ?url= ให้คลี่ — ต้องคืนคีย์ที่ใช้เทียบได้ ไม่ใช่ค่าว่าง."""
        opaque = "https://news.google.com/rss/articles/CBMiUEFVX3lxTFA0bHRJS1V4dzA1"
        assert nf._normalize_url(opaque)
        assert nf._normalize_url(opaque) != nf._normalize_url(
            "https://news.google.com/rss/articles/CBMixgFBVV95cUxQVkNQc3l"
        )

    def test_ค่าว่างคืนค่าว่าง(self):
        assert nf._normalize_url("") == "" and nf._normalize_url(None) == ""


class TestTitleKey:
    def test_ชื่อสำนักข่าวท้ายพาดหัวถูกตัด(self):
        """Google News ต่อ ' - <สำนักข่าว>' ท้ายพาดหัว — Yahoo ไม่ต่อ."""
        assert nf._title_key("XLV vs IBBQ: is broad healthcare better? - The Motley Fool") == nf._title_key(
            "XLV vs IBBQ: is broad healthcare better?"
        )

    @pytest.mark.parametrize("sep", [" - ", " – ", " — ", " | "])
    def test_ตัวคั่นทุกแบบที่ผู้รวมข่าวใช้(self, sep):
        assert nf._title_key(f"Fed holds rates{sep}Reuters") == nf._title_key("Fed holds rates")

    def test_ท้ายที่ยาวเกินไปคือเนื้อพาดหัวไม่ใช่ชื่อสำนักข่าว(self):
        """ตัดมั่วเมื่อไร พาดหัวคนละบทความจะยุบรวมกัน — ต้องเก็บส่วนที่ยาวไว้."""
        long_tail = "Gold rally - why the metal keeps climbing while equities stall out"
        assert nf._title_key(long_tail) != nf._title_key("Gold rally")

    def test_เครื่องหมายวรรคตอนและช่องว่างไม่มีผล(self):
        assert nf._title_key("Fed  holds   rates.") == nf._title_key("Fed holds rates")
        assert nf._title_key("S&P 500 hits record!") == nf._title_key("S P 500 hits record")
        assert nf._title_key("Fed holds rates") != nf._title_key("Fed cuts rates")

    def test_ไม่ตัดความยาวเพราะพาดหัวขึ้นต้นเหมือนกันมีเยอะ(self):
        """เคสจริงของชุดนี้: ต่างกันที่คำท้ายสุดเท่านั้น."""
        a = "Which is the better State Street healthcare ETF: the broad XLV or the focused XBI?"
        b = "Which is the better State Street healthcare ETF: the broad XLV or the focused IHI?"
        assert nf._title_key(a) != nf._title_key(b)

    def test_พาดหัวว่างคืนค่าว่าง(self):
        assert nf._title_key("") == "" and nf._title_key(None) == ""


class TestMergeDedupe:
    def test_บทความเดียวกันจากสองแหล่งกินช่องเดียว(self):
        merged = [
            _item("XLV vs IBBQ: which wins?", url="https://fool.com/a/1", source="Yahoo"),
            _item(
                "XLV vs IBBQ: which wins? - The Motley Fool",
                url="https://news.google.com/rss/articles/CBMiOPAQUE",
                source="Google News",
            ),
        ]
        out = nf._merge_and_rank(merged)
        assert len(out) == 1
        assert out[0]["source"] == "Yahoo", (
            "ตัวแรกที่เจอชนะ — ลำดับรวมคือ Yahoo → Google ลิงก์ตรงจึงชนะลิงก์ทึบเอง"
        )

    def test_url_ต่างกันแค่พารามิเตอร์ติดตามผลก็ซ้ำ(self):
        merged = [
            _item("A", url="https://barchart.com/s/1"),
            _item("B", url="https://barchart.com/s/1?.tsrc=rs"),
        ]
        assert len(nf._merge_and_rank(merged)) == 1

    def test_บทความคนละอันต้องอยู่ครบ(self):
        merged = [_item("A", url="http://x/1"), _item("B", url="http://x/2")]
        assert len(nf._merge_and_rank(merged)) == 2

    def test_รายการไม่มีพาดหัวไม่ถูกยุบรวมกันทั้งหมด(self):
        """พาดหัวว่างสองอันไม่ใช่ "บทความเดียวกัน" — ต้องตัดสินด้วย url."""
        merged = [_item("", url="http://x/1"), _item("", url="http://x/2")]
        assert len(nf._merge_and_rank(merged)) == 2

    def test_รายการไม่มี_url_ยังถูกตัดทิ้งเหมือนเดิม(self):
        assert nf._merge_and_rank([_item("A", url="")]) == []

    def test_ข่าวซ้ำไม่กินโควตา_30_ช่อง(self):
        """ก่อนแก้ บทความเดียวกินได้หลายช่อง — เสียช่องให้ข่าวที่ยังไม่เคยเห็น."""
        dupes = [
            _item("Same headline", url=f"http://dup/{i}", published_at="2026-08-08T00:00:00Z")
            for i in range(10)
        ]
        fresh = [
            _item(f"Fresh {i}", url=f"http://new/{i}", published_at="2026-08-07T00:00:00Z")
            for i in range(29)
        ]
        out = nf._merge_and_rank(dupes + fresh)
        assert len(out) == 30
        assert sum(1 for a in out if a["title"] == "Same headline") == 1
        assert sum(1 for a in out if a["title"].startswith("Fresh")) == 29

    def test_เสียงซ้ำไม่ไหลเข้า_sentiment(self):
        """sentiment นับต่อรายการ — บทความเดียวที่กิน 4 ช่องคือ 4 เสียง พอที่จะพลิกป้าย."""
        merged = [_item("Great news for VOO", url=f"http://s/{i}") for i in range(4)]
        assert len(nf._merge_and_rank(merged)) == 1


# =========================================================================== #
# 3. HTTP 200 ที่ไม่ใช่ฟีด
# =========================================================================== #
class _FeedResponse:
    def __init__(self, body: bytes, content_type: str = "text/html", status: int = 200):
        self.content = body
        self.headers = {"Content-Type": content_type}
        self.status_code = status

    def raise_for_status(self):
        return None


_REAL_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>A</title><link>http://x/1</link></item></channel></rss>"""
_EMPTY_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title></channel></rss>"""
_CONSENT_PAGE = b"<!DOCTYPE html><html><body><h1>Before you continue</h1></body></html>"


class TestNonFeedBodyIsAnError:
    def test_หน้า_html_ที่ตอบ_200_ต้องเป็น_error(self, monkeypatch):
        monkeypatch.setattr(nf.requests, "get", lambda *a, **k: _FeedResponse(_CONSENT_PAGE))
        items, status = nf.fetch_rss_status("http://feed", source_name="Yahoo Finance")
        assert items == []
        assert status["status"] == nf.STATUS_ERROR, (
            "ตอบ 200 พร้อม HTML แล้วรายงาน ok count=0 คือ 'ดึงไม่สำเร็จ' ที่ปลอมเป็น 'ไม่มีข่าว'"
        )
        assert "text/html" in status["detail"]
        assert "ไม่ใช่ 'ไม่มีข่าว'" in status["detail"]

    def test_ฟีดที่ว่างจริงยังเป็น_ok(self, monkeypatch):
        """ฟีดถูกต้องแต่ไม่มี item = "วันนี้ไม่มีข่าว" จริง ๆ ห้ามรายงานเป็นความล้มเหลว."""
        monkeypatch.setattr(
            nf.requests, "get", lambda *a, **k: _FeedResponse(_EMPTY_FEED, "application/rss+xml")
        )
        items, status = nf.fetch_rss_status("http://feed", source_name="Yahoo Finance")
        assert items == [] and status["status"] == nf.STATUS_OK and status["count"] == 0

    def test_ฟีดที่มีของยังทำงานปกติ(self, monkeypatch):
        monkeypatch.setattr(
            nf.requests, "get", lambda *a, **k: _FeedResponse(_REAL_FEED, "application/rss+xml")
        )
        items, status = nf.fetch_rss_status("http://feed", source_name="Yahoo Finance")
        assert len(items) == 1 and status["status"] == nf.STATUS_OK

    def test_ฟีดที่มีของแต่ประกาศ_content_type_ผิดยังผ่าน(self, monkeypatch):
        """ตัวตัดสินคือ 'อ่านออกว่าเป็นฟีดไหม' ไม่ใช่ Content-Type ที่เซิร์ฟเวอร์ประกาศ."""
        monkeypatch.setattr(nf.requests, "get", lambda *a, **k: _FeedResponse(_REAL_FEED, "text/html"))
        items, status = nf.fetch_rss_status("http://feed", source_name="Yahoo Finance")
        assert len(items) == 1 and status["status"] == nf.STATUS_OK

    def test_ความล้มเหลวนี้ต้องไปถึง_all_news_sources_failed(self, monkeypatch):
        """ปลายทางที่หน้าจอใช้ตัดสินว่าจะพิมพ์ 'ไม่มีข่าว' ได้ไหม."""
        monkeypatch.setattr(nf.requests, "get", lambda *a, **k: _FeedResponse(_CONSENT_PAGE))
        monkeypatch.setenv("NEWSAPI_KEY", "")
        monkeypatch.setattr(
            nf, "fetch_reddit_status", lambda _s: ([], nf._source_status("Reddit", nf.KIND_SOCIAL, nf.STATUS_OFF))
        )
        monkeypatch.setattr(
            nf,
            "fetch_stocktwits_status",
            lambda _s: ([], nf._source_status("StockTwits", nf.KIND_SOCIAL, nf.STATUS_OFF)),
        )
        result = nf.get_news_with_status("VOO")
        assert result["has_error"] is True
        assert result["all_news_sources_failed"] is True, (
            "หน้าจอจะพิมพ์ 'ไม่มีข่าว' ถ้าธงนี้ไม่ขึ้น ทั้งที่แหล่งข่าวจริงพังหมด"
        )
