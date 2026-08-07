"""Integration test for BacktestEngine (ต่อเน็ตจริง).

ติด ``@pytest.mark.network`` เพราะ ``engine.run()`` ดึงราคาจริงจาก yfinance —
ถูกกันออกจากการรันปกติ (``addopts = -m "not network"`` ใน pytest.ini)
เรียกกลับมาด้วย ``pytest -m network``  (AUDIT_2026-08-06 ข้อ 0-B)
"""

import pytest

from analysis.backtest_engine import BacktestEngine

pytestmark = pytest.mark.network


_METRICS = ("total_return", "sharpe_ratio", "max_drawdown", "win_rate")


def test_backtest():
    engine = BacktestEngine()

    print("Running BacktestEngine for VOO (2022-2024)...")
    result = engine.run("VOO", "2022-01-01", "2024-01-01")

    for key in ("symbol", *_METRICS, "num_trades", "benchmark_return", "outperformed", "detail"):
        print(f"{key:16}: {result[key]}")

    print("\nRunning optimize() for VOO...")
    opt = engine.optimize("VOO", "2022-01-01", "2024-01-01")
    print(f"best_params     : {opt['best_params']}")
    print(f"train_sharpe    : {opt['train_sharpe']}")
    print(f"test_sharpe     : {opt['test_sharpe']}")

    assert result["num_trades"] >= 0
    assert result["benchmark_return"] is not None

    # AUDIT_2026-08-06 B3.1: เดิมไฟล์นี้ยืนยันว่าทุกช่อง ``is not None`` เสมอ ซึ่งเป็นจริง
    # ได้เพราะ engine ยัด 0.0 ให้ช่วงที่กลยุทธ์ไม่เคยเทรด — ตอนนี้ 0 เทรด = ``None``
    # (ไม่นิยาม) เงื่อนไขจึงตรึงตาม num_trades แทน ไม่ใช่ผ่อนเทสต์ให้หลวมลง
    if result["num_trades"] > 0:
        assert all(result[key] is not None for key in _METRICS), result
        assert isinstance(result["outperformed"], bool)
    else:
        assert all(result[key] is None for key in _METRICS), result
        assert result["outperformed"] is None, "ไม่มีเทรด = เทียบกับ buy & hold ไม่ได้"
        assert result["detail"], "ต้องบอกผู้ใช้ว่าทำไมทุกช่องว่าง"

    assert opt["best_params"] is not None
    # AUDIT M2: optimize รายงาน train/test แยกกัน — ห้ามมีคีย์ in-sample เดิม
    assert "best_sharpe" not in opt
    # B3.3: train_sharpe ติดลบต้องรายงานติดลบ (เดิมถูกปัดพื้นเป็น 0.0) และ
    # ``None`` = ไม่มีคอมโบไหนส่งสัญญาณเลย ซึ่งเป็นคำตอบที่ถูกต้อง ไม่ใช่ 0
    if opt["best_params"]:
        assert opt["train_sharpe"] is not None
    else:
        assert opt["train_sharpe"] is None and opt["test_sharpe"] is None

    print("\n✅ passed")


if __name__ == "__main__":
    test_backtest()
