"""ดึงข่าว **ที่เกี่ยวกับสัญลักษณ์นั้นจริง ๆ** จาก Yahoo Finance, NewsAPI, Reddit และ StockTwits.

เดิม ``get_news`` ยัดฟีด RSS ข่าวเศรษฐกิจไทย (Settrade/Thairath) เข้าไปทุกสัญลักษณ์
โดยไม่กรอง — ข่าวหุ้นไทยชุดเดียวกันจึงกลายเป็น "ข่าวของ" VOO/SCHD/QQQM/XLV/GLDM
พร้อมกันทั้งห้าตัว (และตอนตรวจ 2026-07-28 ทั้งสองฟีดคืน 0 รายการอยู่แล้ว)
ตอนนี้ทุกแหล่งใน ``get_news`` ผูกกับสัญลักษณ์ที่ขอเสมอ

แต่ละรายการมี ``kind``:
- ``news``   — ข่าวจากสำนักข่าว (Yahoo Finance RSS, NewsAPI)
- ``social`` — ความเห็นนักลงทุนรายย่อย (Reddit, StockTwits) **ไม่ใช่ข่าว**
  แยกไว้เพื่อไม่ให้ผู้อ่านผลเข้าใจว่าโพสต์เชียร์หุ้นคือรายงานข่าว
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import praw
import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]

KIND_NEWS = "news"
KIND_SOCIAL = "social"

# ฟีดข่าวราย ticker ของ Yahoo — ไม่ต้องใช้ API key จึงเป็นแหล่ง "ข่าวจริง" ตัวเดียว
# ที่ทำงานได้ทันทีโดยไม่ต้องตั้งค่าอะไรเพิ่ม
YAHOO_RSS_TEMPLATE = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"

_NEWSAPI_URL = "https://newsapi.org/v2/everything"
_STOCKTWITS_STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"


def _iso_from_struct_time(st: Any) -> str:
    if st is None:
        return ""
    try:
        dt = datetime(*st[:6], tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return ""


def fetch_newsapi(symbol: str, api_key: str) -> list[dict[str, Any]]:
    """ดึงข่าวจาก NewsAPI everything; ถ้าล้มเหลวคืน [] ไม่ throw."""
    key = (api_key or "").strip()
    if not key or not (symbol or "").strip():
        return []
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
            return []
        out: list[dict[str, Any]] = []
        for a in data.get("articles") or []:
            src = a.get("source") or {}
            name = src.get("name") if isinstance(src, dict) else None
            out.append(
                {
                    "title": a.get("title") or "",
                    "description": a.get("description") or "",
                    "url": a.get("url") or "",
                    "published_at": a.get("publishedAt") or "",
                    "source": name or "NewsAPI",
                    "kind": KIND_NEWS,
                }
            )
        return out
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return []


_REDDIT_SUBS = "ETFs+investing+stocks"


def fetch_reddit(symbol: str) -> list[dict[str, Any]]:
    """ค้นโพสต์ Reddit ในซับที่กำหนด; ถ้าล้มเหลวคืน [] ไม่ throw."""
    sym = (symbol or "").strip()
    if not sym:
        return []
    load_dotenv(ROOT_DIR / ".env")
    client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    user_agent = (os.getenv("REDDIT_USER_AGENT") or "VaultisBot/1.0").strip() or "VaultisBot/1.0"
    if not client_id or not client_secret:
        return []
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
        return out
    except Exception:
        return []


def fetch_stocktwits(symbol: str) -> list[dict[str, Any]]:
    """ดึงสตรีม StockTwits สำหรับสัญลักษณ์ สูงสุด 20 รายการ; ถ้าล้มเหลวคืน [] ไม่ throw."""
    sym = (symbol or "").strip()
    if not sym:
        return []
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
            return []
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
        return out
    except Exception:
        return []


def fetch_rss(feed_url: str, kind: str = KIND_NEWS) -> list[dict[str, Any]]:
    """Parse RSS ด้วย feedparser; ถ้าล้มเหลวคืน [] ไม่ throw."""
    url = (feed_url or "").strip()
    if not url:
        return []
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; VaultisNews/1.0; +https://github.com/)"
            )
        }
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        feed_title = ""
        if getattr(parsed, "feed", None):
            feed_title = (parsed.feed.get("title") or "").strip()
        out: list[dict[str, Any]] = []
        for entry in getattr(parsed, "entries", None) or []:
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
        return out
    except (requests.RequestException, TypeError, AttributeError, ValueError):
        return []


def fetch_yahoo_rss(symbol: str) -> list[dict[str, Any]]:
    """ข่าวราย ticker จาก Yahoo Finance; ล้มเหลวคืน [] ไม่ throw."""
    sym = (symbol or "").strip()
    if not sym:
        return []
    return fetch_rss(YAHOO_RSS_TEMPLATE.format(symbol=sym), kind=KIND_NEWS)


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


def get_news(symbol: str) -> list[dict[str, Any]]:
    """รวมข่าว+โซเชียลของ ``symbol`` ลบซ้ำตาม url เรียงล่าสุดก่อน สูงสุด 30 รายการ.

    ทุกแหล่งผูกกับสัญลักษณ์ที่ขอ — ไม่มีฟีดข่าวทั่วไปปนเข้ามาแล้ว (ดู docstring บนสุด)
    ข่าวจริง (``kind="news"``) ถูกจัดลำดับก่อนโซเชียลเสมอ เพื่อไม่ให้โพสต์ StockTwits
    ซึ่งมีปริมาณมากและใหม่กว่า เบียดข่าวจริงตกขอบ 30 รายการ
    """
    load_dotenv(ROOT_DIR / ".env")
    api_key = os.getenv("NEWSAPI_KEY", "").strip()

    merged: list[dict[str, Any]] = []
    merged.extend(fetch_yahoo_rss(symbol))
    merged.extend(fetch_newsapi(symbol, api_key))
    merged.extend(fetch_reddit(symbol))
    merged.extend(fetch_stocktwits(symbol))

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in merged:
        u = (item.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        unique.append(item)

    # เรียงสองรอบ (sort ของ Python เสถียร): เวลาใหม่ก่อน แล้วดัน news ขึ้นเหนือ social
    unique.sort(key=lambda x: _parse_sort_key(str(x.get("published_at") or "")), reverse=True)
    unique.sort(key=lambda x: 0 if str(x.get("kind") or KIND_NEWS) == KIND_NEWS else 1)
    return unique[:_MAX_ARTICLES]
