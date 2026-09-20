"""Ансамбль детекторов на крипте (дневки).

1. Каждый детектор поодиночке (как мини-ансамбль) — видно, кто живой.
2. Полный ансамбль с весами.
3. Walk-forward по порогам входа/выхода — честная OOS-оценка.

Запуск:  python run_ensemble.py
"""
import pandas as pd

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.optimize import ParamGrid
from trading_analyzer.risk import RiskParams
from trading_analyzer.walkforward import WalkForward
from trading_analyzer.strategies import EnsembleStrategy
from trading_analyzer.detectors import (
    RoundLevelDetector,
    DivergenceDetector,
    ExhaustionDetector,
)

TIMEFRAME = "d"
START_DATE = "2020-01-01"
SYMBOLS = ["btc.v", "eth.v", "sol.v", "ada.v"]

RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=3.0, risk_per_trade=0.02)


def make_detectors():
    return [
        RoundLevelDetector(weight=1.0),
        DivergenceDetector(weight=1.5),   # на дневках показал лучший край
        ExhaustionDetector(weight=1.0),
    ]


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

        print(f"\n=== {symbol} [{df.index[0].date()} – {df.index[-1].date()}] ===")

        # Детекторы поодиночке + полный ансамбль
        variants = {d.name: EnsembleStrategy([d], entry_threshold=0.5,
                                             exit_threshold=-0.5)
                    for d in make_detectors()}
        variants["ensemble"] = EnsembleStrategy(
            make_detectors(), entry_threshold=1.0, exit_threshold=-0.8,
            cooldown_bars=5)

        for label, strat in variants.items():
            m = engine.run(strat, df, symbol, risk=RISK).metrics
            print(f"  {label:<15}"
                  f"  return {m['total_return_%']:>8.1f}%"
                  f"  (b&h {m['buy_hold_%']:>7.1f}%)"
                  f"  sharpe {m['sharpe']:>5.2f}"
                  f"  maxDD {m['max_drawdown_%']:>6.1f}%"
                  f"  trades {m['trades']:>4}"
                  f"  win {m['win_rate_%']:>5.1f}%")
            rows.append({"symbol": symbol, "variant": label, **m})

    pd.DataFrame(rows).to_csv("ensemble_results.csv", index=False)

    # Walk-forward порогов ансамбля на BTC
    print("\n=== Walk-forward порогов ансамбля на btc.v ===")
    btc = data.load("btc.v", TIMEFRAME).loc[START_DATE:]
    wf = WalkForward(engine, metric="sharpe", risk=RISK)
    res = wf.run("btc.v", btc, ParamGrid(
        EnsembleStrategy,
        {"entry_threshold": [0.8, 1.0, 1.3],
         "exit_threshold": [-0.5, -0.8, -1.1]},
        fixed_params={"detectors": make_detectors(), "cooldown_bars": 5},
    ), train_bars=500, test_bars=125)

    print("\nСохранено: ensemble_results.csv")


if __name__ == "__main__":
    main()
