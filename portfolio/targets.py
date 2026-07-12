# -*- coding: utf-8 -*-
"""สัดส่วนพอร์ตเป้าหมาย — แหล่งเดียวของทั้งระบบ.

เดิมมีชุดเป้าหมาย 2 ชุดที่ไม่ตรงกัน:
- dashboard / main.py : VOO 35 / SCHD 20 / QQQM 20 / XLV 15 / GLDM 10
- rebalance / goals   : VOO 35 / SCHD 25 / QQQM 20 / XLV 10 / GLDM 10
→ แผน DCA กับแผน rebalance ดึงพอร์ตไปคนละทาง

ตอนนี้ทุกที่อ่านจาก ``get_target_weights()`` ซึ่งมาจาก config.json
(``portfolio.risk_profile`` + ``portfolio.target_weights`` ถ้าตั้งเอง)
"""

from __future__ import annotations

from utils.config import get_tickers, load_config

RISK_PROFILES: dict[str, dict[str, float]] = {
    "conservative": {"VOO": 0.30, "SCHD": 0.30, "QQQM": 0.10, "XLV": 0.20, "GLDM": 0.10},
    "moderate":     {"VOO": 0.35, "SCHD": 0.25, "QQQM": 0.20, "XLV": 0.10, "GLDM": 0.10},
    "aggressive":   {"VOO": 0.25, "SCHD": 0.10, "QQQM": 0.45, "XLV": 0.10, "GLDM": 0.10},
}

DEFAULT_PROFILE = "moderate"


def get_risk_profile() -> str:
    profile = str(load_config()["portfolio"].get("risk_profile", DEFAULT_PROFILE)).strip().lower()
    return profile if profile in RISK_PROFILES else DEFAULT_PROFILE


def get_target_weights(tickers: list[str] | None = None) -> dict[str, float]:
    """สัดส่วนเป้าหมายของ ticker ที่ระบบติดตาม (รวมเป็น 1.0 เสมอ).

    ลำดับความสำคัญ: ``portfolio.target_weights`` ที่ตั้งเอง → preset ตาม risk_profile
    ticker ที่ไม่มีเป้าหมายกำหนดไว้จะได้ส่วนแบ่งจากน้ำหนักที่เหลือแบบเท่า ๆ กัน
    (เพื่อให้ ETF ที่เพิ่งเพิ่มเข้ามาไม่ถูกละเลย และไม่ทำให้สัดส่วนเดิมเพี้ยน)
    """
    config = load_config()
    symbols = [t.strip().upper() for t in (tickers or get_tickers()) if t.strip()]
    if not symbols:
        return {}

    custom = {
        str(k).strip().upper(): float(v)
        for k, v in dict(config["portfolio"].get("target_weights") or {}).items()
        if _is_positive_number(v)
    }
    preset = RISK_PROFILES[get_risk_profile()]

    raw: dict[str, float] = {}
    for symbol in symbols:
        if symbol in custom:
            raw[symbol] = custom[symbol]
        elif symbol in preset:
            raw[symbol] = preset[symbol]
        else:
            raw[symbol] = 0.0

    unset = [s for s in symbols if raw[s] <= 0]
    if unset:
        assigned = sum(raw.values())
        leftover = max(0.0, 1.0 - assigned)
        share = (leftover / len(unset)) if leftover > 0 else (1.0 / len(symbols))
        for symbol in unset:
            raw[symbol] = share

    total = sum(raw.values())
    if total <= 0:
        equal = 1.0 / len(symbols)
        return {s: equal for s in symbols}
    return {s: raw[s] / total for s in symbols}


def _is_positive_number(value: object) -> bool:
    try:
        return float(value) > 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
