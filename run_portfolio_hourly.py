"""Часовой портфельный walk-forward: дивергенции на корзине акций.

Часовые архивы Stooq покрывают ~2 года (~3600 баров, ~7 баров/день),
поэтому окна короче дневных: train 1400 (~9 мес), test 350 (~2.5 мес)
— получается ~6 непересекающихся OOS-окон на тикер.

Запуск:  python run_portfolio_hourly.py
"""
import pandas as pd

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.optimize import ParamGrid
from trading_analyzer.portfolio import PortfolioWalkForward
from trading_analyzer.risk import RiskParams
from trading_analyzer.strategies import DivergenceStrategy

TIMEFRAME = "h"

SYMBOLS = [
    "aapl.us", "msft.us", "googl.us", "amzn.us", "meta.us",
    "tsla.us", "nvda.us", "jpm.us", "v.us", "unh.us",
    "hd.us", "nflx.us",
]

# На часовиках стопы уже, риск на сделку меньше (сигналов больше)
RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=2.0,
                  atr_take_mult=4.0, risk_per_trade=0.01)

GRID = ParamGrid(
    DivergenceStrategy,
    {"rsi_period": [7, 14],
     "pivot_order": [3, 5],
     "max_pivot_gap": [20, 40]},
)


def main():
    data = StooqData("stooq_data")
    engine = BacktestEngine(initial_capital=10_000, commission=0.001)
    portfolio = PortfolioWalkForward(engine, metric="sharpe", risk=RISK)

    datasets = {}
    for s in SYMBOLS:
        try:
            df = data.load(s, TIMEFRAME)
            datasets[s] = df
        except KeyError:
            print(f"{s}: не найден, пропуск")

    n_bars = min(len(d) for d in datasets.values())
    print(f"Часовой walk-forward: {len(datasets)} акций, ~{n_bars} баров, "
          f"train 1400 / test 350\n")
    res = portfolio.run(datasets, GRID, train_bars=1400, test_bars=350)

    print("\n" + "=" * 64)
    print("ПОРТФЕЛЬ, ЧАСОВОЙ ТАЙМФРЕЙМ")
    print("=" * 64)
    for k, v in res.summary.items():
        print(f"  {k:<26} {v if not isinstance(v, float) else round(v, 2)}")

    res.details.to_csv("portfolio_hourly_details.csv", index=False)
    pd.DataFrame([res.summary]).to_csv("portfolio_hourly_summary.csv", index=False)
    print("\nСохранено: portfolio_hourly_summary.csv, portfolio_hourly_details.csv")


if __name__ == "__main__":
    main()
