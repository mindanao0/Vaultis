# -*- coding: utf-8 -*-
"""โมดูลคำนวณตัวชี้วัดความเสี่ยงของ ETF."""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.cache import cache_data_1h

# อัตราปลอดความเสี่ยงมาตรฐานของทั้งระบบ — ใช้ค่าเดียวกันทุกที่ที่คำนวณ Sharpe
# (AUDIT.md M4: เดิม backtest ใช้ 0% ส่วนหน้า Risk ใช้ 2% → เทียบกันไม่ได้)
DEFAULT_RISK_FREE_RATE = 0.02

# จำนวนวันทำการต่อปีที่ใช้ annualize ทุกตัวเลขในโมดูลนี้ (ค่าเดียวกับพารามิเตอร์
# ``annualization`` ของ Volatility/Sharpe) — เปลี่ยนที่นี่ที่เดียวถ้าต้องเปลี่ยน
TRADING_DAYS_PER_YEAR = 252


def calculate_daily_returns(price_df: pd.DataFrame) -> pd.DataFrame:
    """คำนวณผลตอบแทนรายวันจากราคา Adjusted Close.

    ``fill_method=None`` บังคับไว้ (AUDIT_2026-08-06 B11) — ค่าเริ่มต้นของ pandas คือ
    ``'pad'`` ซึ่ง **forward-fill ราคาก่อนคำนวณ** วันที่ ETF ตัวหนึ่งไม่มีแท่งจึงกลายเป็น
    ผลตอบแทน 0.00% พอดีแล้วไหลเข้า Volatility/Sharpe/Correlation = กุตัวเลขบนเส้นทางราคา
    วันที่ไม่มีข้อมูลต้องคง ``NaN`` ไว้ให้ ``dropna``/``skipna`` จัดการต่ออย่างซื่อสัตย์
    (ผู้เรียกที่*ตั้งใจ*จะเติมช่องว่างต้อง ``ffill`` เองที่จุดใช้งาน เช่น ``etf_service``)
    """
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถคำนวณผลตอบแทนรายวันได้")
        return price_df.sort_index().pct_change(fill_method=None).dropna(how="all")
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณผลตอบแทนรายวัน: {exc}") from exc


def calculate_volatility(price_df: pd.DataFrame, annualization: int = 252) -> pd.Series:
    """คำนวณความผันผวนรายปี (Annualized Volatility)."""
    try:
        daily_returns = calculate_daily_returns(price_df)
        volatility = daily_returns.std() * np.sqrt(annualization)
        return volatility
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ Volatility: {exc}") from exc


def calculate_sharpe_ratio(
    price_df: pd.DataFrame,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    annualization: int = 252,
) -> pd.Series:
    """คำนวณ Sharpe Ratio แบบ annualized."""
    try:
        daily_returns = calculate_daily_returns(price_df)
        mean_return = daily_returns.mean() * annualization
        volatility = daily_returns.std() * np.sqrt(annualization)
        sharpe = (mean_return - risk_free_rate) / volatility.replace(0, np.nan)
        return sharpe
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ Sharpe Ratio: {exc}") from exc


def underwater_series(prices: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """ซีรีส์ % ต่ำกว่าจุดสูงสุดเดิม (underwater) — ค่ากลางตัวเดียวกับที่ใช้คิด Max Drawdown.

    0 = อยู่ที่ ATH, -0.25 = ต่ำกว่า ATH 25% (Roadmap A3 — กราฟ underwater)
    รับได้ทั้ง DataFrame (ต่อคอลัมน์) และ Series ตัวเดียว
    """
    if prices.empty:
        raise ValueError("prices ว่าง ไม่สามารถคำนวณ underwater ได้")
    cumulative_max = prices.ffill().cummax()
    return (prices / cumulative_max) - 1.0


def calculate_max_drawdown(price_df: pd.DataFrame) -> pd.Series:
    """คำนวณ Max Drawdown ของ ETF แต่ละตัว (จุดต่ำสุดของซีรีส์ underwater)."""
    try:
        return underwater_series(price_df).min()
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการคำนวณ Max Drawdown: {exc}") from exc


def drawdown_episodes(prices: pd.Series, min_depth: float = 0.10) -> list[dict]:
    """แยกรอบ drawdown ในอดีตของ ETF ตัวเดียว: พีค → จุดต่ำสุด → วันกลับมา ATH.

    ใช้เล่าประวัติ "เคยลงลึกแค่ไหน ฟื้นกี่เดือน" ประกอบกราฟ underwater (Roadmap A3)
    — สถิติเชิงบรรยายจากราคาจริง ไม่ใช่สัญญาณซื้อขาย และไม่เข้าเลขคะแนน/จัดสรรใด ๆ

    คืนเฉพาะรอบที่ลึกเกิน ``min_depth`` (สัดส่วน เช่น 0.10 = ลง 10%) เรียงจากลึกสุด
    รอบที่ยังไม่กลับมา ATH (รอบปัจจุบัน) จะมี ``recovery_date=None``
    """
    close = pd.to_numeric(prices, errors="coerce").dropna()
    if close.empty:
        raise ValueError("ไม่มีข้อมูลราคา ไม่สามารถแยกรอบ drawdown ได้")

    uw = underwater_series(close)
    in_drawdown = uw < 0
    runs = (in_drawdown != in_drawdown.shift(1)).cumsum()

    episodes: list[dict] = []
    for _, segment in uw[in_drawdown].groupby(runs[in_drawdown]):
        depth = float(segment.min())
        if depth > -abs(min_depth):
            continue
        start = segment.index[0]
        start_pos = int(uw.index.get_loc(start))
        peak_date = uw.index[start_pos - 1] if start_pos > 0 else start
        trough_date = segment.idxmin()
        end_pos = int(uw.index.get_loc(segment.index[-1]))
        recovered = end_pos + 1 < len(uw)  # มีวันถัดไปที่กลับมา ≥ ATH; ไม่มี = รอบปัจจุบัน
        recovery_date = uw.index[end_pos + 1] if recovered else None
        episodes.append(
            {
                "peak_date": peak_date,
                "trough_date": trough_date,
                "recovery_date": recovery_date,
                "depth_pct": round(depth * 100, 1),
                "months_to_trough": round((trough_date - peak_date).days / 30.44, 1),
                "months_to_recover": (
                    round((recovery_date - peak_date).days / 30.44, 1) if recovered else None
                ),
            }
        )

    episodes.sort(key=lambda e: e["depth_pct"])
    return episodes


def _portfolio_daily_returns(
    price_df: pd.DataFrame, weights: dict[str, float]
) -> tuple[pd.Series, list[str], int]:
    """ผลตอบแทนรายวันของพอร์ต (ถ่วงน้ำหนัก, สมมติ rebalance รายวัน) — ตัวกลางของ μ/σ ทุกตัว.

    คืน ``(ซีรีส์รายวัน, ticker ที่ใช้จริง, จำนวนแถวก่อน dropna)``
    แถวก่อน/หลัง ``dropna`` ต่างกันเมื่อกองใดกองหนึ่งมีประวัติสั้นกว่าเพื่อน (เช่น QQQM
    เพิ่งลิสต์ปี 2020) — ผู้เรียก **ต้องรายงานส่วนต่างนี้** ไม่ใช่ปล่อยให้ป้ายบอกช่วงที่ขอมา
    (AUDIT_ROUND2_2026-08-07 · FIX_PLAN เฟส 4①)
    """
    tickers = [t for t, w in weights.items() if w > 0 and t in price_df.columns]
    if not tickers:
        raise ValueError("ไม่มี ticker ที่มีทั้งน้ำหนักและข้อมูลราคา")
    all_returns = calculate_daily_returns(price_df[tickers])
    rows_available = int(len(all_returns))
    daily_returns = all_returns.dropna()
    if daily_returns.empty:
        raise ValueError("ผลตอบแทนรายวันว่าง — คำนวณ μ/σ ไม่ได้")
    normalized = pd.Series({t: float(weights[t]) for t in tickers})
    normalized = normalized / normalized.sum()
    return (daily_returns * normalized).sum(axis=1), tickers, rows_available


def _window_label(value: object) -> str:
    """ป้ายวันที่ของขอบหน้าต่างข้อมูล — คืนสตริงเสมอเพื่อให้ JSON-serializable."""
    try:
        return pd.Timestamp(value).date().isoformat()
    except (TypeError, ValueError):
        return str(value)


def portfolio_return_stats(price_df: pd.DataFrame, weights: dict[str, float]) -> dict[str, object]:
    """สถิติผลตอบแทนของพอร์ต **พร้อมช่วงข้อมูลที่ใช้จริง** — ตัวป้อน Monte Carlo (Roadmap ข้อ 15).

    คืนค่าเฉลี่ยผลตอบแทนต่อปี **สองตัวที่ห้ามสลับกัน** (AUDIT_ROUND2_2026-08-07 ·
    FIX_PLAN เฟส 4①) — เดิมมีแค่ตัวเลขคณิตตัวเดียวแล้วปลายทางเอาไปทบต้น:

    - ``mu_arithmetic`` = ``mean(รายวัน) × 252`` — ค่าเฉลี่ยเลข**คณิต** ตัวเดียวกับที่
      ``calculate_sharpe_ratio`` ใช้ เหมาะกับการเป็น *drift ต่องวด* ของการจำลองที่มี
      ความผันผวน (Monte Carlo) เพราะการทบต้นในตัวจำลองจะหักส่วนต่างออกให้เอง
      **ห้ามเอาไปยกกำลังทบต้นตรง ๆ** — มันสูงกว่าอัตราทบต้นจริงราว σ²/2 ต่อปี
      (σ 15% ⇒ สูงเกิน ~1.1 จุด/ปี ⇒ บอกให้ผู้ใช้ออมน้อยกว่าที่ต้องออมจริง)
    - ``mu_geometric`` = ``prod(1+r)^(252/n) − 1`` — อัตราทบต้นต่อปี (CAGR) คือตัวเดียว
      ที่ถูกต้องเมื่อจะ "โตทบต้น" เช่นสูตร PMT / มูลค่าคาดการณ์ปลายทาง

    และช่วงข้อมูลที่ใช้จริง: ``window_start`` / ``window_end`` (วันของ**ผลตอบแทน**แถวแรก
    และแถวสุดท้ายหลัง ``dropna``), ``window_days`` (แถวที่ใช้), ``window_days_available``
    (แถวที่ดึงมาได้ก่อนตัด) และ ``window_years`` = ``window_days / 252``

    ข้อมูล/น้ำหนักใช้ไม่ได้ → raise ValueError — ผู้เรียกค่อย fallback ไปค่า preset
    อย่างโปร่งใส ห้ามเงียบ ๆ กลายเป็นเลขคงที่ (AUDIT.md C1)
    """
    portfolio_daily, tickers, rows_available = _portfolio_daily_returns(price_df, weights)

    mu_arithmetic = float(portfolio_daily.mean() * TRADING_DAYS_PER_YEAR)
    sigma = float(portfolio_daily.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    if not np.isfinite(mu_arithmetic) or not np.isfinite(sigma) or sigma <= 0:
        raise ValueError("μ/σ ที่ได้ไม่สมเหตุสมผล (ข้อมูลอาจสั้น/นิ่งเกินไป)")

    # ทบต้นได้ก็ต่อเมื่อไม่มีวันไหนที่มูลค่าหายเกิน 100% — เจอเมื่อไหร่ต้องดัง ห้ามปัดเป็นเลขสวย
    if float((1.0 + portfolio_daily).min()) <= 0.0:
        raise ValueError("มีวันที่ผลตอบแทนพอร์ต ≤ −100% — คำนวณอัตราทบต้น (CAGR) ไม่ได้")
    mu_geometric = float(np.expm1(np.log1p(portfolio_daily).mean() * TRADING_DAYS_PER_YEAR))
    if not np.isfinite(mu_geometric):
        raise ValueError("อัตราทบต้น (CAGR) ที่ได้ไม่สมเหตุสมผล")

    days_used = int(len(portfolio_daily))
    return {
        "mu_arithmetic": mu_arithmetic,
        "mu_geometric": mu_geometric,
        "sigma": sigma,
        "tickers": tickers,
        "window_start": _window_label(portfolio_daily.index[0]),
        "window_end": _window_label(portfolio_daily.index[-1]),
        "window_days": days_used,
        "window_days_available": rows_available,
        "window_years": days_used / TRADING_DAYS_PER_YEAR,
    }


def portfolio_mu_sigma(price_df: pd.DataFrame, weights: dict[str, float]) -> tuple[float, float]:
    """μ (เลข**คณิต**) / σ ต่อปีของพอร์ต — รูปย่อของ :func:`portfolio_return_stats`.

    ⚠ ``mu`` ที่คืนคือค่าเฉลี่ยเลขคณิต (ตัวเดียวกับที่ Sharpe ใช้) **ห้ามเอาไปทบต้น**
    ผู้เรียกที่ต้องการอัตราทบต้นให้ใช้ ``portfolio_return_stats()["mu_geometric"]``
    (AUDIT_ROUND2_2026-08-07 · FIX_PLAN เฟส 4①)
    """
    stats = portfolio_return_stats(price_df, weights)
    return float(stats["mu_arithmetic"]), float(stats["sigma"])


@cache_data_1h
def calculate_risk_metrics(
    price_df: pd.DataFrame, risk_free_rate: float = DEFAULT_RISK_FREE_RATE
) -> pd.DataFrame:
    """รวมผลลัพธ์ตัวชี้วัดความเสี่ยงเป็นตารางเดียว."""
    try:
        metrics = pd.DataFrame(
            {
                "Volatility": calculate_volatility(price_df),
                "Sharpe Ratio": calculate_sharpe_ratio(price_df, risk_free_rate=risk_free_rate),
                "Max Drawdown": calculate_max_drawdown(price_df),
            }
        )
        return metrics
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการรวม Risk Metrics: {exc}") from exc
