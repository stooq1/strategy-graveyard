"""Портфельный walk-forward: дивергенции на корзине крупных акций США.

Проверяем, есть ли у паттерна реальный край, или Sharpe 1.38 на AAPL
был удачей одного тикера.

Запуск:  python portfolio_walkforward.py
"""
import pandas as pd

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.optimize import ParamGrid
from trading_analyzer.portfolio import PortfolioWalkForward
from trading_analyzer.risk import RiskParams
from trading_analyzer.strategies import DivergenceStrategy

TIMEFRAME = "d"
START_DATE = "2018-01-01"

# Тикеры проверены по архиву (brk.b в Stooq — это brk-b.us)
SYMBOLS = [
    "aapl.us", "msft.us", "googl.us", "amzn.us", "meta.us",
    "tsla.us", "brk-b.us", "nvda.us", "jpm.us", "jnj.us",
    "v.us", "pg.us", "unh.us", "hd.us", "dis.us",
    "ma.us", "bac.us", "crm.us", "nflx.us", "adbe.us",
]

# Лучший профиль риска из compare_stops
RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=3.0,
                  atr_take_mult=6.0, risk_per_trade=0.02)

# Облегчённая сетка: pivot_order=3 даёт больше сигналов
GRID = ParamGrid(
    DivergenceStrategy,
    {"rsi_period": [7, 14],
     "pivot_order": [3, 5],
     "max_pivot_gap": [30, 45]},
)


def main():
    data = StooqData("stooq_data")
    engine = BacktestEngine(initial_capital=10_000, commission=0.001)
    portfolio = PortfolioWalkForward(engine, metric="sharpe", risk=RISK)

    datasets = {}
    for s in SYMBOLS:
        try:
            datasets[s] = data.load(s, TIMEFRAME).loc[START_DATE:]
        except KeyError:
            print(f"{s}: не найден, пропуск")

    print(f"Портфельный walk-forward: {len(datasets)} акций, "
          f"train 400 / test 100 баров\n")
    res = portfolio.run(datasets, GRID, train_bars=400, test_bars=100)

    print("\n" + "=" * 64)
    print("ПОРТФЕЛЬ (равные веса, ежедневная ребалансировка)")
    print("=" * 64)
    for k, v in res.summary.items():
        print(f"  {k:<26} {v if not isinstance(v, float) else round(v, 2)}")

    res.details.to_csv("portfolio_details.csv", index=False)
    pd.DataFrame([res.summary]).to_csv("portfolio_summary.csv", index=False)
    print("\nСохранено: portfolio_summary.csv, portfolio_details.csv")


if __name__ == "__main__":
    main()
