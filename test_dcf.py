from analysis.financial_model import run_full_analysis

result = run_full_analysis(budget_thb=5000)

for ticker, data in result["analysis"].items():
    dcf = data["dcf"]
    print(f"\n{ticker}:")
    print(f"  Score:    {data['total_score']}/100 — {data['signal']}")
    print(f"  RSI:      {data['rsi']}")
    print(f"  Price:    ${dcf['current_price']}")
    print(f"  DCF Value:${dcf['intrinsic_value']}")
    print(f"  MoS:      {dcf['margin_of_safety']}%")
    print(f"  Signal:   {dcf['signal']}")

print("\n=== Allocation ===")
for ticker, alloc in result["allocation"].items():
    print(f"{ticker}: {alloc['amount_thb']} THB ({alloc['percent']}%)")
