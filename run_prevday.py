"""Предиктивный фильтр волатильности: торговать день D, если D-1 был бурным.

Реактивный VolatilityGate проиграл (запаздывает внутри дня). Здесь решение
о торговле дня принято ДО его начала по вчерашнему размаху — причинно и без
лага. Опора на кластеризацию волатильности.

Свип порога вчерашнего размаха, честный walk-forward, maker.

Запуск:  python run_prevday.py
"""
import pandas as pd

from trading_analyzer import BacktestEngine
from trading_analyzer.optimize import ParamGrid
from trading_analyzer.risk import RiskParams
from trading_analyzer.maker import MakerParams
from trading_analyzer.walkforward import WalkForward
from trading_analyzer.strategies import EnsembleStrategy
from trading_analyzer.filters import PrevDayVolGate, TrendGate
from trading_analyzer.detectors import (
    WallBounceDetector, WallPullDetector, ImbalanceDetector, GatedDetector,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=5.0, atr_take_mult=10.0)
MAKER = MakerParams(fee=-0.0001, offset=0.0003, fill_window=5)
TRAIN_BARS, TEST_BARS = 7200, 2880   # 5 дней train / 2 дня test (25 дней всего)
PREV_RANGES = [0.0, 0.03, 0.04, 0.05, 0.06]   # 0.0 = фильтр выключен (база)
OHLCV = {"open": "Open", "high": "High", "low": "Low", "close": "Close",
         "volume": "Volume"}


def load(sym):
    df = pd.read_csv(f"crypto_data/{sym}.csv", parse_dates=["time"])
    return df.set_index("time").sort_index().rename(columns=OHLCV)


def detectors(prev_gate):
    """Все детекторы под общим предиктивным дневным фильтром."""
    trend = TrendGate(ma_window=240, threshold=0.002)
    base = [WallBounceDetector(weight=1.0), WallPullDetector(weight=1.0),
            ImbalanceDetector(weight=1.0)]
    # imbalance дополнительно под трендом, всё — под дневным фильтром
    base[2] = GatedDetector(base[2], trend)
    if prev_gate is None:
        return base
    return [GatedDetector(d, prev_gate) for d in base]


def main():
    engine = BacktestEngine(initial_capital=10_000, commission=0.0004)
    for sym in SYMBOLS:
        try:
            df = load(sym)
        except FileNotFoundError:
            print(f"{sym}: нет crypto_data/{sym}.csv")
            continue
        print(f"\n=== {sym}: предиктивный дневной фильтр (WF maker, "
              f"{len(df)} минут) ===")
        print(f"  {'вчера>=':>8}  {'OOS return':>11}  {'sharpe':>7}  "
              f"{'maxDD':>7}  {'trades':>6}  {'окон+':>6}")
        for pr in PREV_RANGES:
            gate = None if pr == 0 else PrevDayVolGate(min_range=pr)
            wf = WalkForward(engine, metric="sharpe", risk=RISK,
                             allow_short=True, maker=MAKER)
            try:
                res = wf.run(sym, df, ParamGrid(
                    EnsembleStrategy,
                    {"entry_threshold": [0.7, 1.0], "exit_threshold": [-0.5, -0.9]},
                    fixed_params={"detectors": detectors(gate),
                                  "cooldown_bars": 10},
                ), train_bars=TRAIN_BARS, test_bars=TEST_BARS, verbose=False)
                s = res["summary"]
                w = res["windows"]
                pos = int((w["test_return_%"] > 0).sum())
                tag = "выкл" if pr == 0 else f"{pr:.0%}"
                print(f"  {tag:>8}  {s['oos_total_return_%']:>10.2f}%"
                      f"  {s['oos_sharpe']:>7.2f}  {s['oos_max_drawdown_%']:>6.2f}%"
                      f"  {s['total_trades']:>6}  {pos:>4}/{s['n_windows']}")
            except ValueError as e:
                print(f"  {pr}: {e}")


if __name__ == "__main__":
    main()
