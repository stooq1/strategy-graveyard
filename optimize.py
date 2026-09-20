"""Запуск grid search: подбор параметров стратегий с out-of-sample проверкой.

Запуск:  python optimize.py
Результаты — в консоль и optimization_results.csv.
"""
from trading_analyzer import StooqData
from trading_analyzer.optimize import GridSearch, ParamGrid
from trading_analyzer.risk import RiskParams
from trading_analyzer.strategies import (
    RoundNumberStrategy,
    CorrelationStrategy,
    DivergenceStrategy,
)

TIMEFRAME = "d"
START_DATE = "2020-01-01"

# Общий риск-менеджмент для всех прогонов (None — отключить)
RISK = RiskParams(stop_loss_pct=0.05, trailing_stop_pct=0.10)


def main():
    data = StooqData("stooq_data")
    search = GridSearch(metric="sharpe", test_frac=0.3, risk=RISK)

    btc = data.load("btc.v", TIMEFRAME).loc[START_DATE:]
    aapl = data.load("aapl.us", TIMEFRAME).loc[START_DATE:]
    eth_close = data.load("eth.v", TIMEFRAME)["Close"].loc[START_DATE:]

    # Круглые уровни на BTC
    search.run("btc.v", btc, ParamGrid(
        RoundNumberStrategy,
        {"level_step_frac": [0.02, 0.05, 0.10],
         "touch_threshold": [0.001, 0.003, 0.005, 0.01]},
    ))

    # Дивергенции на AAPL (ослабляем пороги — нужно больше сделок)
    search.run("aapl.us", aapl, ParamGrid(
        DivergenceStrategy,
        {"rsi_period": [7, 14, 21],
         "pivot_order": [3, 5, 7],
         "max_pivot_gap": [30, 60, 90]},
    ))

    # Корреляционная BTC/ETH
    search.run("btc.v", btc, ParamGrid(
        CorrelationStrategy,
        {"window": [20, 30, 50],
         "entry_z": [1.5, 2.0, 2.5],
         "exit_z": [0.0, 0.5, 1.0],
         "min_corr": [0.5, 0.7]},
        fixed_params={"pair_close": eth_close, "pair_name": "eth.v"},
    ))

    search.save("optimization_results.csv")


if __name__ == "__main__":
    main()
