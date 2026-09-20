"""Проверка гипотез по стакану на минутках FORTS 2020.

Сначала:  ./export_moex.sh   (создаст moex_data/*.csv)
Затем:    python run_walls.py

Каждый контракт тестируется на своём фронт-периоде (когда он был самым
ликвидным). Детекторы соло + ансамбль walk-forward'ом.
"""
import pandas as pd

from trading_analyzer import BacktestEngine
from trading_analyzer.optimize import ParamGrid
from trading_analyzer.risk import RiskParams
from trading_analyzer.walkforward import WalkForward
from trading_analyzer.strategies import EnsembleStrategy
from trading_analyzer.filters import VolatilityGate, TrendGate
from trading_analyzer.detectors import (
    WallBounceDetector, WallPullDetector, ImbalanceDetector, GatedDetector,
)

# Фронт-периоды контрактов (экспирации март/июнь/сентябрь 2020)
CONTRACTS = {
    "SiH0": ("2020-02-25", "2020-03-19"),
    "SiM0": ("2020-03-20", "2020-06-18"),
    "SiU0": ("2020-06-19", "2020-09-03"),
    "RIH0": ("2020-02-25", "2020-03-19"),
    "RIM0": ("2020-03-20", "2020-06-18"),
    "RIU0": ("2020-06-19", "2020-09-03"),
}

# Комиссия+проскальзывание ~0.005% на сторону (биржевой сбор + 1 тик)
COMMISSION = 0.00005
ALLOW_SHORT = True   # сигналы стакана симметричны — шорты обязательны
RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=5.0, atr_take_mult=10.0)

OHLCV = {"open": "Open", "high": "High", "low": "Low", "close": "Close",
         "volume": "Volume"}


def load(code: str) -> pd.DataFrame:
    df = pd.read_csv(f"moex_data/{code}.csv", parse_dates=["time"])
    df = df.set_index("time").sort_index().rename(columns=OHLCV)
    start, end = CONTRACTS[code]
    return df.loc[start:end]


def detectors():
    return [
        WallBounceDetector(weight=1.0),
        WallPullDetector(weight=1.0),
        ImbalanceDetector(weight=1.0),
    ]


def specialized_detectors():
    """Режимная специализация: плиты — только в шторм, дисбаланс — в движении."""
    vol = VolatilityGate()
    return [
        GatedDetector(WallBounceDetector(weight=1.0), vol),
        GatedDetector(WallPullDetector(weight=1.0), vol),
        GatedDetector(ImbalanceDetector(weight=1.0), TrendGate()),
    ]


def main():
    engine = BacktestEngine(initial_capital=10_000, commission=COMMISSION)
    rows = []

    for code in CONTRACTS:
        try:
            df = load(code)
        except FileNotFoundError:
            print(f"{code}: нет moex_data/{code}.csv — сперва ./export_moex.sh")
            continue
        if len(df) < 3000:
            print(f"{code}: мало данных ({len(df)} минут), пропуск")
            continue

        glass_cov = (df["bid_wall_qty"] > 0).mean() * 100
        print(f"\n=== {code} [{df.index[0].date()} – {df.index[-1].date()}] "
              f"{len(df)} минут, покрытие стаканом {glass_cov:.0f}% ===")

        # Детекторы соло (вся история контракта)
        for d in detectors():
            strat = EnsembleStrategy([d], entry_threshold=0.5,
                                     exit_threshold=-0.5, cooldown_bars=10)
            m = engine.run(strat, df, code, risk=RISK,
                           allow_short=ALLOW_SHORT).metrics
            print(f"  {d.name:<13}"
                  f"  return {m['total_return_%']:>7.2f}%"
                  f"  (b&h {m['buy_hold_%']:>7.2f}%)"
                  f"  sharpe {m['sharpe']:>6.2f}"
                  f"  maxDD {m['max_drawdown_%']:>6.2f}%"
                  f"  trades {m['trades']:>4}"
                  f"  win {m['win_rate_%']:>5.1f}%")
            rows.append({"code": code, "variant": d.name, **m})

        # Ансамбль walk-forward: train ~10 дней, test ~2.5 дня.
        # Два варианта: без фильтра и с воротами волатильности.
        wf = WalkForward(engine, metric="sharpe", risk=RISK,
                         allow_short=ALLOW_SHORT)
        variants = (
            ("WF ensemble  ", {"detectors": detectors(), "gate": None}),
            ("WF ens+gate  ", {"detectors": detectors(),
                               "gate": VolatilityGate()}),
            ("WF specialzd ", {"detectors": specialized_detectors(),
                               "gate": None}),
        )
        for label, fixed in variants:
            try:
                res = wf.run(code, df, ParamGrid(
                    EnsembleStrategy,
                    {"entry_threshold": [0.7, 1.0, 1.4],
                     "exit_threshold": [-0.5, -0.9]},
                    fixed_params={"cooldown_bars": 10, **fixed},
                ), train_bars=8400, test_bars=2100, verbose=False)
                s = res["summary"]
                print(f"  {label} OOS return {s['oos_total_return_%']:>7.2f}%"
                      f"  sharpe {s['oos_sharpe']:>6.2f}"
                      f"  maxDD {s['oos_max_drawdown_%']:>6.2f}%"
                      f"  trades {s['total_trades']:>4}"
                      f"  ({s['n_windows']} окон)")
                rows.append({"code": code, "variant": label.strip(),
                             "total_return_%": s["oos_total_return_%"],
                             "sharpe": s["oos_sharpe"],
                             "max_drawdown_%": s["oos_max_drawdown_%"],
                             "trades": s["total_trades"]})
            except ValueError as e:
                print(f"  {label}: {e}")

    pd.DataFrame(rows).to_csv("walls_results.csv", index=False)
    print("\nСохранено: walls_results.csv")


if __name__ == "__main__":
    main()
