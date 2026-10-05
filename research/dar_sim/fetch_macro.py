# -*- coding: utf-8 -*-
"""USDTHB รายวัน (yfinance THB=X) + CPI สหรัฐ/ไทย รายเดือนจาก FRED ถ้ามี key — ไม่มี key = บอกตรง ๆ ไม่เดา."""
import os, sys
import pandas as pd
import yfinance as yf

out = sys.argv[1]
fx = yf.Ticker("THB=X").history(start="1996-01-01", auto_adjust=False, actions=False)["Close"].dropna()
fx.index = pd.DatetimeIndex(fx.index).tz_localize(None).normalize()
print("USDTHB", fx.index[0].date(), fx.index[-1].date(), len(fx), "last", round(float(fx.iloc[-1]), 3))
res = {"usdthb": fx}
key = os.getenv("FRED_API_KEY")
if key:
    import requests
    for name, sid in {"cpi_us": "CPIAUCSL", "cpi_th": "THACPIALLMINMEI"}.items():
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": sid, "api_key": key, "file_type": "json"}, timeout=30)
        if r.ok:
            d = r.json()["observations"]
            s = pd.Series({pd.Timestamp(o["date"]): float(o["value"]) for o in d if o["value"] != "."})
            res[name] = s
            print(name, s.index[0].date(), s.index[-1].date(), len(s))
        else:
            print(name, "FRED error", r.status_code)
else:
    print("ไม่มี FRED_API_KEY ในสภาพแวดล้อม → ไม่มี CPI จริง")
pd.to_pickle(res, out)
