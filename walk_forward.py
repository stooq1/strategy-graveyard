"""Запуск walk-forward оптимизации.

Запуск:  python walk_forward.py
Сводка — в консоль и walk_forward_results.csv.
"""
import pandas as pd

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.optimize import ParamGrid
from trading_analyzer.risk import RiskParams
from trading_analyzer.walkforward import WalkForward
from trading_analyzer.strategies import (
    RoundNumberStrategy,
    DivergenceStrategy,
)

TIMEFRAME = "d"
START_DATE = "2020-01-01"
RISK = RiskParams(stop_loss_pct=0.05, trailing_stop_pct=0.10)


def main():
    data = StooqData("stooq_data")
    wf = WalkForward(BacktestEngine(initial_capital=10_000, commission=0.001),
                     metric="sharpe", risk=RISK)

    summaries = []

    print("\n=== Walk-forward: round_numbers на btc.v ===")
    btc = data.load("btc.v", TIMEFRAME).loc[START_DATE:]
    res = wf.run("btc.v", btc, ParamGrid(
        RoundNumberStrategy,
        {"level_step_frac": [0.02, 0.05, 0.10],
         "touch_threshold": [0.001, 0.003, 0.01]},
    ), train_bars=500, test_bars=125)
    summaries.append(res["summary"])

    print("\n=== Walk-forward: rsi_divergence на aapl.us ===")
    aapl = data.load("aapl.us", TIMEFRAME).loc[START_DATE:]
    res = wf.run("aapl.us", aapl, ParamGrid(
        DivergenceStrategy,
        {"rsi_period": [7, 14, 21],
         "pivot_order": [3, 5],
         "max_pivot_gap": [30, 60]},
    ), train_bars=400, test_bars=100)
    summaries.append(res["summary"])

    df = pd.DataFrame(summaries)
    df.to_csv("walk_forward_results.csv", index=False)
    print("\nСводка:")
    print(df.round(2).to_string(index=False))
    print("\nСохранено: walk_forward_results.csv")


if __name__ == "__main__":
    main()
