import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

DEFAULT_BACKEND_URL = "https://vaultis-backend.onrender.com"

# ``secrets.BACKEND_URL`` ที่ยังไม่ถูกตั้งใน GitHub Actions ถูกส่งเข้ามาเป็น **สตริงว่าง**
# (ไม่ใช่ "ไม่มีตัวแปร") ⇒ ``os.environ.get(k, default)`` คืน ``''`` แล้ว URL กลายเป็น
# ``/api/etf/daily-snapshot`` → ``Invalid URL: No scheme supplied`` ทุกครั้ง
# สำนวนนี้เหมือนกับที่ ``backend/database.py`` ใช้อยู่แล้ว (AUDIT_2026-08-06 ข้อ M-CI-1)
BACKEND_URL = (os.environ.get("BACKEND_URL") or "").strip() or DEFAULT_BACKEND_URL

# backend อยู่บน Render free — cold start วัดได้ 80.78 วิ ขณะที่โค้ดเดิมตั้ง timeout=30
# ⇒ ถึง secret ถูกตั้งถูกต้อง CI ก็ได้ ``{}`` แล้วตกไปเส้นทางสำรองอยู่ดี
BACKEND_TIMEOUT_SEC = float((os.environ.get("BACKEND_TIMEOUT_SEC") or "").strip() or 120)

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
TICKERS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]

SEP_LINE = "─" * 31


def _as_float(value: Any) -> float | None:
    """แปลงเป็น float แบบไม่กุค่า — ``None``/NaN/แปลงไม่ได้ → ``None`` (ห้ามเป็น 0.0)."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def fetch_daily_snapshot_from_backend() -> tuple[dict, str | None]:
    """คืน ``(data, error)`` — ``error`` เป็น ``None`` เมื่อสำเร็จ.

    ผู้เรียกต้องแยก "backend บอกว่าไม่มีข้อมูล" ออกจาก "ยิงไม่ถึง backend" ได้
    เดิมความล้มเหลวจบที่ ``print()`` ใน log ของ GitHub Actions คนอ่าน Discord
    จึงไม่มีทางรู้ว่าตัวเลขที่เห็นมาจากเส้นทางสำรอง
    """
    url = f"{BACKEND_URL}/api/etf/daily-snapshot"
    print(f"Fetching from: {url}")
    try:
        r = requests.get(url, timeout=BACKEND_TIMEOUT_SEC)
        r.raise_for_status()
        data = r.json()
        print(f"Got snapshot: {data}")
        return (data.get("data", {}) or {}), None
    except Exception as e:
        print(f"Daily snapshot error: {e}")
        return {}, f"daily-snapshot: {e}"


def fetch_prices_from_backend() -> tuple[dict, str | None]:
    """คืน ``(data, error)`` — เหตุผลเดียวกับ ``fetch_daily_snapshot_from_backend``."""
    url = f"{BACKEND_URL}/api/etf/prices"
    print(f"Fetching from: {url}")
    try:
        r = requests.get(url, timeout=BACKEND_TIMEOUT_SEC)
        r.raise_for_status()
        data = r.json()
        return (data.get("data", {}) or {}), None
    except Exception as e:
        print(f"Backend error: {e}")
        return {}, f"prices: {e}"


def _yfinance_snapshot(ticker: str) -> dict[str, Any]:
    """ราคาปิดล่าสุด + %เปลี่ยนแปลง จาก **แท่งปิด 2 แท่ง** ของ history.

    ฐานเดียวกับ ``/api/etf/daily-snapshot`` (FIX_PLAN 2.2) — เดิมใช้
    ``fast_info['previous_close']`` ซึ่งเป็นฟิลด์จาก quote endpoint ที่ไม่ตรงกับ
    bar รายวันใด ๆ ทำให้ข้อความสองใบของวันเดียวกันพลิกเครื่องหมายกันเอง

    ``change_pct`` เป็น ``None`` เมื่อมีแท่งจริงไม่ถึง 2 แท่งหรือราคาอ้างอิง ``<= 0``
    — **ห้ามเป็น 0.0** เพราะ ``+0.00% 🟢`` อ่านเป็น "วันนี้ราคาไม่เปลี่ยน" (FIX_PLAN 2.3)
    """
    out: dict[str, Any] = {"price": None, "change_pct": None, "date": None, "error": None}
    try:
        import pandas as pd
        import yfinance as yf

        hist = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
        if hist is None or getattr(hist, "empty", True) or "Close" not in hist.columns:
            out["error"] = "ไม่มีแท่งราคาจาก yfinance"
            return out
        closes = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        if closes.empty:
            out["error"] = "ไม่มีแท่งราคาจาก yfinance"
            return out
        out["price"] = float(closes.iloc[-1])
        try:
            out["date"] = closes.index[-1].strftime("%d/%m/%Y")
        except Exception:  # index ที่ไม่ใช่ datetime — ไม่ใช่เหตุให้ทิ้งราคา
            out["date"] = None
        if len(closes) >= 2:
            prev = float(closes.iloc[-2])
            if prev > 0:
                out["change_pct"] = (out["price"] - prev) / prev * 100.0
    except Exception as exc:
        print(f"{ticker} yfinance error: {exc}")
        out["error"] = str(exc)
    return out


def _change_pct_yfinance(ticker: str) -> float | None:
    """%เปลี่ยนแปลงจากแท่งปิด 2 แท่ง — ``None`` เมื่อดึงไม่ได้ (เดิมคืน ``0.0``)."""
    return _yfinance_snapshot(ticker)["change_pct"]


def _row_for_ticker(
    ticker: str,
    snapshot: dict,
    prices: dict,
) -> dict[str, Any]:
    """สถานะของ ticker หนึ่งตัวสำหรับข้อความสรุป.

    คืน ``{"price", "change_pct", "date", "stale", "note", "source"}`` —
    ค่าที่ไม่รู้เป็น ``None`` เสมอ ห้ามแทนด้วย 0

    ลำดับความน่าเชื่อถือ:

    1. snapshot จาก backend ที่ **ไม่ติดธง ``stale``/``error``** (แท่งปิด EOD)
    2. แท่งปิด 2 แท่งจาก yfinance (ฐานเดียวกัน)
    3. ราคาที่ backend บอกว่าเป็นของวันเก่า — แสดงได้ แต่ต้องติดป้าย ⚠️ พร้อมวันที่จริง
    4. ``/api/etf/prices`` (ราคาล่าสุดอย่างเดียว ไม่มีฐานเทียบ) → ``change_pct = None``

    ``stale`` มาจาก ``get_etf_daily_eod_snapshot()`` ที่ติดธงให้รายตัวแล้ว
    (AUDIT_2026-08-06 ข้อ H10) — เดิมโค้ดนี้เชื่อ snapshot ก่อนเสมอ ราคาของวันเก่า
    จึงถูกพิมพ์เป็น "วันนี้ +0.00% 🟢"
    """
    snap = snapshot.get(ticker) if isinstance(snapshot, dict) else None
    snap_price = snap_chg = None
    snap_date = snap_reason = snap_error = None
    snap_stale = False
    if isinstance(snap, dict):
        snap_price = _as_float(snap.get("price"))
        snap_chg = _as_float(snap.get("change_pct"))
        snap_date = str(snap["date"]) if snap.get("date") else None
        snap_stale = bool(snap.get("stale")) or snap.get("data_ok") is False
        snap_error = snap.get("error")
        snap_reason = snap.get("reason")
        if not snap_stale and not snap_error and snap_price and snap_price > 0 and snap_chg is not None:
            return {
                "price": snap_price,
                "change_pct": snap_chg,
                "date": snap_date,
                "stale": False,
                "note": None,
                "source": "backend",
            }

    y = _yfinance_snapshot(ticker)
    if y["price"] and y["price"] > 0:
        return {
            "price": y["price"],
            "change_pct": y["change_pct"],
            "date": y["date"],
            "stale": False,
            "note": None if y["change_pct"] is not None else "ดึง %เปลี่ยนแปลงไม่ได้",
            "source": "yfinance",
        }

    if snap_price and snap_price > 0:
        # ราคายังจริง แต่เป็นของวันเก่า — บอกไปตามนั้น ห้ามพิมพ์เป็น %ของวันนี้
        # เหตุผลเต็มยาวเกินหนึ่งบรรทัดของ Discord code block → ลงที่ log แทน
        if snap_reason:
            print(f"{ticker} ข้อมูลค้าง: {snap_reason}")
        return {
            "price": snap_price,
            "change_pct": None,
            "date": snap_date,
            "stale": True,
            "note": "ข้อมูลค้าง ไม่ใช่ราคาของวันนี้",
            "source": "backend-stale",
        }

    fallback_price = _as_float((prices or {}).get(ticker))
    if fallback_price and fallback_price > 0:
        return {
            "price": fallback_price,
            "change_pct": None,
            "date": None,
            "stale": False,
            "note": "ดึง %เปลี่ยนแปลงไม่ได้",
            "source": "backend-prices",
        }

    return {
        "price": None,
        "change_pct": None,
        "date": snap_date,
        "stale": snap_stale,
        "note": str(snap_error) if snap_error else "ดึงราคาไม่ได้",
        "source": None,
    }


def _alert_status_line() -> str:
    """นับ alert จริงจาก storage — เดิม hardcode '0 items' ทำให้เข้าใจผิด (AUDIT.md C6).

    AUDIT_2026-08-06 ข้อ D1.3: เดิม ``list_alerts()`` สร้างไฟล์คลังเปล่าให้เองเมื่อไม่มีไฟล์
    ⇒ ใน GitHub Actions (ที่ ``price_alerts.json`` ถูก gitignore) บรรทัดนี้พิมพ์
    "รอ trigger 0 รายการ" = "คุณไม่มี alert ค้าง" ซึ่งคนละความหมายกับความจริงว่า
    "ที่นี่มองไม่เห็นคลัง alert ของคุณ" — ผลลัพธ์เหมือน hardcode '0 items' ทุกตัวอักษร
    """
    _ensure_repo_root_on_path()
    try:
        from alerts.price_alert import get_store_status

        status = get_store_status()
    except Exception as exc:
        return f"⚠️ Price Alerts: อ่านสถานะไม่ได้ ({exc})"

    if status["status"] == "missing":
        return (
            "⚠️ Price Alerts: สภาพแวดล้อมนี้มองไม่เห็นคลัง alert "
            "(ไฟล์เป็นข้อมูลส่วนตัวและถูก gitignore — CI จึงไม่มี) "
            "— ไม่ได้แปลว่าไม่มี alert ค้าง"
        )
    if status["status"] == "error":
        return f"⚠️ Price Alerts: อ่านคลัง alert ไม่ได้ ({status['error']})"
    return f"⚠️ Price Alerts: รอ trigger {status['pending']} รายการ (trigger แล้ว {status['triggered']})"


def build_discord_message(
    snapshot: dict,
    prices: dict,
    backend_errors: list[str] | None = None,
) -> str:
    """ข้อความสรุปราคารายวัน — แต่ละแถวพก **วันที่ของตัวเอง**.

    เดิมพาดหัวและทุกแถวใช้ ``date`` ของ ticker ตัวแรกที่เจอในผลลัพธ์ ⇒ ตัวที่ข้อมูล
    ค้างลากวันที่ปลอมไปให้เพื่อนทั้งกระดาน (AUDIT_2026-08-06 ข้อ H10)
    """
    run_date = datetime.now().strftime("%d/%m/%Y")
    lines = [
        f"Daily Price Check — {run_date}",
        SEP_LINE,
    ]

    for ticker in TICKERS:
        row = _row_for_ticker(ticker, snapshot, prices)
        price = row["price"]
        change_pct = row["change_pct"]
        date_text = f"  ({row['date']})" if row.get("date") else ""

        if price is None or price <= 0:
            lines.append(f"{ticker:<6} N/A (⚠️ {row['note']}){date_text}")
            continue
        if change_pct is None:
            note = row["note"] or "ดึง %เปลี่ยนแปลงไม่ได้"
            lines.append(f"{ticker:<6} ${price:<8.2f} ⚠️ {note}{date_text}")
            continue

        sign = "+" if change_pct >= 0 else ""
        emoji = "🟢" if change_pct >= 0 else "🔴"
        lines.append(
            f"{ticker:<6} ${price:<8.2f} ({sign}{change_pct:.2f}%) {emoji}{date_text}"
        )

    lines.append(SEP_LINE)
    if backend_errors:
        # ห้ามให้ความล้มเหลวจบที่ log อย่างเดียว — คนอ่าน Discord ต้องรู้ว่าตัวเลข
        # ที่เห็นมาจากเส้นทางสำรอง ไม่ใช่จาก backend (AUDIT_2026-08-06 ข้อ M-CI-1)
        lines.append("⚠️ ดึงจาก backend ไม่ได้ ใช้ค่าสำรองจาก yfinance")
        for detail in backend_errors:
            lines.append(f"   • {detail}")
    lines.append(_alert_status_line())
    return "\n".join(lines)


def _ensure_repo_root_on_path() -> None:
    """ให้ import แพ็กเกจ ``analysis`` ได้เมื่อรัน ``python jobs/daily_check.py`` จาก repo root."""
    root = Path(__file__).resolve().parents[1]
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)


def _build_signals_discord_embed() -> dict:
    """สรุปสัญญาณรายวันจาก **โมเดลในระบบ** (ไม่มีค่าใช้จ่าย).

    เดิม job นี้เรียก AI ทุกวันทำการ (~22 ครั้ง/เดือน) โดยผู้ใช้ไม่ได้สั่ง
    → ตอนนี้ส่งเฉพาะตัวเลข/สัญญาณที่คำนวณในโค้ด ซึ่งเป็นข้อมูลที่ใช้ตัดสินใจจริงอยู่แล้ว
    บทวิเคราะห์ AI ให้กดที่หน้าเว็บเอง (หรือตั้ง VAULTIS_LLM_AUTO=1)
    """
    _ensure_repo_root_on_path()
    try:
        from analysis.financial_model import build_etf_scores

        scores = build_etf_scores(TICKERS)
        lines = []
        for row in sorted(scores, key=lambda r: r.get("total_pct") or -1, reverse=True):
            if not row.get("data_ok", True):
                lines.append(f"{row['ticker']:<5} ⚠️ ดึงข้อมูลไม่ได้")
                continue
            lines.append(
                f"{row['ticker']:<5} คะแนน {row['total_pct']:>5.1f}%  RSI {row['rsi']:>5.1f}  "
                f"{row['signal']} — {row.get('technical_signal_th', '')}"
            )
        value = "\n".join(lines) or "(ไม่มีข้อมูล)"
    except Exception as exc:
        value = f"(คำนวณสัญญาณไม่สำเร็จ: {exc})"

    if len(value) > 1024:
        value = value[:1021] + "..."

    return {
        "color": 0x5865F2,
        "fields": [{"name": "📊 สัญญาณจากโมเดล (ไม่ใช้ AI — ไม่มีค่าใช้จ่าย)", "value": value}],
    }


def run():
    print("Starting daily check...")
    print(f"Backend: {BACKEND_URL}")
    print(f"Webhook: {'OK' if DISCORD_WEBHOOK_URL else 'MISSING'}")

    snapshot, snapshot_error = fetch_daily_snapshot_from_backend()
    prices, prices_error = fetch_prices_from_backend()
    backend_errors = [e for e in (snapshot_error, prices_error) if e]

    # ไม่มีลูปเติมราคาจาก ``fast_info`` อีกแล้ว — ``_row_for_ticker`` ตกไปเส้นทาง
    # แท่งปิด 2 แท่งของ yfinance เองรายตัว ซึ่งเป็นฐานเดียวกับ backend
    message = build_discord_message(snapshot, prices, backend_errors=backend_errors)
    print(f"\nMessage:\n{message}")

    if DISCORD_WEBHOOK_URL:
        payload = {"content": f"```\n{message}\n```"}
        payload["embeds"] = [_build_signals_discord_embed()]
        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
            print(f"Discord: {r.status_code}")
        except requests.RequestException as e:
            print(f"Discord send failed: {e}")
    else:
        print("No Discord webhook URL")


if __name__ == "__main__":
    run()
