"""Сравнение стратегий с риск-менеджментом и без.

Запуск:  python compare_risk.py
"""
import pandas as pd

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.risk import RiskParams
from trading_analyzer.strategies import (
    RoundNumberStrategy,
    CorrelationStrategy,
    DivergenceStrategy,
)

TIMEFRAME = "d"
START_DATE = "2020-01-01"

RISK_VARIANTS = {
    "без риска": None,
    "sl 5%": RiskParams(stop_loss_pct=0.05),
    "sl 5% + trail 10%": RiskParams(stop_loss_pct=0.05, trailing_stop_pct=0.10),
    "sl 5% + tp 15%": RiskParams(stop_loss_pct=0.05, take_profit_pct=0.15),
}


def main():
    data = StooqData("stooq_data")
    engine = BacktestEngine(initial_capital=10_000, commission=0.001)

    cases = [
        ("btc.v", RoundNumberStrategy(level_step_frac=0.05, touch_threshold=0.003)),
        ("aapl.us", DivergenceStrategy(rsi_period=14, pivot_order=5, max_pivot_gap=60)),
        ("btc.v", CorrelationStrategy(
            data.load("eth.v", TIMEFRAME)["Close"].loc[START_DATE:],
            pair_name="eth.v", window=30, entry_z=2.0, exit_z=0.0, min_corr=0.7)),
    ]

    rows = []
    for symbol, strategy in cases:
        df = data.load(symbol, TIMEFRAME).loc[START_DATE:]
        print(f"\n=== {strategy.name} на {symbol} ===")
        for label, risk in RISK_VARIANTS.items():
            m = engine.run(strategy, df, symbol, risk=risk).metrics
            print(f"  {label:<18}"
                  f"  return {m['total_return_%']:>8.1f}%"
                  f"  sharpe {m['sharpe']:>5.2f}"
                  f"  maxDD {m['max_drawdown_%']:>6.1f}%"
                  f"  trades {m['trades']:>4}"
                  f"  win {m['win_rate_%']:>5.1f}%")
            rows.append({"strategy": strategy.name, "symbol": symbol,
                         "risk": label, **m})

    pd.DataFrame(rows).to_csv("risk_comparison.csv", index=False)
    print("\nСохранено: risk_comparison.csv")


if __name__ == "__main__":
    main()
