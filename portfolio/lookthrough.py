# -*- coding: utf-8 -*-
"""ทะลุกอง ETF ลงไปดูหุ้นและเซกเตอร์ที่ถืออยู่จริง (FIX_PLAN เฟส 4③).

**ข้อมูลนี้ระบบไม่เคยมีเลย และมันเปลี่ยนภาพพอร์ตทั้งใบ** — หัวข้อ "การกระจายจริง &
ความทับซ้อน" บนหน้าจอมีแค่ correlation matrix กับข้อความบรรยาย ไม่มีตัวเลขความทับซ้อน
สักตัว ทั้งที่ yfinance ให้ฟรีผ่าน ``Ticker(t).funds_data``

สามอย่างที่ผู้ใช้เข้าใจผิดอยู่ (วัดตอนตรวจ):

- คิดว่าถือ healthcare 10% (XLV) — **จริงคือ 19.02%** เพราะ SCHD มี healthcare 20.77%
  และ VOO มี 8.9%
- คิดว่ากระจาย 5 กอง — **NVDA ตัวเดียวกิน 4.14%** ของพอร์ตทั้งใบ
- VOO–QQQM correlation 0.94 ⇒ เงินกว่าครึ่งอยู่ในสินทรัพย์ที่แทบเป็นตัวเดียวกัน

**ทุกตัวเลขในไฟล์นี้เป็นสถิติเชิงพรรณนา — ห้ามไหลเข้าเลขคะแนนหรือการจัดสรร DCA**
(invariant เดียวกับ ``trend_channel.py`` และ ``news_fetcher.py``)

**เป็นขอบล่างเสมอ ไม่ใช่ตัวเลขเต็ม** yfinance ให้แค่ top-10 ของแต่ละกอง น้ำหนักหุ้นราย
ตัวที่คำนวณได้จึงเป็น "อย่างน้อยเท่านี้" ผู้เรียกต้องพูดออกมา ห้ามนำเสนอเป็นสัดส่วนเต็ม
(ต่างจาก ``sector_weightings`` ที่ครอบทั้งกองและรวมได้ ~100%)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

#: กองที่ดึง funds_data ไม่ได้ ต้องถูกรายงาน ไม่ใช่หายจากตัวหารเงียบ ๆ
UNAVAILABLE = "unavailable"


def _fund_data(symbol: str) -> tuple[pd.DataFrame | None, dict[str, float] | None, str]:
    """``(top_holdings, sector_weightings, เหตุผลที่ไม่ได้)`` — ไม่ throw.

    yfinance โยน exception ได้หลายชนิด (เครือข่าย, กองที่ไม่ใช่ ETF, รูปแบบเปลี่ยน)
    กองที่ดึงไม่ได้ต้อง**ถูกนับและรายงาน** ไม่ใช่หายไปจากตัวหารจนสัดส่วนกองที่เหลือพองขึ้น
    — บั๊กเดียวกับที่ ``rebalance_service`` เคยโดน (ราคาหาย = ตัวหารเล็กลง)
    """
    try:
        import yfinance

        funds = yfinance.Ticker(str(symbol).strip().upper()).funds_data
        holdings = getattr(funds, "top_holdings", None)
        sectors = getattr(funds, "sector_weightings", None)
    except Exception as exc:  # noqa: BLE001 — ต้นทางโยนได้หลายชนิด รวมถึงชนิดของ yfinance เอง
        return None, None, f"{type(exc).__name__}: {exc}"
    if holdings is None and not sectors:
        return None, None, "ผู้ให้ข้อมูลไม่มี funds_data ของกองนี้"
    return (
        holdings if isinstance(holdings, pd.DataFrame) else None,
        dict(sectors) if isinstance(sectors, dict) else None,
        "",
    )


def look_through(weights: dict[str, float]) -> dict[str, Any]:
    """กระจายน้ำหนักพอร์ตลงไปถึงหุ้นรายตัวและเซกเตอร์.

    ``weights``: ``{ticker: น้ำหนัก}`` (ดิบหรือสัดส่วนก็ได้ — normalize ให้ภายใน)

    คืน ``{"holdings", "sectors", "covered_weight", "unavailable", "notes"}``:

    - ``holdings``: ``[{"symbol", "name", "weight_pct", "via": [กองที่ถือ]}]`` เรียงมากไปน้อย
      **เป็นขอบล่าง** เพราะมาจาก top-10 ของแต่ละกองเท่านั้น
    - ``sectors``: ``{sector: น้ำหนัก%}`` — ครอบทั้งกอง จึงเป็นตัวเลขเต็ม
    - ``covered_weight``: สัดส่วนของพอร์ตที่ดึง funds_data ได้ (0–1) — ต่ำกว่า 1 เมื่อไร
      แปลว่าตัวเลขข้างบนคิดจากพอร์ตแค่บางส่วน ผู้เรียก**ต้องบอก**
    - ``unavailable``: ``{ticker: เหตุผล}``

    ไม่มีน้ำหนักที่ใช้ได้เลย → ``ValueError`` (ไม่ใช่คืนตารางว่างที่อ่านเหมือน "ไม่มีความทับซ้อน")
    """
    usable = {
        str(t).strip().upper(): float(w)
        for t, w in (weights or {}).items()
        if isinstance(w, (int, float)) and float(w) > 0
    }
    if not usable:
        raise ValueError("ไม่มีน้ำหนักพอร์ตที่ใช้ได้ — ทะลุกองไม่ได้")
    total = sum(usable.values())
    normalized = {t: w / total for t, w in usable.items()}

    stock_weight: dict[str, float] = {}
    stock_name: dict[str, str] = {}
    stock_via: dict[str, set[str]] = {}
    sector_weight: dict[str, float] = {}
    unavailable: dict[str, str] = {}
    covered = 0.0

    for ticker, weight in normalized.items():
        holdings, sectors, error = _fund_data(ticker)
        if error:
            unavailable[ticker] = error
            continue
        covered += weight
        if holdings is not None and "Holding Percent" in holdings.columns:
            for symbol, row in holdings.iterrows():
                share = pd.to_numeric(row.get("Holding Percent"), errors="coerce")
                if pd.isna(share) or float(share) <= 0:
                    continue
                key = str(symbol).strip().upper()
                stock_weight[key] = stock_weight.get(key, 0.0) + weight * float(share)
                stock_name.setdefault(key, str(row.get("Name") or key))
                stock_via.setdefault(key, set()).add(ticker)
        for sector, share in (sectors or {}).items():
            value = pd.to_numeric(share, errors="coerce")
            if pd.isna(value) or float(value) <= 0:
                continue
            sector_weight[str(sector)] = sector_weight.get(str(sector), 0.0) + weight * float(value)

    holdings_out = [
        {
            "symbol": symbol,
            "name": stock_name.get(symbol, symbol),
            "weight_pct": round(value * 100.0, 4),
            "via": sorted(stock_via.get(symbol, set())),
        }
        for symbol, value in sorted(stock_weight.items(), key=lambda kv: kv[1], reverse=True)
    ]
    sectors_out = {
        sector: round(value * 100.0, 4)
        for sector, value in sorted(sector_weight.items(), key=lambda kv: kv[1], reverse=True)
    }
    return {
        "holdings": holdings_out,
        "sectors": sectors_out,
        "covered_weight": round(covered, 6),
        "unavailable": unavailable,
        "notes": describe_coverage(covered, unavailable),
    }


def describe_coverage(covered: float, unavailable: dict[str, str]) -> str:
    """ประโยคไทยบอกว่าตัวเลขทะลุกองคิดจากพอร์ตกี่ % และกองไหนดึงไม่ได้."""
    parts = [
        "ตัวเลขหุ้นรายตัวเป็น **ขอบล่าง** — ผู้ให้ข้อมูลให้แค่ top-10 ของแต่ละกอง "
        "ของจริงมากกว่านี้เสมอ (สัดส่วนเซกเตอร์ครอบทั้งกอง จึงเป็นตัวเลขเต็ม)"
    ]
    if covered < 0.999:
        parts.append(
            f"คิดจากพอร์ตเพียง {covered * 100:.1f}% — ดึงข้อมูลไม่ได้: "
            + ", ".join(f"{t} ({why})" for t, why in sorted(unavailable.items()))
        )
    return " · ".join(parts)


def overlap_pairs(result: dict[str, Any], min_weight_pct: float = 0.5) -> list[dict[str, Any]]:
    """หุ้นที่ถูกถือผ่าน **มากกว่าหนึ่งกอง** — คือความทับซ้อนที่ผู้ใช้มองไม่เห็น."""
    return [
        row
        for row in result.get("holdings", [])
        if len(row.get("via") or []) > 1 and float(row.get("weight_pct") or 0.0) >= min_weight_pct
    ]
