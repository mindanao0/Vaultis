"""โมดูลตรวจสอบความเบี่ยงเบนของสัดส่วนพอร์ต."""

from __future__ import annotations

from typing import Dict

import pandas as pd


def check_rebalance_needed(
    price_df: pd.DataFrame,
    target_weights: Dict[str, float],
    threshold: float = 0.05,
) -> pd.DataFrame:
    """ตรวจสอบสัดส่วนปัจจุบันเทียบเป้าหมายและแจ้งว่าควร rebalance หรือไม่."""
    try:
        if price_df.empty:
            raise ValueError("price_df ว่าง ไม่สามารถตรวจสอบ rebalance ได้")
        if not target_weights:
            raise ValueError("target_weights ว่าง ไม่สามารถตรวจสอบ rebalance ได้")
        if threshold <= 0:
            raise ValueError("threshold ต้องมากกว่า 0")

        valid_assets = [ticker for ticker in target_weights if ticker in price_df.columns]
        if not valid_assets:
            raise ValueError("ไม่พบ ticker ใน target_weights ที่ตรงกับข้อมูลราคา")

        latest_prices = price_df[valid_assets].ffill().iloc[-1]
        normalized_target = pd.Series({k: target_weights[k] for k in valid_assets}, dtype=float)
        normalized_target = normalized_target / normalized_target.sum()

        # สมมติถือคนละ 1 หน่วยเพื่อดูน้ำหนักปัจจุบันจากระดับราคา
        market_values = latest_prices.copy()
        current_weights = market_values / market_values.sum()
        drift = (current_weights - normalized_target).abs()
        need_rebalance = drift > threshold

        result = pd.DataFrame(
            {
                "Target Weight": normalized_target,
                "Current Weight": current_weights,
                "Drift": drift,
                "Need Rebalance": need_rebalance,
            }
        )
        return result
    except Exception as exc:
        raise RuntimeError(f"เกิดข้อผิดพลาดในการตรวจสอบ Rebalance: {exc}") from exc
