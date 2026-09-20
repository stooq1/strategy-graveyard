"""Сравнение фиксированных стопов с ATR-адаптивными.

ATR-стоп масштабируется с волатильностью: на спокойном рынке он узкий,
в шторм — широкий, поэтому реже выбивается шумом.

Запуск:  python compare_stops.py
"""
import pandas as pd

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.risk import RiskParams
from trading_analyzer.strategies import RoundNumberStrategy, DivergenceStrategy

TIMEFRAME = "d"
START_DATE = "2020-01-01"

STOP_VARIANTS = {
    "без риска": None,
    "фикс. sl 5%": RiskParams(stop_loss_pct=0.05),
    "ATR 2x": RiskParams(stop_loss_pct=None, atr_stop_mult=2.0),
    "ATR 3x": RiskParams(stop_loss_pct=None, atr_stop_mult=3.0),
    "ATR 3x + tp 6x": RiskParams(stop_loss_pct=None,
                                 atr_stop_mult=3.0, atr_take_mult=6.0),
    "ATR 3x + rpt 2%": RiskParams(stop_loss_pct=None,
                                  atr_stop_mult=3.0, risk_per_trade=0.02),
}


def main():
    data = StooqData("stooq_data")
    engine = BacktestEngine(initial_capital=10_000, commission=0.001)

    cases = [
        ("btc.v", RoundNumberStrategy(level_step_frac=0.05, touch_threshold=0.003)),
        ("aapl.us", DivergenceStrategy(rsi_period=14, pivot_order=5, max_pivot_gap=60)),
    ]

    rows = []
    for symbol, strategy in cases:
        df = data.load(symbol, TIMEFRAME).loc[START_DATE:]
        print(f"\n=== {strategy.name} на {symbol} ===")
        for label, risk in STOP_VARIANTS.items():
            m = engine.run(strategy, df, symbol, risk=risk).metrics
            print(f"  {label:<17}"
                  f"  return {m['total_return_%']:>8.1f}%"
                  f"  sharpe {m['sharpe']:>5.2f}"
                  f"  maxDD {m['max_drawdown_%']:>6.1f}%"
                  f"  trades {m['trades']:>4}"
                  f"  win {m['win_rate_%']:>5.1f}%")
            rows.append({"strategy": strategy.name, "symbol": symbol,
                         "stops": label, **m})

    pd.DataFrame(rows).to_csv("stops_comparison.csv", index=False)
    print("\nСохранено: stops_comparison.csv")


if __name__ == "__main__":
    main()
