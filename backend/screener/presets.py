from backend.screener.models import ScreenerRule, ScreenerPreset


PRESETS = {
    "oversold_momentum": ScreenerPreset(
        name="oversold_momentum",
        description="RSI oversold + MACD bullish cross + above MA200",
        logic="AND",
        rules=[
            ScreenerRule("rsi", "lt", 35, "RSI < 35 (oversold)"),
            ScreenerRule("macd_cross", "cross_up", None, "MACD bullish crossover"),
            ScreenerRule("price_vs_ma200", "gt", None, "Price above MA200"),
        ],
    ),
    "golden_cross_alert": ScreenerPreset(
        name="golden_cross_alert",
        description="Golden Cross occurred in last 3 days",
        logic="AND",
        rules=[
            ScreenerRule("golden_cross", "cross_up", 3, "Golden Cross within 3 days"),
        ],
    ),
    "bb_breakout_watch": ScreenerPreset(
        name="bb_breakout_watch",
        description="BB squeeze + volume spike",
        logic="AND",
        rules=[
            ScreenerRule("bb_squeeze", "squeeze", None, "Bollinger Band squeeze"),
            ScreenerRule("volume_spike", "spike", 2.0, "Volume > 2x MA20"),
        ],
    ),
    "dividend_dip": ScreenerPreset(
        name="dividend_dip",
        description="SCHD/XLV price dropped 5%+ in 10 days",
        logic="AND",
        rules=[
            ScreenerRule("price_drop_pct", "drop_pct", 5.0, "Price dropped 5%+ in 10 days"),
        ],
    ),
    "overbought_warning": ScreenerPreset(
        name="overbought_warning",
        description="RSI overbought + MACD bearish",
        logic="AND",
        rules=[
            ScreenerRule("rsi", "gt", 70, "RSI > 70 (overbought)"),
            ScreenerRule("macd_cross", "cross_down", None, "MACD bearish crossover"),
        ],
    ),
}


def get_preset(name: str) -> ScreenerPreset:
    preset = PRESETS.get(name)
    if preset is None:
        raise ValueError(f"Preset '{name}' not found")
    return preset
