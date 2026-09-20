"""Точка входа: бэктест стратегий на данных из локальных архивов Stooq.

Запуск:  python main.py
Результаты — в консоль и backtest_results.csv.
"""
import pandas as pd

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.strategies import (
    RoundNumberStrategy,
    CorrelationStrategy,
    DivergenceStrategy,
)

# --- настройки --------------------------------------------------------------
SYMBOLS = ["btc.v", "eth.v", "aapl.us", "spy.us"]
TIMEFRAME = "d"          # 'd' | 'h' | '5'
START_DATE = "2020-01-01"

# Парный инструмент для корреляционной стратегии
CORRELATION_PAIRS = {
    "btc.v": "eth.v",
    "eth.v": "btc.v",
    "aapl.us": "spy.us",
    "spy.us": "aapl.us",
}
# -----------------------------------------------------------------------------


def build_strategies(data: StooqData, symbol: str):
    """Набор стратегий для инструмента. Новые добавлять сюда."""
    strategies = [
        RoundNumberStrategy(level_step_frac=0.05, touch_threshold=0.003),
        DivergenceStrategy(rsi_period=14, pivot_order=5, max_pivot_gap=60),
    ]
    pair = CORRELATION_PAIRS.get(symbol)
    if pair:
        try:
            pair_close = data.load(pair, TIMEFRAME)["Close"].loc[START_DATE:]
            strategies.append(CorrelationStrategy(
                pair_close, pair_name=pair,
                window=30, entry_z=2.0, exit_z=0.0, min_corr=0.7))
        except KeyError:
            print(f"  (пара {pair} не найдена — корреляционная пропущена)")
    return strategies


def main():
    data = StooqData("stooq_data")
    engine = BacktestEngine(initial_capital=10_000, commission=0.001)

    rows = []
    for symbol in SYMBOLS:
        try:
            df = data.load(symbol, TIMEFRAME).loc[START_DATE:]
        except KeyError as e:
            print(f"{symbol}: {e}")
            continue
        if len(df) < 50:
            print(f"{symbol}: мало данных ({len(df)} баров), пропуск")
            continue

        print(f"\n=== {symbol} [{df.index[0].date()} – {df.index[-1].date()}] ===")
        for strategy in build_strategies(data, symbol):
            result = engine.run(strategy, df, symbol=symbol)
            m = result.metrics
            print(f"  {strategy.name:<16}"
                  f"  return {m['total_return_%']:>8.1f}%"
                  f"  (b&h {m['buy_hold_%']:>8.1f}%)"
                  f"  sharpe {m['sharpe']:>5.2f}"
                  f"  maxDD {m['max_drawdown_%']:>6.1f}%"
                  f"  trades {m['trades']:>4}"
                  f"  win {m['win_rate_%']:>5.1f}%")
            rows.append({"strategy": result.strategy, "symbol": symbol, **m})

    if rows:
        results = pd.DataFrame(rows)
        results.to_csv("backtest_results.csv", index=False)
        print("\nСохранено: backtest_results.csv")


if __name__ == "__main__":
    main()
