# -*- coding: utf-8 -*-
"""จำลอง DRIP จากปันผลที่รับจริงใน ledger (Roadmap Phase 2 ข้อ 5).

ตอบคำถามเดียว: "ถ้านำปันผลแต่ละงวดซื้อหุ้นเพิ่มทันที ณ วันรับ วันนี้จะต่างจาก
ถือเป็นเงินสดเท่าไร" — เป็นการจำลองเชิงพรรณนาจากราคาจริงในอดีต ไม่ใช่คำแนะนำ
และไม่เข้าเลขคะแนน/จัดสรรใด ๆ
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def simulate_drip(dividends: pd.DataFrame, closes: pd.Series) -> dict[str, Any]:
    """จำลองนำปันผล (USD สุทธิ) ซื้อหุ้นเพิ่ม ณ ราคาปิดวันรับ (asof).

    ``dividends``: แถวปันผลของ ticker เดียว ต้องมีคอลัมน์ ``date`` และ ``amount_usd``
    ``closes``: ราคาปิด adjusted รายวัน (USD) ของ ticker นั้น

    คืน ``{rounds, skipped, cash_usd, extra_shares, drip_value_usd, advantage_usd}``
    งวดที่หาราคา ณ วันรับไม่ได้ = ข้าม + นับใน ``skipped`` (ไม่เดาราคา — AUDIT.md C1)
    ไม่มีข้อมูลราคาเลย → raise ValueError
    """
    closes = pd.to_numeric(closes, errors="coerce").dropna().sort_index()
    if closes.empty:
        raise ValueError("ไม่มีข้อมูลราคา ไม่สามารถจำลอง DRIP ได้")

    rounds = 0
    skipped = 0
    cash_usd = 0.0
    extra_shares = 0.0
    for _, row in dividends.iterrows():
        amount = float(pd.to_numeric(row.get("amount_usd"), errors="coerce") or 0.0)
        date = pd.to_datetime(row.get("date"), errors="coerce")
        if amount <= 0 or pd.isna(date):
            skipped += 1
            continue
        cash_usd += amount
        price_at_receipt = closes.asof(date)
        if pd.isna(price_at_receipt) or float(price_at_receipt) <= 0:
            skipped += 1
            cash_usd -= amount  # งวดนี้เทียบไม่ได้ ตัดออกทั้งสองขา
            continue
        extra_shares += amount / float(price_at_receipt)
        rounds += 1

    current_price = float(closes.iloc[-1])
    drip_value = extra_shares * current_price
    return {
        "rounds": rounds,
        "skipped": skipped,
        "cash_usd": cash_usd,
        "extra_shares": extra_shares,
        "drip_value_usd": drip_value,
        "advantage_usd": drip_value - cash_usd,
    }
