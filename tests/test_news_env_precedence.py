# -*- coding: utf-8 -*-
"""env ของโปรเซสต้องชนะไฟล์ ``.env`` ใน ``analysis/news_fetcher.py``.

``analysis/llm.py``, ``analysis/ai_advisor.py`` และ ``analysis/macro.py`` ย้าย
``load_dotenv`` ไปทำครั้งเดียวตอน import แล้ว (env ตอนรันคือแหล่งความจริง ไฟล์ ``.env``
เป็นแค่ค่าเริ่มต้นตอนบูต) แต่ไฟล์นี้ตกค้าง — ยังเรียก ``load_dotenv`` **ในตัวฟังก์ชัน**
ทั้ง ``fetch_reddit_status``, ``get_news`` และ ``get_news_with_status``

ผลคือ unset ``NEWSAPI_KEY`` / ``REDDIT_CLIENT_ID`` ในโปรเซสไม่มีผลเลย ไฟล์เติมกลับมา
ให้ทุกครั้ง = ไฟล์ชนะ env เสมอ  เรื่องนี้ร้ายกับไฟล์นี้เป็นพิเศษเพราะ CLAUDE.md กำหนดว่า
สถานะแหล่งข่าวต้องแยก ``ok`` / ``error`` / ``off`` ให้ออก โดย ``off`` = ไม่ได้ตั้งคีย์
ซึ่งไม่ใช่ความล้มเหลว — บนเครื่องที่มี ``.env`` สถานะ ``off`` จึงเกิดขึ้นไม่ได้เลย
ทั้งที่ผู้ใช้ปิดแหล่งนั้นไปแล้ว (และเงียบ ๆ ยิง API ที่นับโควตาต่อไป)

เทสต์ทั้งหมด stub HTTP/praw ครบ — ไม่มีการยิงเน็ตจริง
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from analysis import news_fetcher as nf

_SRC_PATH = Path(nf.__file__).resolve()

_DOTENV_NEWSAPI_KEY = "คีย์จากไฟล์-ห้ามถูกใช้"
_DOTENV_REDDIT_ID = "reddit-id-จากไฟล์"


# ---------------------------------------------------------------- ตัวช่วย stub


class _FakeResp:
    """ตอบได้ทั้งฟีด RSS ว่าง ๆ และ JSON ของ NewsAPI/StockTwits ในตัวเดียว."""

    content = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"status": "ok", "articles": [], "messages": []}


@pytest.fixture
def stub_network(monkeypatch):
    """ตัดเน็ตทั้งหมด และจดว่ามีการยิงไปที่ URL ไหนบ้าง."""
    urls: list[str] = []

    def _fake_get(url, **_kwargs):
        urls.append(str(url))
        return _FakeResp()

    def _no_reddit(**_kwargs):
        raise AssertionError("ไม่ควรสร้าง praw.Reddit เมื่อไม่ได้ตั้งคีย์ในโปรเซส")

    monkeypatch.setattr(nf.requests, "get", _fake_get)
    monkeypatch.setattr(nf.praw, "Reddit", _no_reddit)
    return urls


@pytest.fixture
def dotenv_file_with_keys(tmp_path, monkeypatch):
    """จำลองเครื่องที่มี ``.env`` ตั้งคีย์ไว้ แต่ผู้ใช้ unset คีย์นั้นในโปรเซสแล้ว.

    ชี้ ``ROOT_DIR`` ไปโฟลเดอร์ชั่วคราวแทนรากจริง เพื่อให้เทสต์ให้ผลเดิมทุกเครื่อง
    ไม่ว่าเครื่องนั้นจะมี ``.env`` จริงหรือไม่
    """
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"NEWSAPI_KEY={_DOTENV_NEWSAPI_KEY}\n"
        f"REDDIT_CLIENT_ID={_DOTENV_REDDIT_ID}\n"
        "REDDIT_CLIENT_SECRET=reddit-secret-จากไฟล์\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nf, "ROOT_DIR", tmp_path)
    for name in ("NEWSAPI_KEY", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    return env_path


def _status_of(result: dict, name: str) -> dict:
    return next(s for s in result["sources"] if s["name"] == name)


# ------------------------------------------------------------ โครงสร้างของซอร์ส


class TestLoadDotenvHappensOnceAtImport:
    """``load_dotenv`` ต้องอยู่ระดับโมดูลเท่านั้น — เรียกซ้ำในฟังก์ชัน = ไฟล์ชนะ env."""

    def test_no_load_dotenv_inside_any_function(self):
        tree = ast.parse(_SRC_PATH.read_text(encoding="utf-8"))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "load_dotenv"
                ):
                    offenders.append(f"{node.name}() บรรทัด {inner.lineno}")

        assert not offenders, (
            "เรียก load_dotenv ในฟังก์ชันทำให้ไฟล์ .env เติมค่าที่ถูก unset กลับมาทุกครั้ง "
            "(env ของโปรเซสจึงไม่มีทางชนะไฟล์): " + ", ".join(offenders)
        )

    def test_still_loads_dotenv_at_module_level(self):
        """ห้ามแก้ด้วยการลบทิ้ง — ``.env`` ต้องยังเป็นค่าเริ่มต้นตอนบูตเหมือนเดิม."""
        tree = ast.parse(_SRC_PATH.read_text(encoding="utf-8"))
        module_level = [
            n
            for n in tree.body
            if isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name)
            and n.value.func.id == "load_dotenv"
        ]
        assert len(module_level) == 1, "ต้องเรียก load_dotenv ครั้งเดียวตอน import"


# ------------------------------------------------------------------ พฤติกรรม


class TestProcessEnvWinsOverDotenvFile:
    def test_unset_newsapi_key_makes_source_off_even_with_dotenv(
        self, dotenv_file_with_keys, stub_network
    ):
        """unset NEWSAPI_KEY → NewsAPI ต้องเป็น ``off`` ไม่ใช่ถูกไฟล์เปิดกลับมาให้."""
        result = nf.get_news_with_status("VOO")

        newsapi = _status_of(result, "NewsAPI")
        assert newsapi["status"] == nf.STATUS_OFF
        assert "NEWSAPI_KEY" in newsapi["detail"]
        assert not [u for u in stub_network if "newsapi.org" in u], (
            "ยิง NewsAPI ทั้งที่ผู้ใช้ unset คีย์แล้ว — กินโควตาโดยที่ผู้ใช้สั่งปิดไปแล้ว"
        )

    def test_unset_reddit_credentials_make_source_off_even_with_dotenv(
        self, dotenv_file_with_keys, stub_network
    ):
        items, status = nf.fetch_reddit_status("VOO")

        assert items == []
        assert status["status"] == nf.STATUS_OFF
        assert "REDDIT_CLIENT_ID" in status["detail"]

    def test_get_news_does_not_resurrect_newsapi_key(
        self, dotenv_file_with_keys, stub_network
    ):
        """เส้นทาง ``get_news`` (sentiment job ใช้ตัวนี้) ต้องเคารพ env เหมือนกัน."""
        nf.get_news("VOO")

        assert not [u for u in stub_network if "newsapi.org" in u]

    def test_process_env_is_not_mutated_by_the_call(
        self, dotenv_file_with_keys, stub_network
    ):
        """เรียกฟังก์ชันข่าวแล้วห้ามมีคีย์โผล่กลับเข้า ``os.environ`` เอง."""
        import os

        nf.get_news_with_status("VOO")

        assert "NEWSAPI_KEY" not in os.environ
        assert "REDDIT_CLIENT_ID" not in os.environ


class TestEnvKeyStillTakesEffect:
    """กันแก้เกินตัว: ตั้งคีย์ใน env แล้วต้องยังใช้ได้ตามปกติ."""

    def test_newsapi_key_from_process_env_is_used(self, monkeypatch, stub_network):
        monkeypatch.setattr(nf, "ROOT_DIR", Path("/ไม่มีโฟลเดอร์นี้"))
        monkeypatch.setenv("NEWSAPI_KEY", "คีย์จาก-env")

        result = nf.get_news_with_status("VOO")

        assert _status_of(result, "NewsAPI")["status"] == nf.STATUS_OK
        assert [u for u in stub_network if "newsapi.org" in u]
