"""Промежуточный тест гипотез по стакану на свежих данных Binance.

Те же детекторы, что на FORTS, но с реальной экономикой крипты:
комиссия тейкера ~0.04% на сторону (в 8 раз дороже FORTS).

Данные:  collector/export_crypto.sh на сервере -> scp -> crypto_data/*.csv

Запуск:  python run_walls_crypto.py

ВНИМАНИЕ: 3 дня данных — это промежуточный тест одного режима рынка,
а не приговор. Окна walk-forward маленькие; цель — увидеть знак.
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

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "LTCUSDT", "ATOMUSDT"]
COMMISSION = 0.0004          # тейкер Binance futures ~0.04%
ALLOW_SHORT = True
RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=5.0, atr_take_mult=10.0)
# мейкер: ребейт -0.01%, лимит на 0.03% «лучше» рынка, ждём 5 минут
MAKER = MakerParams(fee=-0.0001, offset=0.0003, fill_window=5)
# walk-forward окна (крипта 24/7): train ~3 дня, test ~1 день
TRAIN_BARS = 4320   # 3 дня минуток
TEST_BARS = 1440    # 1 день

OHLCV = {"open": "Open", "high": "High", "low": "Low", "close": "Close",
         "volume": "Volume"}


def load(sym: str) -> pd.DataFrame:
    df = pd.read_csv(f"crypto_data/{sym}.csv", parse_dates=["time"])
    return df.set_index("time").sort_index().rename(columns=OHLCV)


def detectors():
    return [WallBounceDetector(weight=1.0), WallPullDetector(weight=1.0),
            ImbalanceDetector(weight=1.0)]


def specialized():
    # окна короче, чем на FORTS: данных всего несколько суток
    vol = VolatilityGate(vol_window=60, ref_window=1440, mult=1.3)
    trend = TrendGate(ma_window=240, threshold=0.002)
    return [GatedDetector(WallBounceDetector(weight=1.0), vol),
            GatedDetector(WallPullDetector(weight=1.0), vol),
            GatedDetector(ImbalanceDetector(weight=1.0), trend)]


def main():
    engine = BacktestEngine(initial_capital=10_000, commission=COMMISSION)
    rows = []

    for sym in SYMBOLS:
        try:
            df = load(sym)
        except FileNotFoundError:
            print(f"{sym}: нет crypto_data/{sym}.csv — сперва export_crypto.sh + scp")
            continue
        cov = (df["bid_wall_qty"].fillna(0) > 0).mean() * 100
        print(f"\n=== {sym} [{df.index[0]} – {df.index[-1]}] "
              f"{len(df)} минут, покрытие стаканом {cov:.0f}% ===")

        for d in detectors():
            strat = EnsembleStrategy([d], entry_threshold=0.5,
                                     exit_threshold=-0.5, cooldown_bars=10)
            for tag, mk in (("taker", None), ("maker", MAKER)):
                m = engine.run(strat, df, sym, risk=RISK,
                               allow_short=ALLOW_SHORT, maker=mk).metrics
                print(f"  {d.name:<13} {tag}"
                      f"  return {m['total_return_%']:>7.2f}%"
                      f"  (b&h {m['buy_hold_%']:>6.2f}%)"
                      f"  sharpe {m['sharpe']:>6.2f}"
                      f"  maxDD {m['max_drawdown_%']:>6.2f}%"
                      f"  trades {m['trades']:>4}"
                      f"  win {m['win_rate_%']:>5.1f}%")
                rows.append({"symbol": sym, "variant": d.name, "exec": tag, **m})

        # Несколько непересекающихся OOS-окон: train ~3 дня, test ~1 день.
        # Одно окно = train/test split, который легко переобучить по порогам;
        # 5-6 окон показывают, держится ли край на РАЗНЫХ отрезках.
        n = len(df)
        if n < TRAIN_BARS + TEST_BARS:
            print(f"  мало данных для walk-forward (нужно "
                  f"{TRAIN_BARS + TEST_BARS}, есть {n})")
            continue
        for tag, mk in (("taker", None), ("maker", MAKER)):
            wf = WalkForward(engine, metric="sharpe", risk=RISK,
                             allow_short=ALLOW_SHORT, maker=mk)
            try:
                res = wf.run(sym, df, ParamGrid(
                    EnsembleStrategy,
                    {"entry_threshold": [0.7, 1.0], "exit_threshold": [-0.5, -0.9]},
                    fixed_params={"detectors": specialized(), "cooldown_bars": 10},
                ), train_bars=TRAIN_BARS, test_bars=TEST_BARS, verbose=False)
                s = res["summary"]
                # доля прибыльных окон — устойчивее, чем общий Sharpe
                w = res["windows"]
                pos_windows = int((w["test_return_%"] > 0).sum())
                print(f"  WF specialzd {tag}  OOS return {s['oos_total_return_%']:>7.2f}%"
                      f"  sharpe {s['oos_sharpe']:>6.2f}"
                      f"  maxDD {s['oos_max_drawdown_%']:>6.2f}%"
                      f"  trades {s['total_trades']:>4}"
                      f"  окон+ {pos_windows}/{s['n_windows']}")
            except ValueError as e:
                print(f"  WF specialzd {tag}: {e}")

    if rows:
        pd.DataFrame(rows).to_csv("crypto_walls_results.csv", index=False)
        print("\nСохранено: crypto_walls_results.csv")


if __name__ == "__main__":
    main()
