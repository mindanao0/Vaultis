# -*- coding: utf-8 -*-
"""ดึงราคา total return รายวัน (ทีละกอง ผ่าน fetch_total_return_history) เก็บเป็น pickle สำหรับ simulation."""
import sys
import pandas as pd
from analysis.dar_dca import fetch_total_return_history

# กองจริง 5 กอง + กองพี่ที่ใช้ยืดประวัติ (SPY↔VOO, QQQ↔QQQM, GLD↔GLDM, DVY↔SCHD = proxy ของงานนี้เท่านั้น)
TICKERS = ["VOO", "SPY", "SCHD", "DVY", "QQQM", "QQQ", "XLV", "GLDM", "GLD"]
out = sys.argv[1]
df = fetch_total_return_history(TICKERS, years=40)
df.to_pickle(out)
print(df.apply(lambda s: (s.first_valid_index().date(), s.last_valid_index().date(), int(s.notna().sum()))).T)
