# -*- coding: utf-8 -*-
"""โมดูลคำนวณตัวชี้วัดความเสี่ยงของ ETF."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats as _scipy_stats

from utils.cache import cache_data_1h

# อัตราปลอดความเสี่ยงมาตรฐานของทั้งระบบ — ใช้ค่าเดียวกันทุกที่ที่คำนวณ Sharpe
# (AUDIT.md M4: เดิม backtest ใช้ 0% ส่วนหน้า Risk ใช้ 2% → เทียบกันไม่ได้)
DEFAULT_RISK_FREE_RATE = 0.02

# จำนวนวันทำการต่อปีที่ใช้ annualize ทุกตัวเลขในโมดูลนี้ (ค่าเดียวกับพารามิเตอร์
# ``annualization`` ของ Volatility/Sharpe) — เปลี่ยนที่นี่ที่เดียวถ้าต้องเปลี่ยน
TRADING_DAYS_PER_YEAR = 252

MONTHS_PER_YEAR = 12

#: ระดับความเชื่อมั่นของช่วง (two-sided) — 0.95
CONFIDENCE_LEVEL = 0.95
#: ``z(1−α/2) + z(power)`` ที่ α=0.05, power=80% = 1.9600 + 0.8416 — ตัวคูณของ MDE
#: (minimum detectable effect: ส่วนต่างที่เล็กที่สุดที่ข้อมูลชุดนี้มีโอกาส 80% จะจับได้)
_MDE_Z_SUM = 2.8016


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


def paired_diff_stats(
    a: pd.Series,
    b: pd.Series,
    *,
    periods_per_year: int = MONTHS_PER_YEAR,
    label_a: str = "a",
    label_b: str = "b",
) -> dict[str, object]:
    """paired t-test ของผลตอบแทนสองแขนที่วัด**ช่วงเวลาเดียวกัน** — ต่างจริง หรือเสียงรบกวน.

    **ทำไมต้องมี** (FIX_PLAN เฟส 4②) — การตัดสิน "ชนะไหม" ด้วยการเทียบตัวเลขปลายทาง
    จุดเดียว (``final_value_a > final_value_b``) ไม่มีข้อมูลว่าส่วนต่างนั้นใหญ่กว่า
    ความผันผวนของตัวมันเองหรือเปล่า ทั้งที่อนุกรมผลตอบแทนรายเดือนอยู่ในมือแล้ว
    วัดจริงบนพอร์ตนี้: ``พอร์ต 5 ตัว vs VOO = −0.93%/ปี`` แต่ ``CI95 [−2.59, +0.73]``
    ⇒ **แยกไม่ออกจากศูนย์** และต้องใช้ข้อมูลราว 96 ปีถึงจะสรุปได้ที่ขนาดผลเท่านี้

    **จับคู่ (paired) ไม่ใช่สองกลุ่มอิสระ** — สองแขนเจอตลาดเดียวกันทุกเดือน จึงสหสัมพันธ์
    กันสูงมาก การทดสอบแบบจับคู่หักส่วนที่เหมือนกันออกก่อน เหลือแต่ส่วนต่างที่กลยุทธ์ทำ
    ⇒ power ดีกว่าการเทียบสองกลุ่มแยกกันหลายเท่า (MDE ของด่าน edge นี้อยู่ที่ ~0.22%/ปี
    ซึ่งละเอียดพอจะใช้เป็นด่านจริงได้ทันทีโดยไม่ต้องหาข้อมูลเพิ่ม)

    ใช้ **ส่วนต่างของ log return** (``ln(1+r_a) − ln(1+r_b)``) เพราะค่าเฉลี่ยของมันคูณ
    ``periods_per_year`` = ส่วนต่างของอัตราทบต้นต่อปีพอดี ไม่ใช่การประมาณ — ช่วงความเชื่อมั่น
    ที่ได้จึงอ่านเป็น "จุด/ปี" ได้ตรง ๆ (เทียบกับส่วนต่างเลขคณิตที่ต้องแปลงอีกชั้น)

    คืน ``{n_periods, diff_annual_pct, se_annual_pct, t_stat, ci95_low_pct,
    ci95_high_pct, distinguishable_from_zero, mde_annual_pct, periods_needed,
    years_needed, overlap_start, overlap_end, label_a, label_b}``

    ``distinguishable_from_zero`` = ช่วง CI95 **ไม่คร่อมศูนย์** — เท็จ ไม่ได้แปลว่า
    "สองแขนเท่ากัน" แต่แปลว่า **ข้อมูลเท่านี้ยังตอบไม่ได้** ผู้เรียกต้องเขียนให้ต่างกัน
    (ยุบสองอย่างนี้เป็นอันเดียวคือบั๊กชนิดเดียวกับ "ดึงไม่ได้ ≠ ไม่มีข้อมูล")

    ``ValueError`` เมื่อ: ช่วงที่ทับกันสั้นกว่า 2 งวด · มีงวดที่ผลตอบแทน ≤ −100%
    (ทบต้นไม่ได้ ห้ามปัดเป็นเลขสวย)
    """
    frame = pd.concat(
        [
            pd.to_numeric(pd.Series(a), errors="coerce").rename("a"),
            pd.to_numeric(pd.Series(b), errors="coerce").rename("b"),
        ],
        axis=1,
        join="inner",
    ).dropna()
    if len(frame) < 2:
        raise ValueError(
            f"ช่วงเวลาที่ {label_a} กับ {label_b} ทับกันมีแค่ {len(frame)} งวด — "
            "ทดสอบนัยสำคัญไม่ได้ (ต้องมีอย่างน้อย 2 งวด)"
        )
    if float((1.0 + frame).min().min()) <= 0.0:
        raise ValueError("มีงวดที่ผลตอบแทน ≤ −100% — คำนวณส่วนต่างอัตราทบต้นไม่ได้")

    diff = np.log1p(frame["a"]) - np.log1p(frame["b"])
    n = int(len(diff))
    scale = float(periods_per_year) * 100.0
    mean_annual = float(diff.mean()) * scale
    sd = float(diff.std(ddof=1))
    se_annual = sd / math.sqrt(n) * scale

    if se_annual > 0.0:
        t_crit = float(_scipy_stats.t.ppf(0.5 + CONFIDENCE_LEVEL / 2.0, n - 1))
        half_width = t_crit * se_annual
        t_stat: float | None = mean_annual / se_annual
    else:
        # สองแขนต่างกันเท่ากันทุกงวด (หรือเหมือนกันเป๊ะ) — ส่วนต่างไม่มีความผันผวนให้ทดสอบ
        half_width = 0.0
        t_stat = None
    ci_low, ci_high = mean_annual - half_width, mean_annual + half_width
    mde_annual = _MDE_Z_SUM * se_annual

    periods_needed: int | None = None
    if mean_annual != 0.0 and se_annual > 0.0:
        # SE ลดลงตาม 1/√n ⇒ ต้องมี n × (MDE/ผลที่วัดได้)² งวด ถึงจะจับผลขนาดนี้ได้ที่ power 80%
        periods_needed = int(math.ceil(n * (mde_annual / abs(mean_annual)) ** 2))

    return {
        "n_periods": n,
        "diff_annual_pct": mean_annual,
        "se_annual_pct": se_annual,
        "t_stat": t_stat,
        "ci95_low_pct": ci_low,
        "ci95_high_pct": ci_high,
        # ไม่คร่อมศูนย์ = แยกออกจากศูนย์ได้ · เท็จ = "ข้อมูลยังตอบไม่ได้" ไม่ใช่ "เท่ากัน"
        "distinguishable_from_zero": bool(ci_low > 0.0 or ci_high < 0.0),
        "mde_annual_pct": mde_annual,
        "periods_needed": periods_needed,
        "years_needed": None if periods_needed is None else periods_needed / float(periods_per_year),
        "overlap_start": _window_label(frame.index[0]),
        "overlap_end": _window_label(frame.index[-1]),
        "label_a": label_a,
        "label_b": label_b,
    }


def mix_vs_benchmark_test(
    price_df: pd.DataFrame,
    weights: dict[str, float],
    benchmark: str = "VOO",
) -> dict[str, object]:
    """paired t-test "ส่วนผสมนี้ดีกว่า ``benchmark`` จริงไหม" บนผลตอบแทน**รายเดือน**.

    ตอบคำถามที่หัวข้อ "ชนะ VOO ไหม" ถามจริง ๆ ซึ่ง**คนละคำถาม**กับ "สมุดของฉันจบที่เท่าไร":
    มูลค่าปลายทางของสมุดคือผลที่เกิดขึ้นแล้วหนึ่งเส้นทาง ไม่ใช่หลักฐานว่าส่วนผสมดีกว่า
    — ต้องมีช่วงความเชื่อมั่นกำกับ ไม่งั้นเลขจุดเดียวจะถูกอ่านเป็นคำตัดสิน
    (FIX_PLAN เฟส 4②)

    สมมติฐานที่ต้องบอกผู้ใช้: **น้ำหนักคงที่ ปรับสมดุลรายเดือน** และวัดจากราคา
    Adjusted Close รายเดือนของ**ช่วงที่ทุกกองมีข้อมูลพร้อมกัน** (``dropna``) ซึ่งสั้นกว่า
    ช่วงที่ขอมาเมื่อกองใดกองหนึ่งลิสต์ทีหลัง — ``overlap_start``/``overlap_end``
    ที่คืนมาคือช่วงจริง ป้ายบนจอต้องรายงานค่านั้น ไม่ใช่ช่วงที่ขอ

    คืนผลของ :func:`paired_diff_stats` ตรง ๆ (เพิ่ม ``benchmark`` กับ ``tickers``)
    ``ValueError`` เมื่อไม่มี ticker ที่ใช้ได้ / ไม่มีคอลัมน์ benchmark / เดือนที่ทับกันน้อยกว่า 2
    """
    bench = str(benchmark).strip().upper()
    if bench not in price_df.columns:
        raise ValueError(f"ไม่มีข้อมูลราคาของ benchmark ({bench}) — เทียบไม่ได้")
    tickers = [t for t, w in weights.items() if float(w) > 0 and t in price_df.columns]
    if not tickers:
        raise ValueError("ไม่มี ticker ที่มีทั้งน้ำหนักและข้อมูลราคา")

    columns = list(dict.fromkeys([*tickers, bench]))
    # ยุบเป็นราคาปิดของแท่งสุดท้ายในแต่ละเดือนด้วย ``to_period`` ไม่ใช่ ``resample`` —
    # ชื่อ alias ของ resample เปลี่ยนไปมาระหว่างรุ่น pandas (``M``/``ME``) ส่วน period
    # เสถียรกว่า และ ``dropna(how="any")`` ให้ "ช่วงที่ทุกกองมีข้อมูลพร้อมกัน" ตรง ๆ
    numeric = price_df[columns].apply(pd.to_numeric, errors="coerce")
    monthly = numeric.groupby(numeric.index.to_period("M")).last().dropna(how="any")
    if len(monthly) < 3:
        raise ValueError(
            f"ช่วงที่ทุกกองมีราคาพร้อมกันมีแค่ {len(monthly)} เดือน — ทดสอบนัยสำคัญไม่ได้"
        )
    monthly.index = monthly.index.to_timestamp(how="end").normalize()

    # ``fill_method=None`` บังคับตาม B11 — ดีฟอลต์ ``'pad'`` ของ pandas 2.2 คือการ
    # forward-fill ราคาก่อนคำนวณ ⇒ เดือนที่กองใดกองหนึ่งไม่มีแท่งจะกลายเป็น 0.00% พอดี
    # (ที่นี่ ``dropna(how="any")`` ข้างบนตัดไปแล้ว ค่าจึงเท่าเดิม แต่ห้ามพึ่งดีฟอลต์
    #  ที่กุตัวเลขบนเส้นทางราคา — ``tests/test_pct_change_fill_method.py`` ตรึงกฎนี้ไว้)
    returns = monthly.pct_change(fill_method=None).dropna(how="any")
    normalized = pd.Series({t: float(weights[t]) for t in tickers})
    normalized = normalized / normalized.sum()
    mix = (returns[tickers] * normalized).sum(axis=1)

    stats = paired_diff_stats(
        mix, returns[bench], label_a="ส่วนผสมพอร์ต", label_b=bench, periods_per_year=MONTHS_PER_YEAR
    )
    stats["benchmark"] = bench
    stats["tickers"] = tickers
    return stats


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
