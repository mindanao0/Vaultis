# -*- coding: utf-8 -*-
"""ยืดประวัติราคาของกองที่เพิ่งลิสต์ ด้วยกองพี่ที่ตามดัชนีเดียวกัน (FIX_PLAN เฟส 4①).

**ปัญหาที่แก้** พอร์ตนี้มี QQQM (ลิสต์ 2020-10) และ GLDM (2018-06) ⇒ ``dropna`` ตัด
ประวัติร่วมของทั้งพอร์ตเหลือ ~5.8 ปี **และหน้าต่างที่เหลือไม่มีวิกฤตใหญ่สักรอบ**
σ/maxDD ที่ป้อนเข้า Monte Carlo จึงมองโลกสวยกว่าความจริงอย่างเป็นระบบ —
ผู้ใช้เตรียมใจกับ drawdown ที่ตื้นกว่าที่พอร์ตแบบนี้เคยเจอจริง

**วิธี** ต่อประวัติด้วยกองพี่ที่ตามดัชนีเดียวกัน (QQQM→QQQ, GLDM→GLD) โดย
**ปรับระดับราคาให้ต่อเนื่องที่วันเชื่อม** — ต่อที่ระดับดิบจะเกิดกระโดดปลอมหนึ่งวัน
ซึ่งไหลเข้า σ และ maxDD เป็นความผันผวนที่ไม่เคยเกิดขึ้น

**ข้อจำกัดที่ต้องพูดออกมา ไม่ใช่ซ่อน** ช่วงก่อนวันลิสต์คือผลตอบแทนของ **กองพี่**
ไม่ใช่ของกองที่ถืออยู่จริง (ค่าธรรมเนียมต่างกันเล็กน้อย และ tracking ไม่เท่ากันเป๊ะ)
ผู้เรียกต้องรายงาน ``proxied`` ออกไปให้ผู้ใช้เห็นเสมอ — ตัวเลขที่ยืดมาโดยไม่บอกที่มา
คือการกุข้อมูลชนิดเดียวกับป้าย "ย้อนหลัง 10 ปี" ที่ไม่ตรงหน้าต่างจริง

ตัวเลขนี้เป็น **สถิติเชิงพรรณนาสำหรับสมมติฐาน** — ห้ามไหลเข้าเลขคะแนนหรือการจัดสรร DCA
(invariant เดียวกับ ``trend_channel.py``)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

#: กองที่ลิสต์ทีหลัง → กองพี่ที่ตามดัชนีเดียวกันและมีประวัติยาวกว่า
#: (``portfolio/ab_backtest.py`` ใช้ตารางนี้มาก่อน — ย้ายมาไว้ที่เดียวเพื่อไม่ให้มีสองชุด)
PROXY_MAP: dict[str, str] = {"QQQM": "QQQ", "GLDM": "GLD"}


def proxy_tickers_for(tickers: list[str]) -> list[str]:
    """รายชื่อกองพี่ที่ต้องดึงเพิ่มสำหรับ ``tickers`` (ไม่ซ้ำ ไม่รวมตัวที่ขออยู่แล้ว)."""
    wanted = {str(t).strip().upper() for t in tickers}
    return sorted({PROXY_MAP[t] for t in wanted if t in PROXY_MAP} - wanted)


def splice_with_proxy(
    prices: pd.DataFrame, tickers: list[str]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """ต่อประวัติของกองที่มีใน :data:`PROXY_MAP` ด้วยกองพี่ในเฟรมเดียวกัน.

    ``prices`` ต้องมีทั้งคอลัมน์ของกองจริงและของกองพี่ (ดู :func:`proxy_tickers_for`)
    ``tickers`` คือกองที่ผู้เรียก **ถืออยู่จริง** — คอลัมน์กองพี่ที่ดึงมาเพื่อยืดประวัติ
    ถูกตัดออกจากผลลัพธ์ (ส่งกลับไปจะกลายเป็นกองที่ผู้ใช้ไม่ได้ถือโผล่ในน้ำหนักพอร์ต)
    คืน ``(เฟรมที่ยืดแล้วเฉพาะคอลัมน์ของกองจริง, รายงาน)``

    รายงาน: ``{"proxied": {ticker: {"proxy", "spliced_from", "added_days"}},
    "skipped": {ticker: เหตุผล}}`` — กองที่ต่อไม่ได้ต้องบอกเหตุผล ไม่ใช่หายเงียบ

    ปรับระดับด้วยอัตราส่วนราคา ณ **วันแรกที่กองจริงมีข้อมูล** ⇒ ผลตอบแทนของวันเชื่อม
    เป็นของกองจริง และไม่มีวันกระโดดปลอมเข้าไปใน σ/maxDD
    """
    result = prices.copy()
    report: dict[str, Any] = {"proxied": {}, "skipped": {}}

    for ticker, proxy in PROXY_MAP.items():
        if ticker not in prices.columns:
            continue
        if proxy not in prices.columns:
            report["skipped"][ticker] = f"ไม่มีข้อมูลราคาของกองพี่ ({proxy}) ในชุดที่ส่งมา"
            continue
        real = pd.to_numeric(prices[ticker], errors="coerce")
        proxy_series = pd.to_numeric(prices[proxy], errors="coerce")
        real_valid = real[np.isfinite(real)]
        proxy_valid = proxy_series[np.isfinite(proxy_series)]
        if real_valid.empty or proxy_valid.empty:
            report["skipped"][ticker] = "ไม่มีราคาที่ใช้ได้ของกองจริงหรือกองพี่"
            continue

        splice_at = real_valid.index[0]
        earlier = proxy_valid[proxy_valid.index < splice_at]
        if earlier.empty:
            report["skipped"][ticker] = "กองพี่ไม่มีประวัติก่อนวันที่กองจริงเริ่มซื้อขาย"
            continue
        proxy_at_splice = proxy_valid.get(splice_at)
        if proxy_at_splice is None or not np.isfinite(proxy_at_splice) or proxy_at_splice <= 0:
            # ไม่มีราคากองพี่ ณ วันเชื่อมพอดี → ใช้ราคาก่อนหน้าที่ใกล้ที่สุด
            proxy_at_splice = float(earlier.iloc[-1])
        if not np.isfinite(proxy_at_splice) or proxy_at_splice <= 0:
            report["skipped"][ticker] = "ราคากองพี่ ณ วันเชื่อมใช้ไม่ได้"
            continue

        scale = float(real_valid.iloc[0]) / float(proxy_at_splice)
        result.loc[earlier.index, ticker] = earlier * scale
        report["proxied"][ticker] = {
            "proxy": proxy,
            "spliced_from": pd.Timestamp(earlier.index[0]).date().isoformat(),
            "spliced_at": pd.Timestamp(splice_at).date().isoformat(),
            "added_days": int(len(earlier)),
        }

    wanted = [t for t in tickers if t in result.columns]
    return result[wanted], report


def describe_proxies(report: dict[str, Any]) -> str:
    """ประโยคไทยบอกที่มาของประวัติที่ยืดมา — ผู้เรียกต้องแสดงเสมอเมื่อมีการยืด."""
    proxied = report.get("proxied") or {}
    if not proxied:
        return ""
    parts = [
        f"{ticker} ใช้ {info['proxy']} แทนช่วงก่อน {info['spliced_at']} "
        f"(+{info['added_days']:,} วันทำการ)"
        for ticker, info in sorted(proxied.items())
    ]
    return (
        "ประวัติบางส่วนมาจากกองพี่ที่ตามดัชนีเดียวกัน: "
        + " · ".join(parts)
        + " — ช่วงนั้นเป็นผลตอบแทนของกองพี่ ไม่ใช่ของกองที่ถืออยู่จริง"
    )
