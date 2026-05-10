"""Integration test for PriceForecaster and WalkForwardBacktester."""

from analysis.forecaster import PriceForecaster
from analysis.backtester import WalkForwardBacktester


def test_forecast():
    print("Running PriceForecaster for VOO...")
    forecaster = PriceForecaster()
    result = forecaster.forecast("VOO", 30)

    print(f"last_price  : {result['last_price']}")
    print(f"trend       : {result['trend']}")
    print(f"trend_pct   : {result['trend_pct']}%")
    print("First 5 predictions:")
    for p in result["predictions"][:5]:
        print(f"  {p['date']}: yhat={p['yhat']}  [{p['yhat_lower']} – {p['yhat_upper']}]")

    print("\nRunning WalkForwardBacktester for VOO...")
    bt = WalkForwardBacktester()
    bt_result = bt.run("VOO")

    print(f"mae         : {bt_result['mae']}")
    print(f"rmse        : {bt_result['rmse']}")
    print(f"mape        : {bt_result['mape']}")
    print(f"accuracy_pct: {bt_result['accuracy_pct']}%")
    print(f"n_folds     : {bt_result['n_folds']}")

    assert result["symbol"] is not None
    assert result["last_price"] is not None
    assert result["trend"] is not None
    assert result["trend_pct"] is not None
    assert result["predictions"] is not None
    assert len(result["predictions"]) == 30
    assert bt_result["mae"] is not None
    assert bt_result["rmse"] is not None
    assert bt_result["mape"] is not None

    print("\n✅ passed")


if __name__ == "__main__":
    test_forecast()
