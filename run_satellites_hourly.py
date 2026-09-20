"""Спутниковые детекторы на ЧАСОВОЙ крипте (24/7, ~18k баров за 2 года).

Параметры адаптированы под часовики: окна короче, пороги ниже.

Запуск:  python run_satellites_hourly.py
"""
import pandas as pd

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.optimize import ParamGrid
from trading_analyzer.risk import RiskParams
from trading_analyzer.walkforward import WalkForward
from trading_analyzer.strategies import EnsembleStrategy
from trading_analyzer.detectors import (
    RoundLevelDetector, DivergenceDetector, ExhaustionDetector,
    FairPriceDetector, LeadLagDetector,
)

TIMEFRAME = "h"
PAIRS = {"btc.v": "eth.v", "eth.v": "btc.v"}

RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=3.0, risk_per_trade=0.02)


def detectors_for(sat_close, sat_name):
    return [
        RoundLevelDetector(weight=1.0, touch_threshold=0.002),
        DivergenceDetector(weight=1.5, pivot_order=5, max_pivot_gap=40),
        ExhaustionDetector(weight=1.0, lookback=24, roc_threshold=0.04),
        FairPriceDetector(sat_close, sat_name, weight=1.2, lookback=72),
        LeadLagDetector(sat_close, sat_name, weight=1.0,
                        lookback=12, threshold=0.02),
    ]


def main():
    data = StooqData("stooq_data")
    engine = BacktestEngine(initial_capital=10_000, commission=0.001)
    closes = {s: data.load(s, TIMEFRAME)["Close"] for s in PAIRS}

    rows = []
    for symbol, sat in PAIRS.items():
        df = data.load(symbol, TIMEFRAME)
        dets = detectors_for(closes[sat], sat)

        print(f"\n=== {symbol} hourly ({len(df)} баров, спутник {sat}) ===")
        variants = {d.name: EnsembleStrategy([d], entry_threshold=0.5,
                                             exit_threshold=-0.5)
                    for d in dets}
        variants["ensemble"] = EnsembleStrategy(
            dets, entry_threshold=1.3, exit_threshold=-1.0, cooldown_bars=12)

        for label, strat in variants.items():
            m = engine.run(strat, df, symbol, risk=RISK).metrics
            print(f"  {label:<15}"
                  f"  return {m['total_return_%']:>7.1f}%"
                  f"  sharpe {m['sharpe']:>5.2f}"
                  f"  maxDD {m['max_drawdown_%']:>6.1f}%"
                  f"  trades {m['trades']:>4}"
                  f"  win {m['win_rate_%']:>5.1f}%")
            rows.append({"symbol": symbol, "variant": label, **m})

    pd.DataFrame(rows).to_csv("satellite_hourly_results.csv", index=False)

    # Walk-forward порогов ансамбля на btc.v hourly
    print("\n=== Walk-forward ансамбля на btc.v hourly ===")
    btc = data.load("btc.v", TIMEFRAME)
    wf = WalkForward(engine, metric="sharpe", risk=RISK)
    wf.run("btc.v", btc, ParamGrid(
        EnsembleStrategy,
        {"entry_threshold": [1.0, 1.3, 1.6],
         "exit_threshold": [-0.7, -1.0]},
        fixed_params={"detectors": detectors_for(closes["eth.v"], "eth.v"),
                      "cooldown_bars": 12},
    ), train_bars=3000, test_bars=750)

    print("\nСохранено: satellite_hourly_results.csv")


if __name__ == "__main__":
    main()
