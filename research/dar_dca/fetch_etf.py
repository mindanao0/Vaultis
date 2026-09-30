"""Download ETF data for the confirmation phase (no analysis here — data only).

Saves /scratch/cache/etf_daily.pkl: dict with
  adj  : daily adjusted close (total return, auto_adjust=True)
  raw  : daily unadjusted close
  div  : dividends per share (event dates)
and /scratch/cache/etf_meta.pkl: expense ratio (percent, as yfinance 0.2.61 reports) + trailing yield now.
"""
from __future__ import annotations

import pandas as pd
import yfinance as yf

TICKERS = ["VOO", "SPY", "SCHD", "QQQ", "QQQM", "XLV", "GLD", "GLDM", "IAU"]


def main() -> None:
    adj = yf.download(TICKERS, start="1993-01-01", auto_adjust=True, progress=False, threads=False)["Close"]
    raw = yf.download(TICKERS, start="1993-01-01", auto_adjust=False, progress=False, threads=False)["Close"]
    divs = {}
    meta = {}
    for t in TICKERS:
        tk = yf.Ticker(t)
        d = tk.dividends
        divs[t] = d
        info = {}
        try:
            info = tk.info or {}
        except Exception as exc:  # noqa: BLE001 - research script, record and continue
            info = {"error": repr(exc)}
        meta[t] = {
            "netExpenseRatio": info.get("netExpenseRatio"),
            "dividendYield": info.get("dividendYield"),
            "yield": info.get("yield"),
            "fundInceptionDate": info.get("fundInceptionDate"),
        }
    pd.to_pickle({"adj": adj, "raw": raw, "div": divs}, "/scratch/cache/etf_daily.pkl")
    pd.to_pickle(meta, "/scratch/cache/etf_meta.pkl")
    print("adj", adj.shape, adj.index.min(), adj.index.max())
    print(adj.apply(lambda s: s.first_valid_index()).to_dict())
    for t, m in meta.items():
        print(t, m)


if __name__ == "__main__":
    main()
