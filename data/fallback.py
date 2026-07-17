# -*- coding: utf-8 -*-
"""แหล่งราคาสำรองสำหรับ "ราคาล่าสุด" เท่านั้น (Roadmap Phase 0 ข้อ 3).

ลำดับแหล่งข้อมูล: yfinance → Stooq (ฟรี ไม่ใช้ key) → Alpha Vantage
(ชั้นสุดท้าย ทำงานเฉพาะเมื่อตั้ง env ``ALPHAVANTAGE_API_KEY`` — free tier 25 req/วัน)

นโยบาย (ห้ามละเมิด):
- ใช้กับ "ราคาล่าสุด/สัญญาณวันนี้" เท่านั้น — **ห้ามผสมเข้า series ประวัติที่ใช้คำนวณ score**
  เพราะ Stooq/Alpha Vantage คืนราคาที่ไม่ได้ adjust เงินปันผล (≠ Adj Close ของ yfinance)
  ถ้าเอาไปต่อ RSI/MA/score ค่าจะเพี้ยนแบบเงียบ ๆ ซึ่งขัดหลัก fail-loud ของระบบ
- ticker ที่ดึงไม่ได้จากทุกแหล่ง = หายไปจากผลลัพธ์ (ห้ามเดาเป็น 0.0 — never fabricate)
- ถ้าไม่ได้ราคาเลยสักตัว → raise ``PriceDataUnavailableError`` (fail loud)
"""

from __future__ import annotations

import logging
import os

import pandas as pd
import requests
import yfinance as yf

from data.fetcher import PriceDataUnavailableError, normalize_close_series

logger = logging.getLogger(__name__)

_STOOQ_QUOTE_URL = "https://stooq.com/q/l/"
_ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
_HTTP_TIMEOUT_SECONDS = 10


def _normalize_tickers(tickers: list[str]) -> list[str]:
    """ตัดช่องว่าง/ตัวซ้ำ และบังคับตัวพิมพ์ใหญ่ (ตามแบบ get_current_prices เดิม)."""
    return sorted({str(t).strip().upper() for t in tickers if str(t).strip()})


def _yf_latest_close(tickers: list[str]) -> dict[str, float]:
    """ชั้นหลัก: ราคาปิดล่าสุดจาก yfinance (ดึง 5 วันเผื่อวันหยุดตลาด)."""
    try:
        downloaded = yf.download(
            tickers=tickers,
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
        )
    except Exception as exc:
        logger.warning("yfinance ดึงราคาล่าสุดไม่สำเร็จ (%s): %s", tickers, exc)
        return {}

    if downloaded is None or downloaded.empty:
        return {}

    prices: dict[str, float] = {}
    if isinstance(downloaded.columns, pd.MultiIndex):
        available = set(downloaded.columns.get_level_values(0))
        for ticker in tickers:
            if ticker not in available:
                continue
            close_series = normalize_close_series(downloaded[ticker])
            if not close_series.empty:
                prices[ticker] = float(close_series.iloc[-1])
        return prices

    close_series = normalize_close_series(downloaded)
    if len(tickers) == 1 and not close_series.empty:
        prices[tickers[0]] = float(close_series.iloc[-1])
    return prices


def fetch_latest_close_stooq(tickers: list[str]) -> dict[str, float]:
    """ราคาปิดล่าสุดจาก Stooq — **ไม่ adjust ปันผล** ใช้แสดงผล/แจ้งเตือนเท่านั้น.

    endpoint: ``https://stooq.com/q/l/?s=voo.us&f=sd2t2ohlcv&h&e=csv`` (วนทีละ ticker)
    ค่า ``N/D``/ว่าง/ไม่เป็นบวก → ข้าม ticker นั้น (ห้ามใส่ 0.0)
    """
    prices: dict[str, float] = {}
    for ticker in _normalize_tickers(tickers):
        try:
            response = requests.get(
                _STOOQ_QUOTE_URL,
                params={"s": f"{ticker.lower()}.us", "f": "sd2t2ohlcv", "h": "", "e": "csv"},
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            lines = [line.strip() for line in response.text.strip().splitlines() if line.strip()]
            if len(lines) < 2:
                continue
            header = [col.strip().lower() for col in lines[0].split(",")]
            record = dict(zip(header, lines[1].split(",")))
            close_text = str(record.get("close", "")).strip()
            if not close_text or close_text.upper() == "N/D":
                continue
            close_value = float(close_text)
            if close_value > 0:
                prices[ticker] = close_value
        except Exception as exc:
            logger.warning("Stooq ดึงราคา %s ไม่สำเร็จ: %s", ticker, exc)
    return prices


def _fetch_latest_close_alphavantage(tickers: list[str], api_key: str) -> dict[str, float]:
    """ชั้นสุดท้าย (optional): Alpha Vantage GLOBAL_QUOTE.

    free tier 25 requests/วัน — เรียกเฉพาะ ticker ที่สองชั้นแรกไม่ได้ราคาเท่านั้น
    """
    prices: dict[str, float] = {}
    for ticker in _normalize_tickers(tickers):
        try:
            response = requests.get(
                _ALPHAVANTAGE_URL,
                params={"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": api_key},
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json() or {}
            quote = payload.get("Global Quote") or {}
            price_text = str(quote.get("05. price", "")).strip()
            if not price_text:
                continue
            price_value = float(price_text)
            if price_value > 0:
                prices[ticker] = price_value
        except Exception as exc:
            logger.warning("Alpha Vantage ดึงราคา %s ไม่สำเร็จ: %s", ticker, exc)
    return prices


def get_latest_prices_with_fallback(tickers: list[str]) -> dict[str, float]:
    """ราคาล่าสุดพร้อม fallback: yfinance → Stooq → Alpha Vantage (ถ้าตั้ง key).

    คืน dict เฉพาะ ticker ที่ได้ราคา — ตัวที่ล้มเหลวทุกแหล่งจะหายไปจากผล
    (callers เดิมรองรับ ticker หายอยู่แล้ว) และถ้าไม่ได้ราคาเลยสักตัว
    → raise ``PriceDataUnavailableError``
    """
    normalized = _normalize_tickers(tickers)
    if not normalized:
        return {}

    prices = _yf_latest_close(normalized)

    missing = [t for t in normalized if t not in prices]
    if missing:
        stooq_prices = fetch_latest_close_stooq(missing)
        if stooq_prices:
            logger.warning(
                "ใช้ราคาสำรองจาก Stooq (ไม่ adjust ปันผล — แสดงผล/แจ้งเตือนเท่านั้น): %s",
                ", ".join(sorted(stooq_prices)),
            )
        prices.update(stooq_prices)

    missing = [t for t in normalized if t not in prices]
    api_key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    if missing and api_key:
        av_prices = _fetch_latest_close_alphavantage(missing, api_key)
        if av_prices:
            logger.warning("ใช้ราคาสำรองจาก Alpha Vantage: %s", ", ".join(sorted(av_prices)))
        prices.update(av_prices)

    if not prices:
        raise PriceDataUnavailableError(
            f"ดึงราคาล่าสุดไม่สำเร็จจากทุกแหล่ง (yfinance/Stooq/Alpha Vantage): {', '.join(normalized)}"
        )

    still_missing = [t for t in normalized if t not in prices]
    if still_missing:
        logger.warning("ไม่ได้ราคาล่าสุดของ: %s (ตัดออกจากผล — ห้ามเดาค่า)", ", ".join(still_missing))
    return prices
