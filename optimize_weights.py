"""Walk-forward подбор ВЕСОВ детекторов ансамбля (ETH, дневки).

Гипотеза: данные сами дадут lead_lag большой вес на мажорах,
а мёртвым детекторам — ноль.

Запуск:  python optimize_weights.py
"""
import pandas as pd

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.risk import RiskParams
from trading_analyzer.walkforward import WalkForward
from trading_analyzer.detectors import (
    RoundLevelDetector, DivergenceDetector, ExhaustionDetector,
    FairPriceDetector, LeadLagDetector,
)

TIMEFRAME = "d"
START_DATE = "2020-01-01"
SYMBOL = "eth.v"
SAT = "btc.v"

RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=3.0, risk_per_trade=0.02)


def main():
    data = StooqData("stooq_data")
    engine = BacktestEngine(initial_capital=10_000, commission=0.001)
    df = data.load(SYMBOL, TIMEFRAME).loc[START_DATE:]
    sat_close = data.load(SAT, TIMEFRAME)["Close"].loc[START_DATE:]

    detectors = [
        RoundLevelDetector(),
        DivergenceDetector(),
        ExhaustionDetector(),
        FairPriceDetector(sat_close, SAT, lookback=50),
        LeadLagDetector(sat_close, SAT, lookback=10, threshold=0.05),
    ]
    # corr_break исключён: 0-1 сделка на всю историю, шум в переборе
    weight_options = [
        [0.0, 1.0],        # round_levels: вкл/выкл
        [0.0, 1.0],        # divergence
        [0.0, 1.0],        # exhaustion
        [0.0, 1.0],        # fair_price
        [0.0, 1.0, 2.0],   # lead_lag: может доминировать
    ]

    wf = WalkForward(engine, metric="sharpe", risk=RISK)
    print(f"Подбор весов на {SYMBOL} (спутник {SAT}): "
          f"{2**4 * 3} комбинаций весов x пороги\n")
    res = wf.run_weights(SYMBOL, df, detectors, weight_options,
                         entry_options=[0.5, 1.0, 1.5],
                         exit_options=[-0.5, -1.0],
                         train_bars=500, test_bars=125)

    res["windows"].to_csv("weights_windows.csv", index=False)
    pd.DataFrame([res["summary"]]).to_csv("weights_summary.csv", index=False)

    # Как часто каждый детектор включался
    w_cols = [c for c in res["windows"].columns if c.startswith("w_")]
    print("\nЧастота включения детекторов (доля окон с весом > 0):")
    print((res["windows"][w_cols] > 0).mean().round(2).to_string())
    print("\nСохранено: weights_windows.csv, weights_summary.csv")


if __name__ == "__main__":
    main()
