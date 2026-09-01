# -*- coding: utf-8 -*-
"""โมดูลจำลองการลงทุนแบบ DCA รายเดือน."""

from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd
import plotly.graph_objects as go
import yfinance as yf

from portfolio.weight_rules import validate_weights

logger = logging.getLogger(__name__)

#: คีย์ใน ``DataFrame.attrs`` ที่พกรายงานช่วงเวลาที่จำลองได้จริงติดไปกับผลลัพธ์
COVERAGE_ATTR = "coverage"

#: คีย์ใน ``coverage`` — กองที่ผู้ใช้ตั้งน้ำหนัก **0** จึงไม่ถูกจำลอง (เจตนา ไม่ใช่ความล้มเหลว)
ZERO_WEIGHT_KEY = "excluded_zero_weight"
#: คีย์ใน ``coverage`` — กองที่ **ถือน้ำหนักอยู่** แต่ไม่มีคอลัมน์ราคาในชุดข้อมูลที่ส่งมา
NO_PRICE_KEY = "excluded_no_price"
#
# ทำไมต้องมีสองคีย์นี้ (AUDIT_ROUND2_2026-08-07 · T6): การจำลองคัดกองออกจากพอร์ตได้
# สองทางโดยไม่มีใครรู้ — ``valid_assets`` ตัดกองที่ไม่มีคอลัมน์ราคา แล้ว
# ``_normalize_weights()`` ก็ปั้นน้ำหนักที่เหลือให้รวมเป็น 1.0 ⇒ ตัวเลขที่ตอบกลับไป
# เป็นผลของ **พอร์ตอื่น** แต่ถูกนำเสนอเป็นคำตอบของพอร์ตที่ผู้ใช้กรอก
#
# "ตั้งน้ำหนัก 0" กับ "ไม่มีราคา" เป็นคนละเรื่องกันสำหรับผู้ใช้ จึงแยกเป็นคนละคีย์:
#   * ``ZERO_WEIGHT_KEY`` = เจตนา ตัดออกได้ แค่บอกให้รู้ว่าไม่ได้ลืม
#   * ``NO_PRICE_KEY``    = ข้อมูลขาด ผลลัพธ์ที่ได้ไม่ใช่พอร์ตที่ขอมา ต้องเตือนให้ชัด
# ทั้งคู่ออกหน้าจอผ่าน ``describe_coverage()`` ตัวเดียว (นิยามข้อความมีที่เดียว)


def _fmt_month(stamp: pd.Timestamp) -> str:
    return pd.Timestamp(stamp).strftime("%Y-%m")


def _limited_by_phrase(limited_by: Dict[str, str | None]) -> str:
    """วลีบอกว่ากองไหนเป็นตัวบีบช่วงเวลา และมีราคาแรกวันไหน."""
    if not limited_by:
        return ""
    items = [
        f"{ticker} เพิ่งมีราคาแรก {first_date}" if first_date else f"{ticker} ไม่มีราคาเลย"
        for ticker, first_date in limited_by.items()
    ]
    return "เพราะ " + ", ".join(items)


def _coverage_report(
    prices: pd.DataFrame, monthly_all: pd.DataFrame, monthly: pd.DataFrame
) -> Dict[str, Any]:
    """สรุปว่าจำลองได้กี่เดือนจากข้อมูลกี่เดือน และกองไหนเป็นตัวตัดช่วง."""
    dropped_index = monthly_all.index.difference(monthly.index)

    limited_by: Dict[str, str | None] = {}
    for ticker in monthly_all.columns:
        if len(dropped_index) == 0:
            break
        if not bool(monthly_all.loc[dropped_index, ticker].isna().any()):
            continue
        first_valid = prices[ticker].first_valid_index()
        limited_by[str(ticker)] = (
            None if first_valid is None else pd.Timestamp(first_valid).date().isoformat()
        )

    # กองที่มีราคาช้าที่สุด (หรือไม่มีเลย) คือตัวที่บีบช่วงมากที่สุด — เอาขึ้นก่อน
    ordered = dict(sorted(limited_by.items(), key=lambda kv: kv[1] or "9999-12-31", reverse=True))

    return {
        "available_from": _fmt_month(monthly_all.index[0]) if len(monthly_all) else None,
        "available_to": _fmt_month(monthly_all.index[-1]) if len(monthly_all) else None,
        "months_available": int(len(monthly_all)),
        "simulated_from": _fmt_month(monthly.index[0]) if len(monthly) else None,
        "simulated_to": _fmt_month(monthly.index[-1]) if len(monthly) else None,
        "months_simulated": int(len(monthly)),
        "months_dropped": int(len(monthly_all) - len(monthly)),
        "limited_by": ordered,
    }


def _monthly_first_prices(prices: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """ราคาต้นเดือนของทุกกอง + รายงานว่าตัดเดือนไหนทิ้งไปบ้าง (AUDIT_2026-08-06 B8).

    การจำลองพอร์ตตามน้ำหนักคงที่ต้องมีราคาครบทุกกองในเดือนนั้น ``dropna(how="any")``
    จึงตัด**ทั้งเดือน**ถ้ามีกองใดยังไม่เกิด — ETF ที่เพิ่งเข้าตลาดตัดช่วงต้นของการ
    จำลองทิ้ง ทำให้ ``Total Invested`` ต่ำกว่าการซื้อครบทุกเดือนมาก การตัดยังทำ
    เหมือนเดิม แต่ **ต้องรายงานออกไปให้ผู้ใช้เห็น** ห้ามตัดเงียบ
    (``portfolio/backtest.py::run_portfolio_backtest`` ตัดแบบเดียวกันและอธิบายไว้แล้ว)
    """
    monthly_all = prices.resample("MS").first()
    monthly = monthly_all.dropna(how="any")
    coverage = _coverage_report(prices, monthly_all, monthly)

    if monthly.empty:
        reason = _limited_by_phrase(coverage["limited_by"])
        raise ValueError(
            "ไม่มีเดือนไหนที่ทุกกองมีราคาพร้อมกัน จึงจำลอง DCA ไม่ได้"
            + (f" ({reason})" if reason else "")
        )
    return monthly, coverage


def describe_coverage(coverage: Dict[str, Any] | None) -> str | None:
    """คำเตือนภาษาไทยหนึ่งบรรทัดสำหรับ "สิ่งที่ถูกตัดออกจากการจำลอง" — ``None`` เมื่อไม่มีอะไรหาย.

    นิยามข้อความมีที่เดียว หน้าจอกับ API อ่านจากฟังก์ชันนี้ตัวเดียวกัน ครอบสามเรื่อง
    ที่ต้อง **แยกกันให้ผู้ใช้อ่านออก** (AUDIT_ROUND2_2026-08-07 T6):

    1. เดือนที่ถูกตัดเพราะบางกองยังไม่มีราคา (B8 — ของเดิม)
    2. กองที่ถือน้ำหนักอยู่แต่ไม่มีคอลัมน์ราคา ⇒ ผลลัพธ์เป็นพอร์ตที่ normalize ใหม่
       บนกองที่เหลือ **ไม่ใช่พอร์ตที่กรอกมา** — ร้ายแรงสุด จึงขึ้นก่อน
    3. กองที่ตั้งน้ำหนักไว้ 0 ⇒ เจตนาของผู้ใช้ ไม่ใช่ความล้มเหลว แต่ต้องบอกว่าไม่ได้ลืม
    """
    if not coverage:
        return None

    parts: list[str] = []

    no_price = [str(t) for t in (coverage.get(NO_PRICE_KEY) or [])]
    if no_price:
        parts.append(
            "ไม่มีข้อมูลราคาของ " + ", ".join(no_price) + " ทั้งที่ถือน้ำหนักอยู่ "
            "— ตัวเลขข้างบนคือผลของพอร์ตที่เหลือหลังปรับน้ำหนักใหม่ให้รวมเป็น 100% "
            "ไม่ใช่พอร์ตตามสัดส่วนที่กรอกมา"
        )

    if coverage.get("months_dropped"):
        dropped = [
            f"จำลองได้ตั้งแต่ {coverage.get('simulated_from')} ถึง {coverage.get('simulated_to')} "
            f"เท่านั้น ({coverage.get('months_simulated')} เดือน) — "
            f"ตัดไป {int(coverage['months_dropped'])} เดือนจากช่วงข้อมูลที่เริ่ม "
            f"{coverage.get('available_from')}"
        ]
        reason = _limited_by_phrase(coverage.get("limited_by") or {})
        if reason:
            dropped.append(reason)
        dropped.append("ตัวเลข Total Invested จึงต่ำกว่าการซื้อครบทุกเดือน")
        parts.extend(dropped)

    zero_weight = [str(t) for t in (coverage.get(ZERO_WEIGHT_KEY) or [])]
    if zero_weight:
        parts.append(
            "ไม่ได้จำลอง " + ", ".join(zero_weight) + " เพราะตั้งน้ำหนักไว้ 0 "
            "(ตั้งใจไม่ถือ ไม่ใช่ข้อมูลขาด)"
        )

    return " · ".join(parts) if parts else None


def _split_by_weight(weights: Dict[str, float]) -> tuple[Dict[str, float], list[str]]:
    """แยก "กองที่ถือจริง" ออกจาก "กองที่ตั้งน้ำหนัก 0" แล้วคืนชื่อกลุ่มหลังออกมาด้วย.

    น้ำหนัก 0 = เจตนาของผู้ใช้ ตัดออกจากการจำลองได้ (และ **ควร** ตัด ไม่งั้น
    ``dropna(how="any")`` ใน :func:`_monthly_first_prices` จะยอมตัดทั้งเดือนทิ้ง
    เพราะกองที่ไม่ได้ซื้อสักบาทยังไม่มีราคา) แต่ห้ามตัดเงียบ — รายชื่อที่คืนไปลง
    ``coverage[ZERO_WEIGHT_KEY]`` เพื่อให้หน้าจอบอกได้ว่า "ไม่ได้ลืม แต่ตั้งไว้ 0"
    (AUDIT_ROUND2_2026-08-07 T6)
    """
    held = {t: w for t, w in weights.items() if w > 0}
    zero_weight = [t for t in weights if t not in held]
    return held, zero_weight


def _with_exclusions(
    coverage: Dict[str, Any], zero_weight: list[str], no_price: list[str]
) -> Dict[str, Any]:
    """แนบ "ใครไม่ได้อยู่ในการจำลองและเพราะอะไร" เข้ากับรายงาน coverage.

    ตั้งคีย์ **เสมอ** แม้เป็นลิสต์ว่าง เพื่อให้ปลายทางแยก "ไม่มีใครถูกตัด" ออกจาก
    "ผลลัพธ์เก่าที่ยังไม่มีฟิลด์นี้" ได้ (คีย์หาย ≠ ลิสต์ว่าง)
    """
    coverage[ZERO_WEIGHT_KEY] = list(zero_weight)
    coverage[NO_PRICE_KEY] = list(no_price)
    return coverage


def _normalize_weights(weights: dict[str, float]) -> pd.Series:
    """ตรวจสอบและ normalize weights ให้รวมเป็น 1.

    ด่านตรวจอยู่ที่ ``portfolio/weight_rules.validate_weights()`` ที่เดียว (นิยามเดียวกับ
    ``portfolio/backtest.py``) — เดิมด่านที่นี่เขียนเป็น ``(weight_series < 0).any()``
    ซึ่ง ``inf`` และ ``NaN`` ผ่านทั้งคู่: ``inf`` กลายเป็น ``NaN`` ตอนหารด้วยผลรวม ส่วน
    ``NaN`` ถูก ``Series.sum(skipna=True)`` ข้ามไปเงียบ ๆ แล้วรอดออกไปเป็นน้ำหนักจริง
    ปลายทางคือ ``(shares * row).sum()`` ที่ข้าม ``NaN`` อีกชั้น ⇒ มูลค่าพอร์ต 0.0
    ทุกเดือน = รายงาน "ขาดทุน 100%" ที่ระบบไม่เคยคำนวณ (AUDIT_ROUND2_2026-08-07 G8)
    """
    validated = validate_weights(weights)  # inf/NaN/ติดลบ/รวมกันไม่ได้ ตายที่นี่ พร้อมชื่อกอง

    weight_series = pd.Series(validated, dtype=float)
    total_weight = float(weight_series.sum())

    # ตาข่ายชั้นสอง เผื่อกติกาที่ weight_rules ถูกแก้ — ห้ามหารด้วย 0 เงียบ ๆ
    if total_weight <= 0:
        raise ValueError("ผลรวมของ weights ต้องมากกว่า 0")
    return weight_series / total_weight


def _download_adj_close(tickers: list[str], start_date: str) -> pd.DataFrame:
    """ดึงราคา Adj Close สำหรับ tickers ตั้งแต่ start_date; ล้มเหลว → raise (AUDIT.md C1)."""
    from data.fetcher import PriceDataUnavailableError

    try:
        downloaded = yf.download(
            tickers=tickers,
            start=start_date,
            progress=False,
            auto_adjust=False,
            group_by="ticker",
        )
    except Exception as exc:
        raise PriceDataUnavailableError(f"ดึงราคา {tickers} ไม่สำเร็จ: {exc}") from exc

    if downloaded.empty:
        raise PriceDataUnavailableError(f"ดึงราคา {tickers} ได้ผลว่างเปล่า")

    if isinstance(downloaded.columns, pd.MultiIndex):
        prices = downloaded.xs("Adj Close", axis=1, level=1)
    else:
        if "Adj Close" not in downloaded.columns:
            raise PriceDataUnavailableError("ไม่พบคอลัมน์ Adj Close ในข้อมูลที่ดึงมา")
        prices = downloaded[["Adj Close"]].rename(columns={"Adj Close": tickers[0]})

    cleaned = prices.sort_index().ffill().dropna(how="all")
    if cleaned.empty:
        raise PriceDataUnavailableError("ข้อมูลราคาหลังทำความสะอาดว่างเปล่า")
    return cleaned


def simulate_dca(monthly_amount: float, weights: dict, start_date: str) -> dict[str, Any]:
    """
    จำลอง DCA รายเดือน โดยซื้อทุกวันที่ 1 ของเดือน (หรือวันเทรดแรกของเดือน).

    Returns:
        dict ที่มีคีย์:
        - total_invested
        - current_value
        - profit_loss
        - profit_loss_pct
        - history (DataFrame)
        - figure (Plotly Figure)
        - coverage (dict) + coverage_warning (str|None) — ช่วงที่จำลองได้จริง
          และเดือนที่ถูกตัดทิ้งเพราะกองใดกองหนึ่งยังไม่มีราคา (B8) พร้อมรายชื่อกอง
          ที่ไม่ได้ถูกจำลองใน ``coverage[ZERO_WEIGHT_KEY]`` / ``coverage[NO_PRICE_KEY]``
          (T6 — ตัดกองออกจากพอร์ตเงียบ ๆ ผิดพอกับกุตัวเลข)
    """
    try:
        if monthly_amount <= 0:
            raise ValueError("monthly_amount ต้องมากกว่า 0")

        pd.Timestamp(start_date)  # validate รูปแบบวันที่

        # กองที่ตั้งน้ำหนัก 0 ไม่ต้องดึงราคาและไม่ต้องบีบช่วงเดือน — แต่ต้องมีชื่ออยู่ใน
        # รายงานเสมอ (T6) ``validate_weights`` การันตีแล้วว่าผลรวม > 0 จึงเหลืออย่างน้อยหนึ่งกอง
        held, zero_weight = _split_by_weight(validate_weights(weights))
        normalized_weights = _normalize_weights(held)
        tickers = list(normalized_weights.index)

        prices = _download_adj_close(tickers=tickers, start_date=start_date)
        prices = prices[tickers].dropna(how="all")

        # ใช้ราคาวันเทรดแรกของแต่ละเดือน เทียบเท่าการซื้อวันที่ 1
        monthly_prices, coverage = _monthly_first_prices(prices)
        # ที่นี่ไม่มีกรณี "ถือน้ำหนักแต่ไม่มีคอลัมน์ราคา" — ``_download_adj_close`` โยน
        # ``PriceDataUnavailableError`` ไปก่อนแล้ว (fail loud) จึงส่งลิสต์ว่างไว้ให้ครบคีย์
        coverage = _with_exclusions(coverage, zero_weight, [])

        shares = pd.Series(0.0, index=tickers)
        total_invested = 0.0
        records: list[dict[str, float | pd.Timestamp]] = []

        for date, row in monthly_prices.iterrows():
            allocation = normalized_weights * monthly_amount
            purchased_shares = allocation / row
            shares += purchased_shares
            total_invested += monthly_amount

            portfolio_value = float((shares * row).sum())
            profit_loss = portfolio_value - total_invested
            profit_loss_pct = (profit_loss / total_invested * 100.0) if total_invested else 0.0

            records.append(
                {
                    "Date": date,
                    "Total Invested": total_invested,
                    "Portfolio Value": portfolio_value,
                    "Profit/Loss": profit_loss,
                    "Profit/Loss %": profit_loss_pct,
                }
            )

        history = pd.DataFrame(records).set_index("Date")
        history.attrs[COVERAGE_ATTR] = coverage
        current_value = float(history["Portfolio Value"].iloc[-1])
        profit_loss = float(history["Profit/Loss"].iloc[-1])
        profit_loss_pct = float(history["Profit/Loss %"].iloc[-1])

        figure = go.Figure()
        figure.add_trace(go.Scatter(x=history.index, y=history["Total Invested"], mode="lines", name="Total Invested"))
        figure.add_trace(go.Scatter(x=history.index, y=history["Portfolio Value"], mode="lines", name="Portfolio Value"))
        figure.update_layout(
            title="DCA: Cumulative Invested vs Portfolio Value",
            xaxis_title="Date",
            yaxis_title="USD",
            template="plotly_white",
        )

        return {
            "total_invested": float(total_invested),
            "current_value": current_value,
            "profit_loss": profit_loss,
            "profit_loss_pct": profit_loss_pct,
            "history": history,
            "figure": figure,
            "coverage": coverage,
            "coverage_warning": describe_coverage(coverage),
        }
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการจำลอง DCA: {exc}") from exc


def simulate_monthly_dca(
    price_df: pd.DataFrame,
    weights: Dict[str, float],
    monthly_investment: float = 1000.0,
) -> pd.DataFrame:
    """คงไว้เพื่อรองรับโค้ดเดิมที่ส่ง price_df เข้ามาโดยตรง.

    ผลลัพธ์พกรายงานช่วงเวลาไว้ที่ ``df.attrs[COVERAGE_ATTR]`` — เดือนที่ถูกตัดทิ้ง
    เพราะกองใดกองหนึ่งยังไม่มีราคา **ต้องถูกแสดงต่อผู้ใช้** ใช้ ``describe_coverage()``
    แปลงเป็นข้อความ (AUDIT_2026-08-06 B8)

    รายงานเดียวกันนี้พก **รายชื่อกองที่ไม่ได้ถูกจำลอง** มาด้วยสองกลุ่ม
    (AUDIT_ROUND2_2026-08-07 T6): ``coverage[ZERO_WEIGHT_KEY]`` = ตั้งน้ำหนักไว้ 0 (เจตนา)
    และ ``coverage[NO_PRICE_KEY]`` = ถือน้ำหนักอยู่แต่ไม่มีคอลัมน์ราคาในชุดที่ส่งมา
    กลุ่มหลังแปลว่าเส้นมูลค่าที่ได้เป็นของพอร์ตที่ normalize ใหม่บนกองที่เหลือ
    **ไม่ใช่พอร์ตที่ผู้ใช้กรอก** — ห้ามแสดงตัวเลขโดยไม่แสดงคำเตือนคู่กัน
    """
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถจำลอง DCA ได้")
        if monthly_investment <= 0:
            raise ValueError("monthly_investment ต้องมากกว่า 0")

        # ตรวจน้ำหนัก **ทั้งชุด** ก่อนคัดเฉพาะกองที่มีราคา ไม่งั้นค่าเสีย (inf/NaN/ติดลบ)
        # ของกองที่ไม่มีราคาจะถูกคัดทิ้งไปก่อนถึงด่านตรวจ = อินพุตผิดผ่านไปได้เงียบ ๆ
        held, zero_weight = _split_by_weight(validate_weights(weights))

        # กองที่ "ถือน้ำหนักอยู่แต่ไม่มีคอลัมน์ราคา" ถูกตัดออกแล้ว normalize ใหม่บนที่เหลือ
        # การตัดยังทำเหมือนเดิม (หน้าจอต้องมีอะไรให้ดู) แต่ชื่อกองต้องติดไปกับผลลัพธ์เสมอ
        # ไม่งั้นผู้ใช้อ่านกราฟของพอร์ตอื่นว่าเป็นพอร์ตตัวเอง (T6)
        valid_assets = [ticker for ticker in held if ticker in price_df.columns]
        no_price = [ticker for ticker in held if ticker not in price_df.columns]
        if not valid_assets:
            raise ValueError(
                "ไม่พบ ticker ใน weights ที่ตรงกับข้อมูลราคา: "
                + ", ".join(no_price or list(held))
            )

        normalized_weights = _normalize_weights({k: held[k] for k in valid_assets})
        prices = price_df[valid_assets].ffill().dropna(how="all")
        monthly_prices, coverage = _monthly_first_prices(prices)
        coverage = _with_exclusions(coverage, zero_weight, no_price)

        shares = pd.Series(0.0, index=valid_assets)
        records: list[dict[str, float | pd.Timestamp]] = []
        total_invested = 0.0

        for date, row in monthly_prices.iterrows():
            allocation = normalized_weights * monthly_investment
            purchased_shares = allocation / row
            shares += purchased_shares
            total_invested += monthly_investment
            current_value = float((shares * row).sum())
            profit_loss = current_value - total_invested
            profit_loss_pct = (profit_loss / total_invested * 100.0) if total_invested else 0.0

            records.append(
                {
                    "Date": date,
                    "Total Invested": total_invested,
                    "Portfolio Value": current_value,
                    "Profit/Loss": profit_loss,
                    "Profit/Loss %": profit_loss_pct,
                }
            )

        history = pd.DataFrame(records).set_index("Date")
        history.attrs[COVERAGE_ATTR] = coverage
        return history
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการจำลอง DCA: {exc}") from exc


if __name__ == "__main__":
    test_weights = {"VOO": 0.40, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.05}
    result = simulate_dca(monthly_amount=500.0, weights=test_weights, start_date="2020-01-01")

    print(f"Total Invested: ${result['total_invested']:,.2f}")
    print(f"Current Value: ${result['current_value']:,.2f}")
    print(f"Profit/Loss: ${result['profit_loss']:,.2f} ({result['profit_loss_pct']:.2f}%)")
    if result["coverage_warning"]:
        print(f"⚠️  {result['coverage_warning']}")
