# -*- coding: utf-8 -*-
"""ข้อมูล "สำรวจ" สำหรับออกแบบกติกาเลือกกอง (research/dar_select/PLAN.md หัวข้อ 6) — ตลาดรายประเทศรายเดือน (USD, รวมปันผล)

Ken French F-F_International_Countries.zip: บล็อกแรกของแต่ละไฟล์ = "Value-Weight Dollar Returns" คอลัมน์ Mkt
ข้อมูลชุดนี้ถูกเปิดดูไปแล้ว (ใช้ยืนยันสูตร DAR รอบ 2) จึงใช้ได้เฉพาะ **ออกแบบ** ไม่ใช่ยืนยัน
"""
from __future__ import annotations

import os
import re
import zipfile

import pandas as pd

ZIP = os.environ.get("RESEARCH_COUNTRIES_ZIP", "/scratch/data/F-F_International_Countries.zip")


def country_mkt(zf: zipfile.ZipFile, name: str) -> pd.Series:
    lines = zf.read(name).decode("latin-1").splitlines()
    h = next(i for i, l in enumerate(lines) if l.strip().startswith("Mkt"))
    rows = []
    for l in lines[h + 1:]:
        parts = l.split()
        if not parts or not re.fullmatch(r"\d{6}", parts[0]):
            break
        rows.append((parts[0], float(parts[1])))
    idx = pd.PeriodIndex([f"{d[:4]}-{d[4:]}" for d, _ in rows], freq="M")
    s = pd.Series([v for _, v in rows], index=idx)
    return (s.mask(s <= -99.99) / 100.0).rename(name.replace(".Dat", ""))


def load_countries() -> pd.DataFrame:
    with zipfile.ZipFile(ZIP) as zf:
        return pd.concat([country_mkt(zf, n) for n in zf.namelist() if n.lower().endswith(".dat")], axis=1)


if __name__ == "__main__":
    df = load_countries()
    print(df.shape, df.index.min(), df.index.max())
    print(df.apply(lambda s: f"{s.first_valid_index()}..{s.last_valid_index()} n={int(s.notna().sum())}").to_string())
