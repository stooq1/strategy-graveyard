"""Ансамбль со спутниковыми детекторами на крипте (дневки).

Спутники: BTC и ETH — лидеры, альты на них оглядываются.
  btc.v <- спутник eth.v;  eth.v <- btc.v;  альты <- btc.v (fair) + eth.v (lead)

Запуск:  python run_satellites.py
"""
import pandas as pd

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.optimize import ParamGrid
from trading_analyzer.risk import RiskParams
from trading_analyzer.walkforward import WalkForward
from trading_analyzer.strategies import EnsembleStrategy
from trading_analyzer.detectors import (
    RoundLevelDetector, DivergenceDetector, ExhaustionDetector,
    FairPriceDetector, LeadLagDetector, CorrBreakDetector,
)

TIMEFRAME = "d"
START_DATE = "2020-01-01"
# target: (fair-спутник, lead-лидер)
SATELLITES = {
    "btc.v": ("eth.v", "eth.v"),
    "eth.v": ("btc.v", "btc.v"),
    "ada.v": ("btc.v", "eth.v"),
    "sol.v": ("btc.v", "eth.v"),
}

RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=3.0, risk_per_trade=0.02)


def base_detectors():
    return [
        RoundLevelDetector(weight=1.0),
        DivergenceDetector(weight=1.5),
        ExhaustionDetector(weight=1.0),
    ]


def satellite_detectors(closes, fair_sym, lead_sym):
    return [
        FairPriceDetector(closes[fair_sym], fair_sym, weight=1.2, lookback=50),
        LeadLagDetector(closes[lead_sym], lead_sym, weight=1.0,
                        lookback=10, threshold=0.05),
        CorrBreakDetector(closes[fair_sym], fair_sym, weight=0.7, window=30),
    ]


def main():
    data = StooqData("stooq_data")
    engine = BacktestEngine(initial_capital=10_000, commission=0.001)

    closes = {s: data.load(s, TIMEFRAME)["Close"].loc[START_DATE:]
              for s in ["btc.v", "eth.v"]}

    rows = []
    for symbol, (fair_sym, lead_sym) in SATELLITES.items():
        df = data.load(symbol, TIMEFRAME).loc[START_DATE:]
        sats = satellite_detectors(closes, fair_sym, lead_sym)

        print(f"\n=== {symbol} (fair<-{fair_sym}, lead<-{lead_sym}) ===")
        variants = {}
        for d in sats:  # спутниковые соло
            variants[d.name] = EnsembleStrategy([d], entry_threshold=0.5,
                                                exit_threshold=-0.5)
        variants["base_ens"] = EnsembleStrategy(
            base_detectors(), entry_threshold=1.0, exit_threshold=-0.8,
            cooldown_bars=5)
        variants["full_ens"] = EnsembleStrategy(
            base_detectors() + sats, entry_threshold=1.3,
            exit_threshold=-1.0, cooldown_bars=5)

        for label, strat in variants.items():
            m = engine.run(strat, df, symbol, risk=RISK).metrics
            print(f"  {label:<12}"
                  f"  return {m['total_return_%']:>8.1f}%"
                  f"  sharpe {m['sharpe']:>5.2f}"
                  f"  maxDD {m['max_drawdown_%']:>6.1f}%"
                  f"  trades {m['trades']:>4}"
                  f"  win {m['win_rate_%']:>5.1f}%")
            rows.append({"symbol": symbol, "variant": label, **m})

    pd.DataFrame(rows).to_csv("satellite_results.csv", index=False)

    # Walk-forward порогов полного ансамбля на ETH (исторически лучший)
    print("\n=== Walk-forward полного ансамбля на eth.v ===")
    eth = data.load("eth.v", TIMEFRAME).loc[START_DATE:]
    wf = WalkForward(engine, metric="sharpe", risk=RISK)
    wf.run("eth.v", eth, ParamGrid(
        EnsembleStrategy,
        {"entry_threshold": [1.0, 1.3, 1.6],
         "exit_threshold": [-0.7, -1.0]},
        fixed_params={"detectors": base_detectors()
                      + satellite_detectors(closes, "btc.v", "btc.v"),
                      "cooldown_bars": 5},
    ), train_bars=500, test_bars=125)

    print("\nСохранено: satellite_results.csv")


if __name__ == "__main__":
    main()
