"""Integration test for BacktestEngine."""

from analysis.backtest_engine import BacktestEngine


def test_backtest():
    engine = BacktestEngine()

    print("Running BacktestEngine for VOO (2022-2024)...")
    result = engine.run("VOO", "2022-01-01", "2024-01-01")

    print(f"symbol          : {result['symbol']}")
    print(f"total_return    : {result['total_return']:.4f}%")
    print(f"sharpe_ratio    : {result['sharpe_ratio']:.4f}")
    print(f"max_drawdown    : {result['max_drawdown']:.4f}%")
    print(f"win_rate        : {result['win_rate']:.4f}%")
    print(f"num_trades      : {result['num_trades']}")
    print(f"benchmark_return: {result['benchmark_return']:.4f}%")
    print(f"outperformed    : {result['outperformed']}")

    print("\nRunning optimize() for VOO...")
    opt = engine.optimize("VOO", "2022-01-01", "2024-01-01")
    print(f"best_params     : {opt['best_params']}")
    print(f"best_sharpe     : {opt['best_sharpe']}")

    assert result["num_trades"] >= 0
    assert result["total_return"] is not None
    assert result["sharpe_ratio"] is not None
    assert result["max_drawdown"] is not None
    assert result["win_rate"] is not None
    assert result["benchmark_return"] is not None
    assert opt["best_params"] is not None

    print("\n✅ passed")


if __name__ == "__main__":
    test_backtest()
