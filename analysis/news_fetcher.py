"""ดึงข่าว **ที่เกี่ยวกับสัญลักษณ์นั้นจริง ๆ** จาก Yahoo Finance, Google News, NewsAPI, Reddit และ StockTwits.

เดิม ``get_news`` ยัดฟีด RSS ข่าวเศรษฐกิจไทย (Settrade/Thairath) เข้าไปทุกสัญลักษณ์
โดยไม่กรอง — ข่าวหุ้นไทยชุดเดียวกันจึงกลายเป็น "ข่าวของ" VOO/SCHD/QQQM/XLV/GLDM
พร้อมกันทั้งห้าตัว (และตอนตรวจ 2026-07-28 ทั้งสองฟีดคืน 0 รายการอยู่แล้ว)
ตอนนี้ทุกแหล่งใน ``get_news`` ผูกกับสัญลักษณ์ที่ขอเสมอ

แต่ละรายการมี ``kind``:
- ``news``   — ข่าวจากสำนักข่าว (Yahoo Finance RSS, Google News RSS, NewsAPI)
- ``social`` — ความเห็นนักลงทุนรายย่อย (Reddit, StockTwits) **ไม่ใช่ข่าว**
  แยกไว้เพื่อไม่ให้ผู้อ่านผลเข้าใจว่าโพสต์เชียร์หุ้นคือรายงานข่าว

สองทางเข้า:
- ``get_news(symbol)``             — คืนรายการอย่างเดียว (ใช้โดย sentiment job เดิม)
- ``get_news_with_status(symbol)`` — คืนรายการ **พร้อมสถานะรายแหล่ง** สำหรับหน้าจอที่ต้อง
  แยกให้ออกว่า "ไม่มีข่าว" กับ "ดึงข่าวไม่สำเร็จ" ไม่ใช่เรื่องเดียวกัน (AUDIT.md C1 —
  ความล้มเหลวห้ามกลายเป็นผลลัพธ์เงียบ ๆ)

ทุก ``fetch_*`` มีคู่แฝด ``fetch_*_status`` ที่คืน ``(รายการ, สถานะ)`` — ตัวที่ไม่มี
``_status`` เป็นเปลือกบางเรียกตัวเดียวกัน ตรรกะการดึงจึงมีชุดเดียว ไม่มีทางแยกกันเอง
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote_plus, unquote, urlencode, urlsplit, urlunsplit

import feedparser
import praw
import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]

# โหลด .env **ครั้งเดียวตอน import** — env ของโปรเซสคือแหล่งความจริงตอนรัน
# ไฟล์ .env เป็นแค่ค่าเริ่มต้นตอนบูตเท่านั้น (เหมือน analysis/llm.py)
# เดิมเรียกซ้ำในทุก fetch_reddit_status()/get_news()/get_news_with_status()
# ทำให้ unset NEWSAPI_KEY / REDDIT_CLIENT_ID ในโปรเซสไม่มีผลเลย เพราะไฟล์เติมกลับมา
# ทุกครั้ง = ไฟล์ชนะ env เสมอ  ผลข้างเคียงที่ร้ายกับไฟล์นี้เป็นพิเศษคือสถานะ ``off``
# (ไม่ได้ตั้งคีย์ ซึ่งไม่ใช่ความล้มเหลว) เกิดขึ้นไม่ได้เลยบนเครื่องที่มี .env
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)

KIND_NEWS = "news"
KIND_SOCIAL = "social"

# ฟีดข่าวราย ticker ของ Yahoo — ไม่ต้องใช้ API key จึงเป็นแหล่ง "ข่าวจริง" ตัวเดียว
# ที่ทำงานได้ทันทีโดยไม่ต้องตั้งค่าอะไรเพิ่ม
YAHOO_RSS_TEMPLATE = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
# Google News: ฟรี ไม่ต้องใช้ key — ค้นด้วย "<SYM> ETF" (ดู fetch_google_news_status)
GOOGLE_NEWS_RSS_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
)

_NEWSAPI_URL = "https://newsapi.org/v2/everything"
_STOCKTWITS_STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"

# สถานะรายแหล่ง — ``off`` (ไม่ได้ตั้ง key) ต่างจาก ``error`` (ตั้งแล้วแต่ดึงไม่ได้) โดยสิ้นเชิง
# แหล่งที่ปิดอยู่ไม่ใช่ความล้มเหลว แต่แหล่งที่ error ต้องบอกผู้ใช้ ไม่ใช่ทำเป็นว่าไม่มีข่าว
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_OFF = "off"


def _source_status(
    name: str,
    kind: str,
    status: str,
    count: int = 0,
    detail: str = "",
    fetched: int | None = None,
    filtered: int = 0,
) -> dict[str, Any]:
    """สถานะรายแหล่งหนึ่งชุด.

    ``count`` = จำนวนที่**ส่งต่อออกไป** · ``fetched`` = จำนวนที่ดึงมาได้ก่อนกรอง ·
    ``filtered`` = จำนวนที่ถูกตัดเพราะไม่เกี่ยวกับสัญลักษณ์  สามตัวนี้ต้องมาด้วยกันเสมอ
    เพราะ "ตัดข้อมูลทิ้งแล้วไม่บอก" ผิดกฎเดียวกับ "ดึงไม่สำเร็จแล้วโชว์ว่าไม่มีข่าว" —
    ผู้ใช้ต้องแยกออกว่า 3 ชิ้นที่เห็นมาจาก "มีแค่ 3" หรือ "มี 20 แล้วเราตัดทิ้ง 17"
    """
    return {
        "name": name,
        "kind": kind,
        "status": status,
        "count": count,
        "fetched": count if fetched is None else fetched,
        "filtered": filtered,
        "detail": detail,
    }


# --------------------------------------------------------------------------- #
# ตัวกรองความเกี่ยวข้อง — ใช้กับ **แหล่งที่ค้นด้วยข้อความอิสระ** เท่านั้น
# --------------------------------------------------------------------------- #
#: คำพ้องของแต่ละกอง (ชื่อกอง + คำกลุ่มอุตสาหกรรม) — ทั้งหมดเทียบแบบ **ตัวพิมพ์เล็ก
#: และเป็นสตริงย่อย** กับ ``title + description``  สัญลักษณ์ที่ไม่มีในตารางนี้จะถูก
#: ตัดสินด้วยตัวย่อของมันเองอย่างเดียว (เข้มกว่า แต่ปลอดภัยกว่าการเดาคำพ้องให้)
SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    "VOO": ("vanguard", "s&p 500", "s and p 500", "sp 500"),
    "SCHD": ("schwab", "dividend equity"),
    "QQQM": ("invesco", "nasdaq-100", "nasdaq 100"),
    "XLV": ("health care select", "healthcare", "health care", "spdr"),
    "GLDM": ("spdr gold", "gold minishares", "gold"),
}


def is_relevant(symbol: str, item: dict[str, Any]) -> bool:
    """รายการนี้เกี่ยวกับ ``symbol`` จริงไหม — ดูจาก ``title`` + ``description`` เท่านั้น.

    เกณฑ์: พบตัวย่อแบบ**คำเต็ม** (ไม่ใช่สตริงย่อยกลางคำ) หรือพบคำพ้องใน
    :data:`SYMBOL_ALIASES` · ไม่พบเลย = ไม่เกี่ยว

    ทำไมต้องดูแค่หัวข้อกับคำโปรย: NewsAPI ค้นทั้งเนื้อบทความ บทความที่เอ่ย ``XLV``
    ผ่าน ๆ ในย่อหน้าที่ 8 จึงถูกส่งมาเป็น "ข่าวของ XLV" — และที่แย่กว่าคือ ``XLV`` เป็น
    เลขโรมัน 45 ด้วย วัดจริง 2026-08-08 คำค้น ``q=XLV`` คืน **12 จาก 20 ชิ้นที่ไม่เกี่ยว**
    รวมข่าวหอเกียรติยศทีม Packers และข่าวคริปโต

    ตัวย่อต้องเทียบแบบคำเต็มเพราะตัวย่อสั้น ๆ ฝังอยู่ในคำอื่นได้ (``VOO`` ใน ``VOOG``)
    """
    sym = (symbol or "").strip().lower()
    if not sym:
        return False
    blob = f"{item.get('title') or ''} {item.get('description') or ''}".lower()
    if re.search(rf"(?<![a-z0-9]){re.escape(sym)}(?![a-z0-9])", blob):
        return True
    return any(alias in blob for alias in SYMBOL_ALIASES.get(sym.upper(), ()))


def _filter_relevant(
    symbol: str, items: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """คัดเฉพาะรายการที่เกี่ยวกับสัญลักษณ์ คืน ``(ที่เหลือ, จำนวนที่ตัดออก)``.

    **ห้ามใช้กับแหล่งที่ผูกกับสัญลักษณ์อยู่แล้วโดยโครงสร้าง** (ฟีด Yahoo ที่ยิงด้วย
    ``?s=<SYM>`` และสตรีม StockTwits ราย symbol) — วัดจริง 2026-08-08 การกรองข้อความ
    ทับฟีด Yahoo ตัดข่าวจริงทิ้งมหาศาล (VOO 13→4, SCHD 20→8, QQQM 10→2) เพราะพาดหัว
    ข่าวของกองมักไม่พิมพ์ตัวย่อซ้ำ ฟีดนั้น "เกี่ยว" ตั้งแต่ URL แล้ว การกรองจึงเป็นการ
    ทำลายข้อมูล ไม่ใช่การเพิ่มความแม่น
    """
    kept = [item for item in items if is_relevant(symbol, item)]
    return kept, len(items) - len(kept)


def _iso_from_struct_time(st: Any) -> str:
    if st is None:
        return ""
    try:
        dt = datetime(*st[:6], tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return ""


def fetch_newsapi_status(symbol: str, api_key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """ดึงข่าวจาก NewsAPI everything; คืน ``(รายการ, สถานะ)`` ไม่ throw."""
    name = "NewsAPI"
    key = (api_key or "").strip()
    if not key:
        return [], _source_status(name, KIND_NEWS, STATUS_OFF, 0, "ไม่ได้ตั้ง NEWSAPI_KEY")
    if not (symbol or "").strip():
        return [], _source_status(name, KIND_NEWS, STATUS_OFF, 0, "ไม่ได้ระบุสัญลักษณ์")
    try:
        resp = requests.get(
            _NEWSAPI_URL,
            params={
                "q": symbol.strip(),
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 20,
                "apiKey": key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            # NewsAPI ตอบ 200 พร้อม status=error ได้ (key หมดโควตา/ถูกระงับ) — ต้องไม่อ่านเป็น "ไม่มีข่าว"
            detail = str(data.get("message") or data.get("code") or "status != ok")
            return [], _source_status(name, KIND_NEWS, STATUS_ERROR, 0, detail)
        out: list[dict[str, Any]] = []
        for a in data.get("articles") or []:
            src = a.get("source") or {}
            # ห้ามใช้ชื่อ `name` ซ้ำกับชื่อแหล่ง — เคยทับกันจนสถานะรายงานเป็นสำนักข่าว
            # ของบทความสุดท้าย ("Business Insider 20") แทน "NewsAPI 20" (C1: สถานะต้องตรงความจริง)
            publisher = src.get("name") if isinstance(src, dict) else None
            out.append(
                {
                    "title": a.get("title") or "",
                    "description": a.get("description") or "",
                    "url": a.get("url") or "",
                    "published_at": a.get("publishedAt") or "",
                    "source": publisher or "NewsAPI",
                    "kind": KIND_NEWS,
                }
            )
        # NewsAPI ค้นทั้งเนื้อบทความ ผลดิบจึงมีของไม่เกี่ยวปนมาเยอะที่สุดในบรรดาแหล่งทั้งหมด
        # (วัด q=XLV 2026-08-08: 12/20 ไม่เกี่ยว) — กรองแล้ว **รายงานจำนวนที่ตัด** ออกไปด้วย
        kept, dropped = _filter_relevant(symbol, out)
        detail = f"ตัดข่าวที่ไม่เกี่ยวกับ {symbol.strip().upper()} ออก {dropped} ชิ้น" if dropped else ""
        return kept, _source_status(
            name, KIND_NEWS, STATUS_OK, len(kept), detail, fetched=len(out), filtered=dropped
        )
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        return [], _source_status(
            name, KIND_NEWS, STATUS_ERROR, 0, f"{type(exc).__name__}: {exc}"
        )


def fetch_newsapi(symbol: str, api_key: str) -> list[dict[str, Any]]:
    """ดึงข่าวจาก NewsAPI everything; ถ้าล้มเหลวคืน [] ไม่ throw."""
    return fetch_newsapi_status(symbol, api_key)[0]


_REDDIT_SUBS = "ETFs+investing+stocks"


def fetch_reddit_status(symbol: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """ค้นโพสต์ Reddit ในซับที่กำหนด; คืน ``(รายการ, สถานะ)`` ไม่ throw."""
    name = "Reddit"
    sym = (symbol or "").strip()
    if not sym:
        return [], _source_status(name, KIND_SOCIAL, STATUS_OFF, 0, "ไม่ได้ระบุสัญลักษณ์")
    client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    user_agent = (os.getenv("REDDIT_USER_AGENT") or "VaultisBot/1.0").strip() or "VaultisBot/1.0"
    if not client_id or not client_secret:
        return [], _source_status(
            name, KIND_SOCIAL, STATUS_OFF, 0, "ไม่ได้ตั้ง REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET"
        )
    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )
        sub = reddit.subreddit(_REDDIT_SUBS)
        results = sub.search(sym, sort="top", time_filter="week", limit=10)
        out: list[dict[str, Any]] = []
        for post in results:
            selftext = (getattr(post, "selftext", None) or "")[:300]
            created = getattr(post, "created_utc", None)
            if created is not None:
                dt = datetime.fromtimestamp(float(created), tz=timezone.utc)
                published_at = dt.isoformat().replace("+00:00", "Z")
            else:
                published_at = ""
            permalink = (getattr(post, "permalink", None) or "").strip()
            url = f"https://www.reddit.com{permalink}" if permalink else ""
            out.append(
                {
                    "title": getattr(post, "title", "") or "",
                    "description": selftext,
                    "url": url,
                    "published_at": published_at,
                    "source": "reddit",
                    "kind": KIND_SOCIAL,
                }
            )
        return out, _source_status(name, KIND_SOCIAL, STATUS_OK, len(out))
    except Exception as exc:
        return [], _source_status(
            name, KIND_SOCIAL, STATUS_ERROR, 0, f"{type(exc).__name__}: {exc}"
        )


def fetch_reddit(symbol: str) -> list[dict[str, Any]]:
    """ค้นโพสต์ Reddit ในซับที่กำหนด; ถ้าล้มเหลวคืน [] ไม่ throw."""
    return fetch_reddit_status(symbol)[0]


def fetch_stocktwits_status(symbol: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """ดึงสตรีม StockTwits สูงสุด 20 รายการ; คืน ``(รายการ, สถานะ)`` ไม่ throw."""
    name = "StockTwits"
    sym = (symbol or "").strip()
    if not sym:
        return [], _source_status(name, KIND_SOCIAL, STATUS_OFF, 0, "ไม่ได้ระบุสัญลักษณ์")
    url = _STOCKTWITS_STREAM_URL.format(symbol=sym)
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; VaultisNews/1.0; +https://github.com/)"
                ),
                "Accept": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        messages = data.get("messages")
        if not isinstance(messages, list):
            return [], _source_status(
                name, KIND_SOCIAL, STATUS_ERROR, 0, "รูปแบบคำตอบไม่มีคีย์ messages"
            )
        out: list[dict[str, Any]] = []
        for msg in messages[:20]:
            if not isinstance(msg, dict):
                continue
            body = (msg.get("body") or "").strip()
            msg_id = msg.get("id")
            if msg_id is None or not body:
                continue
            out.append(
                {
                    "title": body[:80],
                    "description": body,
                    "url": f"https://stocktwits.com/message/{msg_id}",
                    "published_at": str(msg.get("created_at") or ""),
                    "source": "stocktwits",
                    "kind": KIND_SOCIAL,
                }
            )
        return out, _source_status(name, KIND_SOCIAL, STATUS_OK, len(out))
    except Exception as exc:
        return [], _source_status(
            name, KIND_SOCIAL, STATUS_ERROR, 0, f"{type(exc).__name__}: {exc}"
        )


def fetch_stocktwits(symbol: str) -> list[dict[str, Any]]:
    """ดึงสตรีม StockTwits สำหรับสัญลักษณ์ สูงสุด 20 รายการ; ถ้าล้มเหลวคืน [] ไม่ throw."""
    return fetch_stocktwits_status(symbol)[0]


def fetch_rss_status(
    feed_url: str, kind: str = KIND_NEWS, source_name: str = "RSS"
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse RSS ด้วย feedparser; คืน ``(รายการ, สถานะ)`` ไม่ throw.

    **HTTP 200 ที่ไม่ใช่ฟีด ต้องเป็น ``error`` ไม่ใช่ "ไม่มีข่าว"** (AUDIT.md C1) —
    เซิร์ฟเวอร์ข่าวตอบ 200 พร้อมหน้า HTML ได้บ่อย (หน้า consent, หน้า error, หน้า
    rate-limit) ``feedparser`` ไม่ throw กับ HTML: มันคืน ``entries=[]`` เฉย ๆ
    ⇒ ``raise_for_status()`` ผ่าน แล้วสถานะออกมาเป็น ``ok count=0`` ซึ่งอ่านได้ว่า
    "วันนี้ไม่มีข่าว" ทั้งที่แปลว่าเราดึงไม่ได้เลย

    ตัวชี้ขาดคือ ``parsed.version`` — feedparser ตั้งเป็น ``rss20``/``atom10``/ฯลฯ
    เมื่ออ่านออกว่าเป็นฟีดชนิดไหน และเป็นสตริงว่างเมื่ออ่านไม่ออก **ฟีดที่ว่างจริง ๆ
    ยังมี version** จึงยังได้ ``ok count=0`` ตามเดิม (ห้ามใช้ ``bozo`` เป็นตัวตัดสิน
    ลำพัง: ฟีดที่มีของครบแต่ XML ไม่สะอาดก็ติด ``bozo`` เหมือนกัน)
    """
    url = (feed_url or "").strip()
    if not url:
        return [], _source_status(source_name, kind, STATUS_OFF, 0, "ไม่ได้ระบุ URL ของฟีด")
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; VaultisNews/1.0; +https://github.com/)"
            )
        }
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        entries = getattr(parsed, "entries", None) or []
        if not entries and not str(getattr(parsed, "version", "") or "").strip():
            content_type = str(resp.headers.get("Content-Type") or "ไม่ทราบ").split(";")[0].strip()
            bozo_exc = getattr(parsed, "bozo_exception", None)
            reason = f" ({type(bozo_exc).__name__}: {bozo_exc})" if bozo_exc else ""
            return [], _source_status(
                source_name,
                kind,
                STATUS_ERROR,
                0,
                f"ตอบ HTTP {resp.status_code} แต่เนื้อหาไม่ใช่ฟีด (Content-Type: "
                f"{content_type}){reason} — นี่คือ 'ดึงไม่สำเร็จ' ไม่ใช่ 'ไม่มีข่าว'",
            )
        feed_title = ""
        if getattr(parsed, "feed", None):
            feed_title = (parsed.feed.get("title") or "").strip()
        out: list[dict[str, Any]] = []
        for entry in entries:
            pub = ""
            if getattr(entry, "published_parsed", None):
                pub = _iso_from_struct_time(entry.published_parsed)
            elif getattr(entry, "updated_parsed", None):
                pub = _iso_from_struct_time(entry.updated_parsed)
            link = (entry.get("link") or "").strip()
            title = (entry.get("title") or "").strip()
            desc = (entry.get("summary") or entry.get("description") or "").strip()
            src = feed_title or "RSS"
            out.append(
                {
                    "title": title,
                    "description": desc,
                    "url": link,
                    "published_at": pub,
                    "source": src,
                    "kind": kind,
                }
            )
        return out, _source_status(source_name, kind, STATUS_OK, len(out))
    except (requests.RequestException, TypeError, AttributeError, ValueError) as exc:
        return [], _source_status(source_name, kind, STATUS_ERROR, 0, f"{type(exc).__name__}: {exc}")


def fetch_rss(feed_url: str, kind: str = KIND_NEWS) -> list[dict[str, Any]]:
    """Parse RSS ด้วย feedparser; ถ้าล้มเหลวคืน [] ไม่ throw."""
    return fetch_rss_status(feed_url, kind=kind)[0]


def fetch_yahoo_rss_status(symbol: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """ข่าวราย ticker จาก Yahoo Finance; คืน ``(รายการ, สถานะ)`` ไม่ throw."""
    name = "Yahoo Finance"
    sym = (symbol or "").strip()
    if not sym:
        return [], _source_status(name, KIND_NEWS, STATUS_OFF, 0, "ไม่ได้ระบุสัญลักษณ์")
    return fetch_rss_status(
        YAHOO_RSS_TEMPLATE.format(symbol=sym), kind=KIND_NEWS, source_name=name
    )


def fetch_yahoo_rss(symbol: str) -> list[dict[str, Any]]:
    """ข่าวราย ticker จาก Yahoo Finance; ล้มเหลวคืน [] ไม่ throw."""
    return fetch_yahoo_rss_status(symbol)[0]


def fetch_google_news_status(symbol: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """ข่าวราย ticker จาก Google News; คืน ``(รายการ, สถานะ)`` ไม่ throw.

    ไม่ต้องใช้ API key และไม่ต้องขออนุมัติใคร ต่อคำค้นด้วย ``"<SYM> ETF"`` เพื่อกัน
    ตัวย่อไปชนคำทั่วไป (เช่น VOO/XLV) — วัดแล้วหัวข้อที่เอ่ยถึงสัญลักษณ์จริง
    ~74/101 (VOO) และ ~28/72 (GLDM) ซึ่งเป็นตัวเลขที่ต่ำกว่าความจริงเพราะบางหัวข้อ
    พูดถึงกองโดยไม่พิมพ์ตัวย่อ
    """
    name = "Google News"
    sym = (symbol or "").strip()
    if not sym:
        return [], _source_status(name, KIND_NEWS, STATUS_OFF, 0, "ไม่ได้ระบุสัญลักษณ์")
    items, status = fetch_rss_status(
        GOOGLE_NEWS_RSS_TEMPLATE.format(query=quote_plus(f"{sym} ETF")),
        kind=KIND_NEWS,
        source_name=name,
    )
    if status["status"] != STATUS_OK:
        return items, status
    # คำค้นเป็นข้อความอิสระ ("<SYM> ETF") ไม่ใช่ฟีดที่ผูกกับสัญลักษณ์ จึงต้องกรอง —
    # วัดจริง 2026-08-08 เหลือ 54–85% แล้วแต่กอง (ตัวที่ตัดคือข่าวที่ไม่เอ่ยทั้งตัวย่อ
    # ชื่อกอง และคำกลุ่มอุตสาหกรรมเลย)
    kept, dropped = _filter_relevant(sym, items)
    if dropped:
        status = _source_status(
            name,
            KIND_NEWS,
            STATUS_OK,
            len(kept),
            f"ตัดข่าวที่ไม่เกี่ยวกับ {sym.upper()} ออก {dropped} ชิ้น",
            fetched=len(items),
            filtered=dropped,
        )
    return kept, status


def fetch_google_news(symbol: str) -> list[dict[str, Any]]:
    """ข่าวราย ticker จาก Google News; ล้มเหลวคืน [] ไม่ throw."""
    return fetch_google_news_status(symbol)[0]


def _parse_sort_key(published_at: str) -> datetime:
    if not published_at:
        return datetime.min.replace(tzinfo=timezone.utc)
    s = published_at.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


_MAX_ARTICLES = 30
# กันที่ไว้ให้โซเชียลไม่ให้ถูกข่าวจริงเบียดตกขอบจนหมด — พอเปิด NewsAPI ข่าวจริงมี 40 ชิ้น
# ล้นเพดาน 30 แล้วกฎ "news ก่อน social" ทำให้ StockTwits หายจากหน้าจอทั้งหมด (social=0)
# กันที่เท่าที่โซเชียล "มีจริง" เท่านั้น ไม่มีโซเชียล = ข่าวจริงได้ครบ 30 ไม่เสียช่องเปล่า
_SOCIAL_RESERVED_SLOTS = 5


#: ชื่อพารามิเตอร์ติดตามผลที่ไม่เปลี่ยนตัวบทความ — ตัดทิ้งก่อนเทียบว่าซ้ำกันไหม
_TRACKING_PARAM_PREFIXES = (
    "utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src",
    "guccounter", "guce_", "yptr", ".tsrc", "tsrc", "cmp", "campaign",
)

#: ตัวคั่นที่ผู้รวมข่าวใช้ต่อท้ายชื่อสำนักข่าวเข้ากับพาดหัว
_TITLE_PUBLISHER_SEP = re.compile(r"\s+[-–—|]\s+")
#: ชื่อสำนักข่าวท้ายพาดหัวยาวไม่เกินเท่านี้ (คำ) — ยาวกว่านี้ถือว่าเป็นเนื้อพาดหัวเอง
_MAX_PUBLISHER_WORDS = 5


def _normalize_url(url: str) -> str:
    """URL ในรูปที่เอาไปเทียบว่า "บทความเดียวกัน" ได้ — ตัดสิ่งที่ไม่เปลี่ยนตัวบทความ.

    ตัด scheme, ``www.``, แฟรกเมนต์, ``/`` ท้าย และพารามิเตอร์ติดตามผล แล้วเรียง
    query ที่เหลือ · คลี่ Google News redirect รูปเก่าที่พก ``?url=`` มาด้วย

    ⚠ **อย่าคาดหวังว่าตัวนี้จะจับข่าวซ้ำข้ามแหล่งได้** — วัดจริง 2026-08-08 บนห้ากอง
    มันจับได้ **0 ชิ้นเพิ่ม** เพราะ Google News สมัยใหม่คืนลิงก์ทึบ
    (``news.google.com/rss/articles/CBMi…``) ที่ไม่มี URL ปลายทางอยู่ในนั้นเลย
    ตัวที่ทำงานจริงคือกุญแจพาดหัวด้านล่าง ตัวนี้ยังต้องมีเพราะมันคือด่านที่ถูกต้อง
    สำหรับลิงก์ตรงที่ต่างกันแค่พารามิเตอร์ติดตามผล (เช่น ``?.tsrc=rs`` ของ Yahoo)
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        split = urlsplit(raw)
    except ValueError:
        return raw.lower()
    host = split.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = parse_qsl(split.query, keep_blank_values=False)
    if "news.google.com" in host:
        for key, value in query:
            if key.lower() == "url" and value:
                unwrapped = unquote(value)
                # กันลูปเมื่อ redirect ชี้กลับมาที่ตัวเอง
                return _normalize_url(unwrapped) if "news.google.com" not in unwrapped else raw.lower()
    kept = [
        (key, value)
        for key, value in query
        if not key.lower().lstrip(".").startswith(_TRACKING_PARAM_PREFIXES)
    ]
    return urlunsplit(("", host, split.path.rstrip("/"), urlencode(sorted(kept)), ""))


def _title_key(title: str) -> str:
    """กุญแจ "บทความเดียวกัน" จากพาดหัว — ตัวจับข่าวซ้ำข้ามแหล่งตัวจริง.

    ตัดชื่อสำนักข่าวที่ต่อท้าย (``"... - The Motley Fool"`` แบบที่ Google News ใส่มา)
    แล้วลดเหลือตัวอักษร/ตัวเลขล้วน  **เทียบด้วยสตริงเต็ม ไม่ตัดความยาว** เพราะการตัด
    N ตัวแรกทำให้พาดหัวคนละบทความที่ขึ้นต้นเหมือนกันชนกันได้ (ชุดนี้มีเยอะมาก:
    ``"Which is the better State Street healthcare ETF: the broad XLV or …"``)

    วัดจริง 2026-08-08 บน Yahoo RSS + Google News: กุญแจนี้จับข่าวซ้ำได้
    VOO 15 · SCHD 23 · QQQM 12 · XLV 12 · GLDM 8 ชิ้น ขณะที่การเทียบพาดหัวดิบ
    จับได้ 2 · 1 · 1 · 2 · 0 (เพราะติดชื่อสำนักข่าวท้ายพาดหัวอยู่)
    """
    raw = " ".join(str(title or "").split())
    if not raw:
        return ""
    parts = _TITLE_PUBLISHER_SEP.split(raw)
    if len(parts) > 1 and len(parts[-1].split()) <= _MAX_PUBLISHER_WORDS:
        raw = " ".join(parts[:-1])
    return " ".join(re.sub(r"[^0-9a-z]+", " ", raw.lower()).split())


def _merge_and_rank(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ลบซ้ำ (url + พาดหัว) แล้วจัดลำดับ: ข่าวจริงก่อนโซเชียล ภายในกลุ่มเรียงล่าสุดก่อน.

    เพดานรวม ``_MAX_ARTICLES`` โดยกันช่องให้โซเชียลไว้ ``_SOCIAL_RESERVED_SLOTS`` ช่อง
    ฝั่งไหนมีของไม่ถึงโควตา อีกฝั่งได้ช่องที่เหลือไปใช้ต่อ — ไม่มีช่องว่างเปล่า

    **ลบซ้ำสองชั้น** เพราะชั้นเดียวไม่พอ: เทียบ url ดิบจับข่าวซ้ำข้ามแหล่งไม่ได้เลย
    (Google News คืนลิงก์ทึบคนละอันกับลิงก์ตรงของ Yahoo) บทความเดียวจึงกินได้หลายช่อง
    จาก 30 ช่อง และตอนเข้า sentiment มันถูกนับเป็นหลายเสียงจนพลิกป้ายได้
    ตัวแรกที่เจอชนะ — ลำดับการรวมคือ Yahoo → Google → NewsAPI ซึ่งทำให้ลิงก์ตรงของ
    สำนักข่าวชนะลิงก์ทึบของ Google โดยอัตโนมัติ

    รายการที่ **ไม่มี url** ยังถูกตัดทิ้งเหมือนเดิม (ไม่มีลิงก์ให้ผู้ใช้กดดู) และ
    รายการที่ไม่มีพาดหัวจะถูกตัดสินด้วย url อย่างเดียว ไม่ใช่ยุบรวมกันทั้งหมด
    """
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in merged:
        u = (item.get("url") or "").strip()
        if not u:
            continue
        url_key = _normalize_url(u) or u
        if url_key in seen_urls:
            continue
        title_key = _title_key(str(item.get("title") or ""))
        if title_key and title_key in seen_titles:
            continue
        seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)
        unique.append(item)

    # เรียงสองรอบ (sort ของ Python เสถียร): เวลาใหม่ก่อน แล้วดัน news ขึ้นเหนือ social
    unique.sort(key=lambda x: _parse_sort_key(str(x.get("published_at") or "")), reverse=True)
    unique.sort(key=lambda x: 0 if str(x.get("kind") or KIND_NEWS) == KIND_NEWS else 1)

    news = [x for x in unique if str(x.get("kind") or KIND_NEWS) == KIND_NEWS]
    social = [x for x in unique if str(x.get("kind") or KIND_NEWS) != KIND_NEWS]

    reserved = min(_SOCIAL_RESERVED_SLOTS, len(social))
    kept_news = news[: _MAX_ARTICLES - reserved]
    kept_social = social[: _MAX_ARTICLES - len(kept_news)]
    return kept_news + kept_social


def get_news(symbol: str) -> list[dict[str, Any]]:
    """รวมข่าว+โซเชียลของ ``symbol`` ลบซ้ำตาม url เรียงล่าสุดก่อน สูงสุด 30 รายการ.

    ทุกแหล่งผูกกับสัญลักษณ์ที่ขอ — ไม่มีฟีดข่าวทั่วไปปนเข้ามาแล้ว (ดู docstring บนสุด)
    ข่าวจริง (``kind="news"``) ถูกจัดลำดับก่อนโซเชียลเสมอ เพื่อไม่ให้โพสต์ StockTwits
    ซึ่งมีปริมาณมากและใหม่กว่า เบียดข่าวจริงตกขอบ 30 รายการ
    """
    api_key = os.getenv("NEWSAPI_KEY", "").strip()

    merged: list[dict[str, Any]] = []
    merged.extend(fetch_yahoo_rss(symbol))
    merged.extend(fetch_google_news(symbol))
    merged.extend(fetch_newsapi(symbol, api_key))
    merged.extend(fetch_reddit(symbol))
    merged.extend(fetch_stocktwits(symbol))

    return _merge_and_rank(merged)


def get_news_with_status(symbol: str) -> dict[str, Any]:
    """เหมือน ``get_news`` แต่แนบสถานะรายแหล่งมาด้วย สำหรับหน้าจอที่ต้องรายงานความจริง.

    คืน ``{"symbol", "items", "sources", "news_count", "social_count",
    "has_error", "all_news_sources_failed"}``

    ``all_news_sources_failed`` = แหล่งข่าวจริงทุกตัวที่เปิดอยู่ล้มเหลวหมด → ผู้เรียก
    **ต้องไม่แสดงว่า "ไม่มีข่าว"** เพราะนั่นคือความล้มเหลวที่ปลอมตัวเป็นข้อมูล (AUDIT.md C1)
    """
    api_key = os.getenv("NEWSAPI_KEY", "").strip()

    merged: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for items, status in (
        fetch_yahoo_rss_status(symbol),
        fetch_google_news_status(symbol),
        fetch_newsapi_status(symbol, api_key),
        fetch_reddit_status(symbol),
        fetch_stocktwits_status(symbol),
    ):
        merged.extend(items)
        sources.append(status)

    ranked = _merge_and_rank(merged)
    news_sources = [s for s in sources if s["kind"] == KIND_NEWS]
    live_news_sources = [s for s in news_sources if s["status"] != STATUS_OFF]

    return {
        "symbol": (symbol or "").strip().upper(),
        "items": ranked,
        "sources": sources,
        "news_count": sum(1 for a in ranked if a.get("kind") == KIND_NEWS),
        "social_count": sum(1 for a in ranked if a.get("kind") == KIND_SOCIAL),
        "has_error": any(s["status"] == STATUS_ERROR for s in sources),
        "all_news_sources_failed": bool(live_news_sources)
        and all(s["status"] == STATUS_ERROR for s in live_news_sources),
    }
