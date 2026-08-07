from __future__ import annotations

import math
from typing import Any

from alerts.price_alert import get_current_prices
from analysis.llm import LLMDisabledError, chat_text
from portfolio import targets
from portfolio.fees import dime_fee_thb
from utils import fx

DRIFT_THRESHOLD = 0.05  # 5%

# ชื่อโปรไฟล์ที่รับได้ — ใช้ **ตรวจชื่อ** เท่านั้น ห้ามอ่านน้ำหนักจากที่นี่
# (เดิม ``TARGET_WEIGHTS = RISK_PROFILES`` ทำให้แผน rebalance อ่าน preset ดิบ
#  จึงมองไม่เห็น ``portfolio.target_weights`` ที่ผู้ใช้ตั้งเอง และไม่เห็น ticker
#  ที่เพิ่มจากหน้า Settings — AUDIT_2026-08-06 B4.1)
RISK_PROFILE_NAMES: tuple[str, ...] = tuple(targets.RISK_PROFILES)

_RISK_PROFILE_TH = {
    "conservative": "อนุรักษ์นิยม",
    "moderate": "สมดุล",
    "aggressive": "เชิงรุก",
}


class RiskProfileMismatch(ValueError):
    """ผู้เรียกขอโปรไฟล์คนละตัวกับที่ระบบตั้งไว้ = สัดส่วนเป้าหมาย 2 ชุด.

    สัดส่วนเป้าหมายของทั้งระบบมีชุดเดียว (``portfolio/targets.py`` ที่อ่านจาก
    ``config.json``) แผน DCA รายเดือนกับแผน rebalance จึงต้องชี้ไปทางเดียวกันเสมอ
    — ถ้ายอมคำนวณให้โปรไฟล์อื่นตามที่ payload ขอ ผู้ใช้จะได้แผนสั่งซื้อขายด้วยเงินจริง
    ที่ขัดกับแผนซื้อรายเดือนของตัวเองโดยไม่มีอะไรบอก
    """


def resolve_target_weights(risk_profile: str | None = None) -> dict[str, float]:
    """สัดส่วนเป้าหมายของแผน rebalance — มาจาก ``portfolio/targets.py`` แหล่งเดียว.

    ``risk_profile`` คือโปรไฟล์ที่ผู้เรียก (payload ของ API) **ยืนยัน** ว่ากำลังใช้
    ไม่ใช่สวิตช์เลือกชุดน้ำหนัก: ถ้าไม่ตรงกับ ``portfolio.risk_profile`` ใน
    ``config.json`` จะโยน :class:`RiskProfileMismatch` แทนที่จะเงียบ ๆ ใช้ชุดใดชุดหนึ่ง
    (ส่ง ``None`` = ไม่ยืนยันอะไร ใช้โปรไฟล์ของระบบตรง ๆ)
    """
    system_profile = targets.get_risk_profile()
    if risk_profile is not None:
        requested = str(risk_profile).strip().lower()
        if requested not in RISK_PROFILE_NAMES:
            raise ValueError(
                f"risk_profile ไม่ถูกต้อง: {risk_profile!r} — "
                f"ต้องเป็นหนึ่งใน {', '.join(RISK_PROFILE_NAMES)}"
            )
        if requested != system_profile:
            raise RiskProfileMismatch(
                f"ขอแผนสำหรับโปรไฟล์ {requested} แต่ระบบตั้งไว้เป็น {system_profile} — "
                "สัดส่วนเป้าหมายมีแหล่งเดียว แก้ที่ config.json (portfolio.risk_profile) "
                "แล้วเรียกใหม่ เพื่อให้แผน DCA รายเดือนกับแผน rebalance ไปทางเดียวกัน"
            )

    weights = targets.get_target_weights()
    if not weights:
        raise ValueError(
            "ยังไม่มี ETF ที่ระบบติดตาม (config.json → etf.tickers ว่าง) — ทำแผน rebalance ไม่ได้"
        )
    return weights


def _untracked_detail(untracked: list[str]) -> str:
    """คำเตือนสำหรับของที่ถืออยู่แต่ไม่มีสัดส่วนเป้าหมาย — ห้ามตัดทิ้งเงียบ ๆ."""
    if not untracked:
        return ""
    return (
        f"ถืออยู่แต่ไม่มีในสัดส่วนเป้าหมาย: {', '.join(untracked)} — "
        "เป้าหมายของ ticker เหล่านี้คือ 0% (ไม่ได้อยู่ใน config.json → etf.tickers) "
        "แผนนี้จึงนับมูลค่าของมันเป็นแหล่งเงินและตั้งเป้าขายออกทั้งหมด "
        "ถ้าตั้งใจถือต่อ ให้เพิ่มเข้า config ก่อนทำตามแผน"
    )


def _get_usdthb_rate() -> float:
    """ใช้แหล่ง FX กลางเดียวของระบบ — เดิม fallback 35.0 ต่างจากที่อื่น (AUDIT.md M5)."""
    return fx.get_usdthb_rate()


def _get_fx_quote() -> tuple[float, bool | None]:
    """อัตราแลกเปลี่ยนที่ใช้ทำแผน **พร้อมที่มา** (AUDIT_2026-08-06 B9 / C1.5 / L-NW-2).

    ยังดึงตัวเลขผ่าน :func:`_get_usdthb_rate` เพื่อให้ทั้งไฟล์เหลือทางเข้าเดียวเหมือนเดิม
    แล้วถาม ``utils.fx`` ว่าอัตราตัวนี้เป็นค่าสดหรือค่าสำรอง (:func:`utils.fx.source_of`
    อ่านจากแคช ไม่ยิงเน็ตซ้ำ) — แบบเดียวกับ ``portfolio.tracker._get_fx_quote``

    ที่มาต้องเดินทางไปถึงผู้เรียก เพราะอัตราตัวนี้ไม่ได้ใช้แค่แปลงหน่วยแสดงผล:
    ``thb_amount`` ทุกช่อง · ``fee_thb`` ทุกช่อง · และ **จำนวนหน่วยที่สั่งซื้อ**
    (งบบาท ÷ อัตรา = งบ USD) ล้วนมาจากมัน ค่าสำรองคลาดจากค่าสดจริงราว 1.4% ณ วันตรวจ

    ``None`` = **ไม่ทราบที่มา** เกิดเมื่อผู้เรียกจัดหาอัตรามาเอง (เช่นเทสต์ที่แทน
    :func:`_get_usdthb_rate`) — คนละความหมายกับ ``False`` ที่แปลว่ารู้ว่าเป็นค่าสำรอง
    """
    rate = float(_get_usdthb_rate())
    return rate, fx.source_of(rate)


def _fx_fallback_warning(fx_rate: float | None, fx_is_live: bool | None) -> str:
    """คำเตือนเมื่อแผนคิดจากอัตราสำรอง — ``None`` (ไม่ทราบที่มา) ไม่ใช่ความล้มเหลว จึงไม่เตือน."""
    if fx_is_live is not False or fx_rate is None:
        return ""
    return (
        f"ใช้อัตราแลกเปลี่ยนสำรองจาก config ({fx_rate:.2f} บาท/ดอลลาร์) ไม่ใช่ค่าสด "
        "— ตัวเลขบาทในแผนนี้ (มูลค่า, ค่าธรรมเนียม) และจำนวนหน่วยที่สั่งซื้อ "
        "ซึ่งมาจากงบบาทหารด้วยอัตรานี้ อาจคลาดเคลื่อน"
    )


def _join_details(*parts: str) -> str:
    """ต่อคำเตือนหลายเรื่องเข้าด้วยกัน — คนละเรื่องต้องอยู่ร่วมกันได้ ห้ามเบียดกันตกขอบ."""
    return "\n".join(p for p in parts if p)


def _usable_budget_thb(available_budget_thb: Any) -> float:
    """งบที่ใช้คำนวณได้จริง — ``nan``/``inf`` ต้องดัง ไม่ใช่ไหลเข้าไปเป็นจำนวนหน่วย.

    ``nan`` เป็นค่าที่อันตรายที่สุดเพราะเทียบกับอะไรก็เป็น False:
    ``nan <= 0`` ผ่านด่าน "ไม่มีงบ" ไปได้ แล้ว ``delta_usd > 0`` ก็เป็น False อีก
    ทุก ETF จึงตกไปช่อง ``else`` = **สั่งขายทั้งพอร์ตด้วยจำนวน nan**
    """
    try:
        budget = float(available_budget_thb)
    except (TypeError, ValueError):
        budget = float("nan")
    if not math.isfinite(budget):
        raise ValueError(f"งบที่ใช้ได้ไม่ถูกต้อง: {available_budget_thb!r} — ต้องเป็นตัวเลขจำกัด")
    return budget


def _usable_fx_quote() -> tuple[float, bool | None]:
    """อัตราแลกเปลี่ยนที่หารได้จริง **พร้อมที่มา** — 0/ติดลบทำให้ตัวเลขบาททั้งแผนไร้ความหมาย.

    ``utils/fx`` sanity check เฉพาะค่าที่ดึงสดได้ ส่วนค่าสำรองจาก ``config.json``
    ไม่เคยถูกตรวจ (ตั้ง ``default_fx_rate`` เป็น 0 = ZeroDivisionError ดิบ ๆ ที่กลายเป็น
    500 ภาษาอังกฤษ) — ที่นี่จึงกันไว้ก่อนหาร ไม่ตั้งช่วงค่าใหม่ (ช่วงเป็นของ ``utils/fx``)

    **คืนที่มามาด้วยเสมอ ห้ามมีทางเข้าที่คืนแต่ตัวเลข** — เดิมเป็น ``_usable_fx_rate()``
    ที่ทิ้ง ``is_live`` ทิ้ง แผนที่คิดจากค่าสำรองจึงออกไปหน้าตาเหมือนแผนที่คิดจากอัตราสด
    """
    rate, is_live = _get_fx_quote()
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError(f"อัตราแลกเปลี่ยน THB/USD ไม่ถูกต้อง: {rate!r} — คำนวณมูลค่าเงินบาทไม่ได้")
    return rate, is_live


def _usable_price(symbol: str, prices: dict[str, float]) -> float | None:
    """ราคาที่ใช้ได้จริง หรือ ``None`` ถ้าดึงไม่สำเร็จ.

    contract ของ ``get_current_prices()``: ticker ที่ดึงไม่ได้จะ **หายไปจาก dict**
    ไม่ใช่มีค่าเป็น 0 — ห้ามใช้ ``prices.get(sym, 0.0)`` เพราะ "ดึงไม่สำเร็จ"
    จะกลายเป็น "มูลค่า 0" แล้วพลิกทิศคำสั่งซื้อขายด้วยเงินจริง
    """
    raw = prices.get(symbol)
    if raw is None:
        return None
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def _held_shares(holdings: list[dict[str, Any]]) -> dict[str, float]:
    """รวมจำนวนหน่วยที่ถืออยู่จริงต่อ ticker (ข้ามรายการที่ถือ 0 หน่วย).

    **หลาย ๆ ล็อตของ ticker เดียวกันต้องบวกรวมกัน** ตามแหล่งความจริงของพอร์ตคือ
    ``portfolio/tracker.get_portfolio_summary()`` ที่ทำ
    ``groupby("ticker").agg(shares=("shares", "sum"))`` หลัง normalize ticker เป็น
    strip+upper — ซื้อ VOO 40 แล้ว VOO 30 คือถือ 70 หน่วย ไม่ใช่ 30
    (เดิมที่นี่เขียนทับด้วยแถวหลัง ทำให้แผนคิดจากพอร์ตที่เล็กกว่าความจริง)
    ตรึงไว้ด้วย ``tests/test_rebalance_missing_price.py::TestMultiLotAggregation``

    จำนวนหน่วยที่อ่านไม่ได้จะ **ไม่ถูกข้ามเงียบ ๆ** — ถ้าข้ามไป ตัวหาร (มูลค่าพอร์ตรวม)
    จะเล็กลงแล้วทำให้ ETF ตัวอื่นดูเกินสัดส่วน ซึ่งเป็นบั๊กชนิดเดียวกับราคาที่หายไป
    """
    shares: dict[str, float] = {}
    for h in holdings:
        # ต้อง strip ด้วย: ``get_current_prices()`` normalize เป็น strip+upper
        # ถ้าฝั่งนี้ไม่ strip คีย์จะไม่ตรงกัน แล้ว " GLDM " จะถูกรายงานว่า
        # "ดึงราคาไม่สำเร็จ" ทั้งที่ดึงได้ — คือรายงานความล้มเหลวที่ไม่ได้เกิดขึ้นจริง
        sym = str(h["symbol"]).strip().upper()
        if not sym:
            raise ValueError("รายการถือครองมี ticker ว่าง — ระบุสัญลักษณ์ให้ครบก่อนทำแผน rebalance")
        try:
            qty = float(h["shares"])
        except (TypeError, ValueError):
            qty = float("nan")
        if not math.isfinite(qty) or qty < 0:
            raise ValueError(f"จำนวนหน่วยของ {sym} ไม่ถูกต้อง: {h.get('shares')!r}")
        if qty == 0:
            continue
        shares[sym] = shares.get(sym, 0.0) + qty
    return shares


def missing_holding_prices(
    holdings: list[dict[str, Any]],
    prices: dict[str, float],
) -> list[str]:
    """ticker ที่ **ถืออยู่** แต่ไม่มีราคา — คำนวณมูลค่าพอร์ตไม่ได้ถ้ามีตัวใดตัวหนึ่ง."""
    return sorted(sym for sym in _held_shares(holdings) if _usable_price(sym, prices) is None)


def missing_plan_prices(
    holdings: list[dict[str, Any]],
    target: dict[str, float],
    prices: dict[str, float],
) -> list[str]:
    """ticker ที่จำเป็นต่อการทำแผน rebalance แต่ไม่มีราคา.

    รวมทั้งของที่ถืออยู่ (ต้องใช้ตีมูลค่าพอร์ต) และ ETF ในสัดส่วนเป้าหมาย
    (ไม่มีราคา = คำนวณจำนวนหน่วยที่ต้องซื้อไม่ได้ และถ้าข้ามไปเงียบ ๆ
    งบส่วนนั้นจะหายไปจากแผนโดยผู้ใช้ไม่รู้ตัว)
    """
    required = set(_held_shares(holdings)) | {str(s).strip().upper() for s in target}
    return sorted(sym for sym in required if _usable_price(sym, prices) is None)


def calculate_drift(
    holdings: list[dict[str, Any]],
    target: dict[str, float],
    prices: dict[str, float],
) -> float:
    """คืนค่า drift สูงสุด (0–1) เทียบกับ target weights.

    Raises:
        ValueError: เมื่อคำนวณไม่ได้จริง ๆ — ราคาของที่ถืออยู่ขาดไป หรือมูลค่ารวมเป็น 0
            (เดิมคืน 1.0 = "เบี่ยงเบน 100%" ที่ผลิตจากความล้มเหลวล้วน ๆ แล้วสั่งขายตาม)
    """
    missing = missing_holding_prices(holdings, prices)
    if missing:
        raise ValueError(
            f"ดึงราคาไม่สำเร็จ: {', '.join(missing)} — คำนวณ drift ไม่ได้ "
            "(ห้ามตีมูลค่าเป็น 0 เพราะจะทำให้ตัวอื่นดูเกินสัดส่วน)"
        )

    values: dict[str, float] = {}
    for sym, qty in _held_shares(holdings).items():
        values[sym] = qty * float(_usable_price(sym, prices))

    total = sum(values.values())
    if total <= 0:
        raise ValueError("มูลค่าพอร์ตรวมเป็น 0 — ยังไม่มีหน่วยลงทุนให้เทียบสัดส่วน")

    max_drift = 0.0
    for sym, target_w in target.items():
        current_w = values.get(sym, 0.0) / total
        drift = abs(current_w - target_w)
        if drift > max_drift:
            max_drift = drift
    return max_drift


def _build_actions(
    holdings: list[dict[str, Any]],
    target: dict[str, float],
    prices: dict[str, float],
    budget_usd: float,
    fx_rate: float,
) -> list[dict[str, Any]]:
    missing = missing_plan_prices(holdings, target, prices)
    if missing:
        # กันไว้อีกชั้น: ผู้เรียกต้องคัดกรองก่อนแล้ว — ถ้าหลุดมาถึงตรงนี้ต้องดังไม่ใช่เงียบ
        raise ValueError(f"ดึงราคาไม่สำเร็จ: {', '.join(missing)} — สร้างแผน rebalance ไม่ได้")

    values: dict[str, float] = {}
    for sym, qty in _held_shares(holdings).items():
        values[sym] = qty * float(_usable_price(sym, prices))

    total_usd = sum(values.values()) + budget_usd
    actions: list[dict[str, Any]] = []

    for sym, target_w in target.items():
        price = float(_usable_price(str(sym).strip().upper(), prices))

        target_value = target_w * total_usd
        current_value = values.get(sym, 0.0)
        delta_usd = target_value - current_value

        if abs(delta_usd) < 0.01:
            action_type = "hold"
            shares_delta = 0.0
            usd_amount = 0.0
        elif delta_usd > 0:
            action_type = "buy"
            usd_amount = delta_usd
            shares_delta = usd_amount / price
        else:
            action_type = "sell"
            usd_amount = abs(delta_usd)
            shares_delta = usd_amount / price

        fee_thb = dime_fee_thb(usd_amount, fx_rate) if action_type != "hold" else 0.0

        actions.append({
            "symbol": sym,
            "action": action_type,
            "shares": round(shares_delta, 6),
            "usd_amount": round(usd_amount, 2),
            "thb_amount": round(usd_amount * fx_rate, 2),
            "fee_thb": round(fee_thb, 2),
        })

    return actions


def _generate_ai_comment(
    risk_profile: str,
    max_drift: float | None,
    actions: list[dict[str, Any]],
    target: dict[str, float],
    prices: dict[str, float],
    holdings: list[dict[str, Any]],
    user_initiated: bool = False,
) -> str:
    """คำอธิบายจาก AI สำหรับแผนที่ **คำนวณเสร็จแล้ว** (AI อธิบาย โค้ดคำนวณ).

    ``max_drift is None`` = พอร์ตยังว่าง ยังไม่มีสัดส่วนปัจจุบันให้เทียบ ซึ่งคนละเรื่อง
    กับ ``0.0`` (เทียบได้และไม่เบี่ยงเบน) จึงต้องใช้ prompt คนละแบบ — ห้ามยัดตัวหารปลอม
    เพื่อให้สูตรเดินต่อ เพราะมันจะผลิตประโยค "VOO 0.0% ของพอร์ต" ที่ไม่ได้มาจากพอร์ตจริง

    คืน ``""`` เมื่อผู้ใช้ไม่ได้กดขอ AI เอง (LLM ปิดตามนโยบายคุมค่าใช้จ่าย) — ตัวเลขแผน
    ซึ่งเป็นข้อมูลที่ใช้ตัดสินใจจริงยังครบทุกตัว
    """
    # ถึงตรงนี้ราคาครบแล้วเสมอ (compute_rebalance คัดกรองก่อน) — ไม่มีการตีมูลค่าเป็น 0
    values: dict[str, float] = {}
    for sym, qty in _held_shares(holdings).items():
        values[sym] = qty * float(_usable_price(sym, prices))

    portfolio_is_empty = not values
    if portfolio_is_empty != (max_drift is None):
        # ผู้เรียกส่งสถานะไม่ตรงกับพอร์ตที่ส่งมา — ดังไว้ดีกว่าเขียนคำอธิบายผิดฉาก
        raise ValueError("สถานะพอร์ตกับค่า drift ไม่สอดคล้องกัน — ตรวจผู้เรียก compute_rebalance")

    profile_th = _RISK_PROFILE_TH.get(risk_profile, risk_profile)

    if portfolio_is_empty:
        situation = (
            f"พอร์ตยังไม่มีหน่วยลงทุน — นี่คือการจัดสรรเงินก้อนแรกตามสัดส่วนเป้าหมาย"
            f"ของโปรไฟล์ {profile_th} (ยังไม่มีสัดส่วนปัจจุบันให้เทียบ จึงไม่มีค่าการเบี่ยงเบน)\n"
            f"สัดส่วนเป้าหมาย: {', '.join(f'{sym} {tw*100:.0f}%' for sym, tw in target.items())}"
        )
        ask = "อธิบายสั้น ๆ ว่าทำไมจึงกระจายเงินก้อนแรกตามสัดส่วนนี้ และประโยชน์ที่ได้รับ"
        plan_label = "แผนจัดสรรเงินก้อนแรก"
    else:
        # ตัวหารมาจากมูลค่าจริงเท่านั้น — ห้ามมี ``or 1.0`` สำรอง (สำนวนต้องห้ามบนเส้นทางเงิน)
        total = sum(values.values())
        overweight = [
            f"{sym} ({values.get(sym, 0.0)/total*100:.1f}% vs เป้า {tw*100:.0f}%)"
            for sym, tw in target.items()
            if values.get(sym, 0.0) / total - tw > 0.01
        ]
        underweight = [
            f"{sym} ({values.get(sym, 0.0)/total*100:.1f}% vs เป้า {tw*100:.0f}%)"
            for sym, tw in target.items()
            if tw - values.get(sym, 0.0) / total > 0.01
        ]
        situation = (
            f"พอร์ตโฟลิโอมีการเบี่ยงเบนสูงสุด {max_drift*100:.1f}% "
            f"จากสัดส่วนเป้าหมายของโปรไฟล์ {profile_th}\n"
            f"ETF เกินสัดส่วน: {', '.join(overweight) if overweight else 'ไม่มี'}\n"
            f"ETF ขาดสัดส่วน: {', '.join(underweight) if underweight else 'ไม่มี'}"
        )
        ask = "อธิบายสั้น ๆ ว่าทำไมต้อง rebalance และประโยชน์ที่ได้รับ"
        plan_label = "แผน rebalance"

    buys = [a["symbol"] for a in actions if a["action"] == "buy"]
    sells = [a["symbol"] for a in actions if a["action"] == "sell"]
    action_summary = ""
    if buys:
        action_summary += f"ซื้อเพิ่ม: {', '.join(buys)}  "
    if sells:
        action_summary += f"ขาย: {', '.join(sells)}"

    user_msg = (
        f"{situation}\n"
        f"{plan_label}: {action_summary if action_summary else 'ไม่มีการซื้อขาย'}\n"
        f"{ask}"
    )

    system_prompt = (
        "คุณเป็นที่ปรึกษาการลงทุนสำหรับนักลงทุนรายย่อยชาวไทย "
        "ให้คำแนะนำเกี่ยวกับการจัดสรร/rebalance พอร์ตโฟลิโอ ETF "
        "ตัวเลขทั้งหมดคำนวณมาแล้ว — อธิบายเท่านั้น ห้ามคำนวณใหม่ "
        "อธิบายเป็นภาษาไทย กระชับ ชัดเจน ไม่เกิน 3 ประโยค"
    )
    try:
        return chat_text(
            system_prompt, user_msg, max_tokens=600, temperature=0.3, user_initiated=user_initiated
        )
    except LLMDisabledError:
        return ""  # แผน rebalance (ตัวเลข) ยังครบ — แค่ไม่มีคำอธิบายจาก AI
    except Exception as exc:
        return f"ไม่สามารถสร้างคำแนะนำได้: {exc}"


def _no_plan(
    missing_prices: list[str],
    detail: str,
    untracked_holdings: list[str] | None = None,
    fx_rate_thb: float | None = None,
    fx_is_live: bool | None = None,
) -> dict[str, Any]:
    """ผลลัพธ์แบบ "ไม่มีแผน" — สั่งเงินจริงต้อง fail closed ไม่ใช่เดาต่อ.

    ``needs_rebalance = None`` แปลว่า *ยังตอบไม่ได้* ซึ่งต่างจาก ``False``
    (ตอบได้ว่าไม่ต้องทำอะไร) — ผู้เรียกต้องแยกสองอย่างนี้ออกจากกัน

    ``fx_rate_thb``/``fx_is_live`` ค้างเป็น ``None`` เมื่อยังไม่ได้ใช้อัตราแลกเปลี่ยนเลย
    (เช่นคัดออกตั้งแต่ราคาขาด) — "ไม่ได้ใช้" คนละเรื่องกับ "ใช้ค่าสำรอง" ห้ามยุบเป็น ``False``
    """
    return {
        "needs_rebalance": None,
        "max_drift_pct": None,
        "actions": [],
        "total_fee_thb": 0.0,
        "ai_comment": "",
        "missing_prices": missing_prices,
        "untracked_holdings": list(untracked_holdings or []),
        "fx_rate_thb": fx_rate_thb,
        "fx_is_live": fx_is_live,
        "detail": detail,
    }


def compute_rebalance(
    holdings: list[dict[str, Any]],
    risk_profile: str,
    available_budget_thb: float,
    user_initiated: bool = False,
) -> dict[str, Any]:
    """แผน rebalance — ถ้าราคาที่จำเป็นขาดแม้ตัวเดียวจะไม่ผลิต action ใด ๆ เลย.

    เดิมราคาที่ดึงไม่ได้ถูกตีเป็น 0 ทำให้มูลค่าพอร์ตรวม (ตัวหาร) เล็กลง
    ETF ที่เหลือจึงดู overweight แล้ว "ซื้อเพิ่ม" พลิกเป็น "ขาย" — เช่น GLDM ดึงไม่ได้
    ทำให้ XLV จาก buy 150 USD กลายเป็น sell 1,450 USD

    ``risk_profile`` ต้องตรงกับโปรไฟล์ของระบบ (ดู :func:`resolve_target_weights`)
    ของที่ถืออยู่แต่ไม่มีสัดส่วนเป้าหมายจะได้เป้า 0% และถูกรายงานใน
    ``untracked_holdings`` — เดิมมูลค่าของมันถูกนับเข้าตัวตั้งแต่ตัวมันเองไม่มีวันได้ action
    ทำให้แผน "สั่งซื้อรวม 10,000 USD ด้วยงบ 0" โดยไม่มีคำสั่งขายมาถ่วง (B4.2)

    ผลลัพธ์พก ``fx_rate_thb`` + ``fx_is_live`` ออกไปเสมอ (B9/C1.5): อัตราแลกเปลี่ยน
    ไม่ใช่แค่หน่วยแสดงผล มันเป็นตัวหารของงบและเป็นตัวคูณของค่าธรรมเนียม แผนที่คิดจาก
    **ค่าสำรอง** (``fx_is_live=False``) จึงห้ามออกไปหน้าตาเหมือนแผนที่คิดจากอัตราสด
    """
    budget_thb = _usable_budget_thb(available_budget_thb)

    target = resolve_target_weights(risk_profile)
    # ชื่อโปรไฟล์ที่ใช้จริงเสมอ (payload ส่ง None ได้) — ห้ามให้ ``None`` หลุดเข้า prompt
    profile_name = str(risk_profile).strip().lower() if risk_profile else targets.get_risk_profile()

    # เป้าหมายที่ใช้ทำแผนจริง = เป้าหมายของระบบ + ของที่ถืออยู่นอกเป้า (เป้า 0%)
    # ทุกฟังก์ชันด้านล่างต้องเห็นชุดเดียวกัน ไม่งั้นตัวตั้งกับตัวหารจะคิดจากคนละพอร์ต
    held = _held_shares(holdings)
    untracked = sorted(sym for sym in held if sym not in target)
    plan_target = {**target, **{sym: 0.0 for sym in untracked}}
    untracked_detail = _untracked_detail(untracked)

    prices = get_current_prices(list(plan_target))

    missing = missing_plan_prices(holdings, plan_target, prices)
    if missing:
        return _no_plan(
            missing,
            f"ดึงราคาไม่สำเร็จ: {', '.join(missing)} — ไม่คำนวณแผน rebalance "
            "เพื่อกันการสั่งซื้อ/ขายผิดทิศ ลองใหม่อีกครั้งเมื่อแหล่งราคากลับมา",
            untracked_holdings=untracked,
        )

    fx_rate, fx_is_live = _usable_fx_quote()
    fx_detail = _fx_fallback_warning(fx_rate, fx_is_live)
    budget_usd = budget_thb / fx_rate

    if not held:
        # ยังไม่ถืออะไรเลย = ไม่มี drift ให้วัด (0/0) — ไม่ใช่ "เบี่ยงเบน 100%"
        # แต่ยังจัดสรรเงินก้อนแรกตามสัดส่วนเป้าหมายได้ ถ้ามีงบ
        if budget_usd <= 0:
            return _no_plan(
                [],
                "ยังไม่มีหน่วยลงทุนและไม่มีงบ — ไม่มีอะไรให้จัดสรร",
                fx_rate_thb=fx_rate,
                fx_is_live=fx_is_live,
            )
        actions = _build_actions(holdings, plan_target, prices, budget_usd, fx_rate)
        # ผู้ใช้ที่กดปุ่ม "ให้ AI อธิบาย" บนแผนก้อนแรกต้องได้คำอธิบายเหมือนแผนอื่น
        # (ก่อนหน้านี้เส้นทางนี้คืน ai_comment ว่างเสมอเพราะ return ก่อนถึงการเรียก AI)
        ai_comment = _generate_ai_comment(
            profile_name, None, actions, plan_target, prices, holdings,
            user_initiated=user_initiated,
        )
        return {
            "needs_rebalance": True,
            "max_drift_pct": None,
            "actions": actions,
            "total_fee_thb": round(sum(a["fee_thb"] for a in actions), 2),
            "ai_comment": ai_comment,
            "missing_prices": [],
            "untracked_holdings": [],
            "fx_rate_thb": fx_rate,
            "fx_is_live": fx_is_live,
            "detail": _join_details(
                "พอร์ตยังว่าง — แผนนี้คือการจัดสรรเงินก้อนแรกตามสัดส่วนเป้าหมาย "
                "(ไม่มีค่า drift เพราะยังไม่มีมูลค่าให้เทียบ)",
                fx_detail,
            ),
        }

    max_drift = calculate_drift(holdings, plan_target, prices)

    if max_drift < DRIFT_THRESHOLD:
        return {
            "needs_rebalance": False,
            "max_drift_pct": round(max_drift * 100, 2),
            "actions": [],
            "total_fee_thb": 0.0,
            "ai_comment": "",
            "missing_prices": [],
            # ของนอกเป้าที่ยังเล็กพอจนไม่ถึงเกณฑ์ drift ก็ยังต้องบอกผู้ใช้
            "untracked_holdings": untracked,
            # ไม่มี action ก็จริง แต่ "ไม่ต้อง rebalance" เป็นคำตอบที่คิดจากอัตรานี้
            "fx_rate_thb": fx_rate,
            "fx_is_live": fx_is_live,
            "detail": _join_details(untracked_detail, fx_detail),
        }

    actions = _build_actions(holdings, plan_target, prices, budget_usd, fx_rate)
    total_fee_thb = round(sum(a["fee_thb"] for a in actions), 2)
    ai_comment = _generate_ai_comment(
        profile_name, max_drift, actions, plan_target, prices, holdings,
        user_initiated=user_initiated,
    )

    return {
        "needs_rebalance": True,
        "max_drift_pct": round(max_drift * 100, 2),
        "actions": actions,
        "total_fee_thb": total_fee_thb,
        "ai_comment": ai_comment,
        "missing_prices": [],
        "untracked_holdings": untracked,
        "fx_rate_thb": fx_rate,
        "fx_is_live": fx_is_live,
        "detail": _join_details(untracked_detail, fx_detail),
    }
