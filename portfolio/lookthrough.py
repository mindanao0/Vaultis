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


#: อัตราส่วนที่ทะลุกองแล้วรวมได้ — ``(คีย์ผลลัพธ์, ฟิลด์ของ yfinance, วิธีรวม, ป้ายไทย)``
#:
#: **วิธีรวมสองแบบนี้ไม่ใช่รสนิยม มันคือคนละสูตรที่ให้คนละคำตอบ**
#:
#: * ``harmonic`` สำหรับ price multiple (P/E, P/B, P/S): ระดับพอร์ตคือ ``ΣP / ΣE``
#:   ซึ่งเท่ากับ **ค่าเฉลี่ยฮาร์มอนิกถ่วงน้ำหนัก** ของ P/E รายตัว ไม่ใช่ค่าเฉลี่ยเลขคณิต
#:   — ผู้ให้ดัชนี (S&P, MSCI) ใช้แบบนี้ด้วยเหตุผลเดียวกัน: บริษัทที่กำไรน้อยมากมี P/E
#:   พุ่งสูงจนค่าเฉลี่ยเลขคณิตถูกลากไปทั้งพอร์ต ทั้งที่มันกินสัดส่วน "กำไร" นิดเดียว
#: * ``arithmetic`` สำหรับตัวที่เป็น "ผลตอบแทนต่อหน่วยฐาน" (ROE, ปันผล, margin):
#:   ค่าเฉลี่ยถ่วงน้ำหนักตรง ๆ คือการรวมที่ถูกอยู่แล้ว
_LOOKTHROUGH_RATIOS: list[tuple[str, str, str, str]] = [
    ("pe", "trailingPE", "harmonic", "P/E (trailing)"),
    ("forward_pe", "forwardPE", "harmonic", "P/E (forward)"),
    ("pb", "priceToBook", "harmonic", "P/B"),
    ("ps", "priceToSalesTrailing12Months", "harmonic", "P/S"),
    ("roe", "returnOnEquity", "arithmetic", "ROE"),
    ("profit_margin", "profitMargins", "arithmetic", "Profit margin"),
]

#: ฟิลด์ที่ yfinance คืนเป็นสัดส่วน (0.23) ไม่ใช่เปอร์เซ็นต์ — ต้องคูณ 100 ตอนแสดง
_RATIO_IS_FRACTION = {"returnOnEquity", "profitMargins"}


def _stock_info(symbol: str) -> dict[str, Any]:
    """``info`` ของหุ้นรายตัว — คืน ``{}`` เมื่อดึงไม่ได้ (ผู้เรียกนับเป็น "ไม่มีข้อมูล")."""
    try:
        import yfinance

        return yfinance.Ticker(str(symbol).strip().upper()).info or {}
    except Exception:  # noqa: BLE001 — ต้นทางโยนได้หลายชนิด
        return {}


#: กรอบที่ price multiple ของหุ้นจดทะเบียนเป็นไปได้จริง — นอกกรอบนี้คือ**ข้อมูลผิด**
#: ไม่ใช่หุ้นที่ถูก/แพงสุดขั้ว วัดจริง 2026-08-31: yfinance คืน ``priceToBook`` ของ
#: BRK-B มาเป็น ``0.00096532935`` (ของจริงราว 1.6 — คลาดไปพันเท่า คล้ายหน่วยผิด)
#: และค่าเดียวนี้ลากค่าเฉลี่ย**ฮาร์มอนิก**ของทั้งพอร์ตจาก ~5 เหลือ **0.072** เพราะ
#: ฮาร์มอนิกให้น้ำหนักกับค่าที่เล็กที่สุดมากที่สุด (``w/0.00096`` = 528 เท่าของน้ำหนักตัวเอง)
#: — ตัวเลขที่ออกมายังดูเป็นตัวเลขปกติ ไม่มีอะไรพัง จึงต้องมีด่านนี้
#: กรอบตั้งไว้กว้างโดยตั้งใจ: ตัดเฉพาะที่**เป็นไปไม่ได้** ไม่ใช่ที่ดู "แปลก"
#: (ธนาคารที่ P/B 0.3 หรือหุ้นเติบโตที่ P/E 300 เป็นของจริง ต้องผ่าน)
_MIN_PLAUSIBLE_MULTIPLE = 0.01
_MAX_PLAUSIBLE_MULTIPLE = 1000.0

#: เหตุผลที่หุ้นตัวหนึ่งไม่เข้าอัตราส่วน — **สองอย่างนี้คนละเรื่องกัน ห้ามยุบรวม**
#: ``no_data`` ผู้ให้ข้อมูลไม่มีค่านี้ · ``not_meaningful`` มีค่าแต่ใช้ไม่ได้
#: (บริษัทขาดทุน = P/E ติดลบ ซึ่งตำราเรียก "not meaningful" หรือค่าหลุดกรอบข้างบน)
EXCLUDED_NO_DATA = "no_data"
EXCLUDED_NOT_MEANINGFUL = "not_meaningful"


def _usable_ratio(raw: Any, method: str) -> tuple[float | None, str]:
    """``(ค่าที่รวมได้, เหตุผลที่ไม่ได้)`` — "รวมไม่ได้" ต้องไม่กลายเป็น 0.

    ``harmonic`` ตัด **ค่าที่ไม่เป็นบวก** ทิ้ง เพราะ P/E ของบริษัทที่ขาดทุนติดลบ
    ไม่มีความหมายทางการเงิน และการหารด้วยค่าติดลบจะดึงค่าเฉลี่ยฮาร์มอนิกทั้งพอร์ต
    พลิกเครื่องหมายได้ · แล้วตัดค่าที่หลุดกรอบ ``_MIN/_MAX_PLAUSIBLE_MULTIPLE`` ต่อ
    เพราะฮาร์มอนิกอ่อนไหวกับค่าเล็กมากเป็นพิเศษ

    ``arithmetic`` รับค่าติดลบได้ตามจริง (ROE ติดลบคือข้อเท็จจริงของบริษัทที่ขาดทุน
    ไม่ใช่ข้อมูลเสีย) จึงไม่ใช้กรอบเดียวกัน
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, EXCLUDED_NO_DATA
    value = float(raw)
    if value != value:  # NaN
        return None, EXCLUDED_NO_DATA
    if method == "harmonic":
        if value <= 0:
            return None, EXCLUDED_NOT_MEANINGFUL
        if not (_MIN_PLAUSIBLE_MULTIPLE <= value <= _MAX_PLAUSIBLE_MULTIPLE):
            return None, EXCLUDED_NOT_MEANINGFUL
    return value, ""


def weighted_ratios(result: dict[str, Any]) -> dict[str, Any]:
    """อัตราส่วนพื้นฐานของพอร์ต คำนวณจาก**หุ้นที่ทะลุกองเจอ** ไม่ใช่จากตัวกอง ETF.

    ตัวเลขอย่าง P/E หรือ ROE ของ ticker ``VOO`` เองไม่มีความหมาย — กองไม่ได้ทำธุรกิจ
    มันถือหุ้นหลายร้อยตัว วิธีเดียวที่ได้ตัวเลขที่แปลได้คือถ่วงน้ำหนักจากของข้างใน

    ``result``: ผลลัพธ์ของ :func:`look_through`

    **ทุกตัวเลขที่นี่เป็นสถิติเชิงพรรณนา ห้ามไหลเข้าเลขคะแนนหรือการจัดสรร DCA**

    สามข้อจำกัดที่ติดมากับตัวเลขและ**ต้องขึ้นจอพร้อมกันเสมอ**:

    1. ฐานเป็น ``top-10`` ของแต่ละกองเท่านั้น (ข้อจำกัดของ :func:`look_through`) —
       ``coverage_pct`` บอกว่าน้ำหนักที่เอามาคิดรวมกันได้กี่ % ของพอร์ต ซึ่งจะน้อยมาก
       (ราว 30–40%) และตัวเลขนี้คือ **ค่าเฉลี่ยของหุ้นใหญ่ที่สุด** ไม่ใช่ของทั้งพอร์ต
    2. หุ้นที่ดึงอัตราส่วนไม่ได้/ค่าไม่มีความหมาย ถูก **ตัดออกจากตัวหารของอัตราส่วนนั้น**
       และรายงานจำนวนใน ``missing`` — ไม่ใช่นับเป็น 0 ซึ่งจะลากค่าเฉลี่ยลงทั้งพอร์ต
    3. แต่ละอัตราส่วนมีตัวหารของตัวเอง (``weight_pct`` ต่างกันได้) เพราะหุ้นคนละชุด
       มีข้อมูลคนละอย่าง — เทียบ P/E กับ ROE ข้ามแถวจึงต้องดู ``weight_pct`` ประกอบ

    ไม่มีหุ้นให้คิดเลย → ``ValueError`` (ไม่ใช่คืนตารางว่างที่อ่านเหมือน "พอร์ตไม่มีมูลค่า")
    """
    holdings = list(result.get("holdings") or [])
    if not holdings:
        raise ValueError("ยังไม่มีรายชื่อหุ้นจากการทะลุกอง — คิดอัตราส่วนไม่ได้")

    infos = {row["symbol"]: _stock_info(row["symbol"]) for row in holdings}
    total_weight = sum(float(row.get("weight_pct") or 0.0) for row in holdings)

    ratios: dict[str, Any] = {}
    for key, field, method, label in _LOOKTHROUGH_RATIOS:
        used_weight = 0.0
        accumulator = 0.0
        excluded: dict[str, list[str]] = {EXCLUDED_NO_DATA: [], EXCLUDED_NOT_MEANINGFUL: []}
        for row in holdings:
            symbol = row["symbol"]
            weight = float(row.get("weight_pct") or 0.0)
            if weight <= 0:
                continue
            value, reason = _usable_ratio(infos.get(symbol, {}).get(field), method)
            if value is None:
                excluded[reason].append(symbol)
                continue
            used_weight += weight
            accumulator += weight / value if method == "harmonic" else weight * value

        base = {
            "label": label,
            "method": method,
            "missing": sorted(excluded[EXCLUDED_NO_DATA]),
            "not_meaningful": sorted(excluded[EXCLUDED_NOT_MEANINGFUL]),
        }
        if used_weight <= 0 or accumulator <= 0:
            # "คำนวณไม่ได้" ต้องเป็น None พร้อมรายชื่อที่ขาด ไม่ใช่ 0.0 ที่อ่านเป็นตัวเลขจริง
            ratios[key] = {**base, "value": None, "weight_pct": 0.0}
            continue

        value = used_weight / accumulator if method == "harmonic" else accumulator / used_weight
        if field in _RATIO_IS_FRACTION:
            value *= 100.0
        ratios[key] = {**base, "value": round(value, 3), "weight_pct": round(used_weight, 3)}

    return {
        "ratios": ratios,
        "coverage_pct": round(total_weight, 3),
        "stocks": len(holdings),
        "notes": describe_ratio_limits(total_weight, result),
    }


def describe_ratio_limits(coverage_pct: float, result: dict[str, Any]) -> str:
    """ประโยคไทยที่ต้องขึ้นจอคู่กับตัวเลขอัตราส่วนเสมอ."""
    parts = [
        f"คิดจากหุ้นที่ทะลุกองเจอ รวมน้ำหนัก {coverage_pct:.1f}% ของพอร์ต "
        "— เป็น**ค่าเฉลี่ยของหุ้นใหญ่ที่สุดในแต่ละกอง (top-10)** ไม่ใช่ของทั้งพอร์ต",
        "P/E · P/B · P/S ใช้ค่าเฉลี่ย**ฮาร์มอนิก**ถ่วงน้ำหนัก (= ΣP/ΣE ระดับพอร์ต) "
        "ส่วน ROE · margin ใช้ค่าเฉลี่ยเลขคณิตถ่วงน้ำหนัก",
        "หุ้นที่ **ไม่มีข้อมูล** และหุ้นที่ **มีค่าแต่ใช้ไม่ได้** (P/E ของบริษัทที่ขาดทุน "
        "หรือค่าที่ผู้ให้ข้อมูลส่งมาผิดจนเป็นไปไม่ได้) ถูกตัดออกจากตัวหารของอัตราส่วนนั้น "
        "และรายงานแยกกันไว้ ไม่ใช่นับเป็น 0",
    ]
    if result.get("unavailable"):
        parts.append(
            "และยังมีกองที่ดึงโครงสร้างไม่ได้: "
            + ", ".join(sorted(result["unavailable"]))
        )
    return " · ".join(parts)


def overlap_pairs(result: dict[str, Any], min_weight_pct: float = 0.5) -> list[dict[str, Any]]:
    """หุ้นที่ถูกถือผ่าน **มากกว่าหนึ่งกอง** — คือความทับซ้อนที่ผู้ใช้มองไม่เห็น."""
    return [
        row
        for row in result.get("holdings", [])
        if len(row.get("via") or []) > 1 and float(row.get("weight_pct") or 0.0) >= min_weight_pct
    ]
