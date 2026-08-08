from __future__ import annotations

import pandas as pd

from alerts.price_alert import get_current_prices
from analysis.correlation import calculate_correlation_matrix
from analysis.returns import RETURNS_HISTORY_YEARS, calculate_period_returns, real_bars
from analysis.risk import calculate_risk_metrics
from analysis.ta_compat import ta
from data.fetcher import fetch_adjusted_close_data
from technical import signal_rules
from utils.config import get_tickers

from .cache_service import PRICE_HISTORY_TTL, shared_cache
from .json_safe import frame_to_dict

_LATEST_PRICE_TTL = 5 * 60  # 5 นาที — ราคาล่าสุดไม่ต้องสดวินาทีต่อวินาที

# หน้าต่างผลตอบแทนที่ยาวสุดคือ 10Y = 2,520 แถวเทรด แต่ข้อมูล 10 ปีให้มาแค่ ~2,510 แถว
# → เงื่อนไข ``len(price_df) <= window`` เป็นจริงเสมอ แถว 10Y จึงเป็น NaN ทั้งแถว
# ตั้งแต่เขียนมา (AUDIT.md M16) ต้องดึงยาวกว่าหน้าต่างจริงถึงจะคำนวณได้
#
# ค่านี้เคยเป็นเลข ``11`` ส่วนตัวของไฟล์นี้ ขณะที่หน้าจอกับ PDF ยังขอ 10 ปี ⇒ endpoint
# ตอบแถว 10Y ได้ แต่อีกสองทางเป็น N/A มาตลอด (FIX_PLAN ข้อ 2.8) — ตอนนี้อ่านนิยามเดียว
# จาก ``analysis/returns.py`` ซึ่งคิดจาก ``RETURN_WINDOWS`` เอง
_RETURNS_HISTORY_YEARS = RETURNS_HISTORY_YEARS


def _prices_df() -> pd.DataFrame:
    """ราคา 10 ปีของทุก ticker — cache 1 ชม. (AUDIT.md H3).

    เดิมทุก request ของ /api/etf/* ดึงใหม่หมด → โดน rate limit → ข้อมูลพัง → สัญญาณปลอม

    **ห้ามใส่ ``.ffill()`` ตรงนี้** (AUDIT_2026-08-06 H10) — DataFrame ใช้ index ร่วมกัน
    ทุก ticker ตัวที่ผู้ให้ข้อมูลยังไม่ส่งแท่งของวันนั้นจะเป็น NaN แล้ว ffill เติมราคา
    เมื่อวานลงไป ⇒ ``iloc[-1] == iloc[-2]`` เป๊ะ → snapshot รายงาน "+0.00%" พร้อม
    วันที่ของ ticker อื่น และ MA50/MA200 ถูกคำนวณบนแท่งที่ไม่มีอยู่จริง
    ผู้เรียกที่ *ต้องการ* ให้ช่องว่างถูกเติม (risk/correlation) ต้อง ffill เองที่จุดนั้น
    """
    tickers = get_tickers()
    key = "prices_10y:" + ",".join(sorted(tickers))
    return shared_cache.get_or_compute(
        key,
        PRICE_HISTORY_TTL,
        lambda: fetch_adjusted_close_data(tickers=tickers, years=10),
        expect_keys=tickers,
    )


def _prices_df_for_returns() -> pd.DataFrame:
    """ราคายาวพอสำหรับหน้าต่าง 10Y — แยก cache จาก ``_prices_df``.

    จงใจไม่ขยายช่วงของ ``_prices_df`` เพราะ risk/correlation อ่านจากตัวนั้น
    การขยายจะทำให้ตัวเลขความเสี่ยงเปลี่ยนเงียบ ๆ ทั้งที่ไม่มีใครขอให้เปลี่ยน

    ไม่ ffill ที่นี่เช่นกัน — ``calculate_period_returns`` คิดจาก**แท่งจริงรายคอลัมน์**
    (``real_bars``) เอง การเติมช่องว่างก่อนส่งเข้าไปจะทำให้ ticker ที่ผู้ให้ข้อมูล
    หยุดส่งแท่งได้ผลตอบแทน 0.00% แทนที่จะเป็นตัวเลขของแท่งจริง/``null`` (G7)
    """
    tickers = get_tickers()
    key = f"prices_{_RETURNS_HISTORY_YEARS}y:" + ",".join(sorted(tickers))
    return shared_cache.get_or_compute(
        key,
        PRICE_HISTORY_TTL,
        lambda: fetch_adjusted_close_data(tickers=tickers, years=_RETURNS_HISTORY_YEARS),
        expect_keys=tickers,
    )


def get_etf_prices() -> dict[str, float]:
    tickers = get_tickers()
    key = "latest_prices:" + ",".join(sorted(tickers))
    return shared_cache.get_or_compute(
        key, _LATEST_PRICE_TTL, lambda: get_current_prices(tickers), expect_keys=tickers
    )


def _real_bars(prices: pd.DataFrame, ticker: str) -> pd.Series:
    """แท่งที่ผู้ให้ข้อมูลส่งมาจริงของ ticker เดียว — ช่องว่างถูกตัดทิ้ง **ไม่เติม**.

    นิยามอยู่ที่ ``analysis.returns.real_bars`` ที่เดียวทั้งระบบ (G7) — ตาราง Returns,
    snapshot, สัญญาณเทคนิค และรายงานรายสัปดาห์ต้องนับ "แท่งจริง" แบบเดียวกัน
    (รวมถึงการตัด ``inf``/``-inf`` ที่ไม่ใช่ราคา และทำให้ ``JSONResponse`` ล้มทั้ง endpoint)
    """
    return real_bars(prices[ticker])


def get_etf_daily_eod_snapshot() -> dict[str, dict[str, float | str | bool | None]]:
    """แท่งปิดล่าสุดเทียบแท่งก่อนหน้า — **รายคอลัมน์** จากข้อมูลดิบ (EOD).

    คิดจากแท่งจริงของแต่ละ ticker เอง (AUDIT_2026-08-06 H10) ไม่ใช่แถวสุดท้ายของ
    ทั้งเฟรม เพราะ ticker ที่ผู้ให้ข้อมูลยังไม่ส่งแท่งของวันนั้นจะยืมวันที่และ
    "ราคาไม่เปลี่ยน" ของเพื่อนไปทั้งดุ้น

    สัญญาของแต่ละช่อง — "ดึงไม่สำเร็จ" ≠ "ไม่มีข้อมูล" ≠ "ราคาไม่ขยับ":

    - ``stale: True`` + ``data_ok: False`` + ``reason`` เมื่อแท่งล่าสุดของ ticker นั้น
      ตามหลังแท่งล่าสุดของทั้งเฟรม (ราคายังจริง แต่เป็นของวันเก่า)
    - ``change_pct: None`` เมื่อมีแท่งจริงไม่ถึง 2 แท่ง หรือราคาอ้างอิง ``<= 0``
      — **ห้ามเป็น 0.0** เพราะ 0.00% อ่านเป็น "วันนี้ราคาไม่เปลี่ยน" ได้
    - ticker ที่ไม่มีแท่งจริงเลย → ``{"error": ..., "data_ok": False}`` รายตัว
      ไม่ลาก endpoint ทั้งตัวลงไปเป็น 500 (M-ETF-1)
    """
    prices = _prices_df()
    if prices.empty:
        return {}
    frame_last = pd.Timestamp(prices.index[-1])
    out: dict[str, dict[str, float | str | bool | None]] = {}
    for ticker in prices.columns:
        key = str(ticker).strip().upper()
        bars = _real_bars(prices, ticker)
        if bars.empty:
            out[key] = {
                "price": None,
                "previous_close": None,
                "change_pct": None,
                "date": None,
                "stale": True,
                "data_ok": False,
                "error": "ดึงราคาไม่สำเร็จ — ไม่มีแท่งราคาของ ticker นี้เลย",
            }
            continue

        bar_ts = pd.Timestamp(bars.index[-1])
        stale = bool(bar_ts < frame_last)
        p_t = float(bars.iloc[-1])

        prev: float | None = None
        chg: float | None = None
        if len(bars) >= 2:
            p_y = float(bars.iloc[-2])
            prev = round(p_y, 2)
            if p_y > 0:
                chg = round((p_t - p_y) / p_y * 100.0, 4)

        row: dict[str, float | str | bool | None] = {
            "price": round(p_t, 2),
            "previous_close": prev,
            "change_pct": chg,
            "date": bar_ts.strftime("%d/%m/%Y"),
            "stale": stale,
            "data_ok": not stale,
        }
        if stale:
            row["reason"] = (
                f"แท่งราคาล่าสุดของ {key} คือ {bar_ts.strftime('%d/%m/%Y')} "
                f"ตามหลังวันล่าสุดของชุดข้อมูล ({frame_last.strftime('%d/%m/%Y')})"
            )
        out[key] = row
    return out


def get_etf_returns() -> dict:
    """ผลตอบแทนตามช่วงเวลา — ช่องที่ข้อมูลไม่พอเป็น ``null`` (ไม่ใช่ 0 และไม่ทำทั้ง endpoint พัง).

    ETF ที่เกิดทีหลัง (QQQM ต.ค. 2020, GLDM 2018) จะได้ ``null`` ในช่วงยาว ๆ
    ตามจริง — นั่นคือคำตอบที่ถูก ไม่ใช่ข้อผิดพลาด

    ticker ที่ผู้ให้ข้อมูล**หยุดส่งแท่ง**ก็เข้ากติกาเดียวกัน (G7): ตัวเลขที่ได้คือ
    ผลตอบแทนของแท่งจริงของ ticker นั้นเอง และเป็น ``null`` เมื่อแท่งจริงไม่ถึงหน้าต่าง
    — ห้ามเป็น ``0.0`` ซึ่งอ่านได้ว่า "ราคาไม่ขยับเลยทั้งช่วง"
    """
    result = calculate_period_returns(_prices_df_for_returns())
    return frame_to_dict(result)


def get_etf_risk() -> dict:
    """``ffill`` ที่จุดใช้งาน — ที่นี่คือจุดที่ "จำเป็น" จริง (AUDIT_2026-08-06 H10).

    ความเสี่ยงคิดจากผลตอบแทนรายวัน วันที่ ETF ตัวหนึ่งไม่มีแท่งจะทำให้ ``pct_change``
    ของทั้งช่วงขาดหาย การเติมช่องว่างที่นี่ให้ค่าเท่าเดิมทุกหลักกับตอนที่ ``_prices_df``
    ยัง ffill ให้ — ตั้งใจไม่ให้ตัวเลขความเสี่ยงเปลี่ยนพร้อมกับการแก้บั๊ก snapshot
    """
    prices = _prices_df().ffill()
    result = calculate_risk_metrics(prices)
    return frame_to_dict(result)


def get_etf_correlation() -> dict:
    prices = _prices_df().ffill()  # เหตุผลเดียวกับ get_etf_risk
    result = calculate_correlation_matrix(prices)
    return frame_to_dict(result)


def get_etf_technical() -> dict[str, dict[str, float | str | bool]]:
    """สัญญาณเทคนิครายตัว — ใช้นิยามกลางจาก technical/signal_rules.py (AUDIT.md C2).

    ticker ที่ข้อมูลไม่พอจะมี ``data_ok: False`` และ ``signal: "no_data"``
    ไม่ถูกซ่อนหายไปเฉย ๆ เหมือนเดิม (AUDIT.md C1)

    ตัวชี้วัดคิดจาก**แท่งจริง**เท่านั้น (AUDIT_2026-08-06 H10) — เดิม ``_prices_df``
    ffill มาก่อน แท่งที่ไม่มีอยู่จริงจึงเข้าไปถ่วง MA50/MA200 แล้วป้อนต่อให้
    ``signal_rules.dca_signal`` โดยติดป้าย ``data_ok: True``
    ticker ที่แท่งล่าสุดตามหลังทั้งเฟรมจะมี ``stale: True`` + ``as_of`` กำกับ —
    ตัวเลขยังจริงแต่เป็นของวันเก่า ผู้ใช้ต้องเห็น ไม่ใช่ถูกกลืน
    """
    prices = _prices_df()
    frame_last = pd.Timestamp(prices.index[-1]) if not prices.empty else None
    signals: dict[str, dict[str, float | str | bool]] = {}
    for ticker in prices.columns:
        s = _real_bars(prices, ticker)
        if len(s) < 200:
            signals[ticker] = {
                "data_ok": False,
                "signal": signal_rules.NO_DATA,
                "reason": "ข้อมูลราคาน้อยกว่า 200 วันเทรด",
            }
            continue
        bar_ts = pd.Timestamp(s.index[-1])
        stale = bool(frame_last is not None and bar_ts < frame_last)
        price = float(s.iloc[-1])
        ma50 = float(ta.sma(s, length=50).iloc[-1])
        ma200 = float(ta.sma(s, length=200).iloc[-1])
        rsi = float(ta.rsi(s, length=14).iloc[-1])
        central = signal_rules.dca_signal(price, ma50, ma200, rsi)
        if central == signal_rules.NO_DATA:
            signals[ticker] = {
                "data_ok": False,
                "signal": signal_rules.NO_DATA,
                "reason": "คำนวณตัวชี้วัดไม่ได้",
                "as_of": bar_ts.strftime("%d/%m/%Y"),
                "stale": stale,
            }
            continue
        signals[ticker] = {
            "data_ok": True,
            "price": price,
            "ma50": ma50,
            "ma200": ma200,
            "rsi14": rsi,
            "ma50_state": "Above" if price >= ma50 else "Below",
            "ma200_state": "Above" if price >= ma200 else "Below",
            "signal": central,
            "signal_th": signal_rules.thai_description(central),
            "as_of": bar_ts.strftime("%d/%m/%Y"),
            "stale": stale,
        }
        if stale:
            signals[ticker]["reason"] = (
                f"ตัวเลขคิดจากแท่งราคาวันที่ {bar_ts.strftime('%d/%m/%Y')} "
                f"ซึ่งตามหลังวันล่าสุดของชุดข้อมูล ({frame_last.strftime('%d/%m/%Y')})"
            )
    return signals
