"""Тест инверсии: вставать ПРОТИВ логики детекторов (fade).

Идея: детекторы стабильно убыточны на плато при win rate < 50%. Инверсия
делает win rate > 50%. НО инверсия переворачивает только направление P&L,
а НЕ издержки — комиссия платится в обе стороны. Поэтому инверсия выгодна
лишь если |убыток| > 2*издержки, то есть прежде всего на МЕЙКЕРЕ (ребейт).

Сравниваем 4 комбинации × walk-forward (честный OOS):
  обычный/инверсия × taker/maker

Запуск:  python run_invert.py
"""
import pandas as pd

from trading_analyzer import BacktestEngine
from trading_analyzer.optimize import ParamGrid
from trading_analyzer.risk import RiskParams
from trading_analyzer.maker import MakerParams
from trading_analyzer.walkforward import WalkForward
from trading_analyzer.strategies import EnsembleStrategy
from trading_analyzer.filters import VolatilityGate, TrendGate
from trading_analyzer.detectors import (
    WallBounceDetector, WallPullDetector, ImbalanceDetector, GatedDetector,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=5.0, atr_take_mult=10.0)
MAKER = MakerParams(fee=-0.0001, offset=0.0003, fill_window=5)
TRAIN_BARS, TEST_BARS = 4320, 1440
OHLCV = {"open": "Open", "high": "High", "low": "Low", "close": "Close",
         "volume": "Volume"}


def load(sym):
    df = pd.read_csv(f"crypto_data/{sym}.csv", parse_dates=["time"])
    return df.set_index("time").sort_index().rename(columns=OHLCV)


def specialized():
    vol = VolatilityGate(vol_window=60, ref_window=1440, mult=1.3)
    trend = TrendGate(ma_window=240, threshold=0.002)
    return [GatedDetector(WallBounceDetector(weight=1.0), vol),
            GatedDetector(WallPullDetector(weight=1.0), vol),
            GatedDetector(ImbalanceDetector(weight=1.0), trend)]


def main():
    engine = BacktestEngine(initial_capital=10_000, commission=0.0004)
    for sym in SYMBOLS:
        try:
            df = load(sym)
        except FileNotFoundError:
            print(f"{sym}: нет crypto_data/{sym}.csv")
            continue
        print(f"\n=== {sym}: обычный vs инверсия, taker vs maker (WF) ===")
        print(f"  {'режим':<18}{'OOS return':>11}  {'sharpe':>7}  "
              f"{'maxDD':>7}  {'trades':>6}  {'окон+':>6}")
        for inv in (False, True):
            for tag, mk in (("taker", None), ("maker", MAKER)):
                wf = WalkForward(engine, metric="sharpe", risk=RISK,
                                 allow_short=True, maker=mk)
                try:
                    res = wf.run(sym, df, ParamGrid(
                        EnsembleStrategy,
                        {"entry_threshold": [0.7, 1.0],
                         "exit_threshold": [-0.5, -0.9]},
                        fixed_params={"detectors": specialized(),
                                      "cooldown_bars": 10, "invert": inv},
                    ), train_bars=TRAIN_BARS, test_bars=TEST_BARS, verbose=False)
                    s = res["summary"]
                    w = res["windows"]
                    pos = int((w["test_return_%"] > 0).sum())
                    label = ("инверсия " if inv else "обычный  ") + tag
                    print(f"  {label:<18}{s['oos_total_return_%']:>10.2f}%"
                          f"  {s['oos_sharpe']:>7.2f}  {s['oos_max_drawdown_%']:>6.2f}%"
                          f"  {s['total_trades']:>6}  {pos:>4}/{s['n_windows']}")
                except ValueError as e:
                    print(f"  {inv}/{tag}: {e}")


if __name__ == "__main__":
    main()
