"""Раскладка P&L детекторов по отрезкам времени, волатильности дней и сторонам.

Отвечает на три вопроса, которых не видно в итоговом return прогона:
  1. что добавил НОВЫЙ отрезок данных (после --cut) к старому — на сделку;
  2. где сделан P&L: на волатильных днях (размах >= --vol %) или спокойных;
  3. с какой стороны: лонги или шорты (плюс на тренде = экспозиция, не край);
  и, опционально, сколько дал узкий всплеск дней (--burst).

net = gross P&L сделки минус круговая комиссия (taker 0.08%, maker 0.03%:
ребейт 0.01% на входе лимиткой, тейкер 0.04% на выходе). Суммы аддитивные,
без компаундинга — для сравнения на сделку это и нужно.

Запуск:  python analyze_segments.py [--cut "2026-08-13 16:10"] [--vol 5]
                                    [--burst 2026-08-19:2026-08-22]
"""
import argparse
import warnings

import numpy as np
import pandas as pd

from trading_analyzer import BacktestEngine
from trading_analyzer.risk import RiskParams
from trading_analyzer.maker import MakerParams
from trading_analyzer.strategies import EnsembleStrategy
from trading_analyzer.filters import VolatilityGate, TrendGate
from trading_analyzer.detectors import (
    WallBounceDetector, WallPullDetector, ImbalanceDetector, GatedDetector,
)

warnings.filterwarnings("ignore")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "LTCUSDT", "ATOMUSDT"]
RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=5.0, atr_take_mult=10.0)
MAKER = MakerParams(fee=-0.0001, offset=0.0003, fill_window=5)
COST = {"taker": 0.08, "maker": 0.03}      # круговая комиссия, %
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


def variants():
    for det in (WallBounceDetector, WallPullDetector, ImbalanceDetector):
        d = det(weight=1.0)
        yield d.name, EnsembleStrategy([d], entry_threshold=0.5,
                                       exit_threshold=-0.5, cooldown_bars=10)
    yield "specialized", EnsembleStrategy(specialized(), entry_threshold=0.7,
                                          exit_threshold=-0.5, cooldown_bars=10)


def closed_trades(result, tag, daily_range):
    c = result.trades.dropna(subset=["profit_pct"]).copy()
    c["day"] = pd.to_datetime(c["time"]).dt.normalize()
    c["net"] = c["profit_pct"] - COST[tag]
    c["range"] = c["day"].map(daily_range)
    c["side"] = np.where(c["action"] == "SELL", "long", "short")
    return c


def summarize(c, vol):
    n = len(c)
    hot, calm = c[c["range"] >= vol], c[c["range"] < vol]
    L, S = c[c["side"] == "long"], c[c["side"] == "short"]
    return {"trades": n, "net%": c["net"].sum(),
            "net/tr": c["net"].mean() if n else np.nan,
            "t": (c["net"].mean() / (c["net"].std(ddof=1) / np.sqrt(n))
                  if n > 1 else np.nan),
            "vol_net%": hot["net"].sum(), "vol_n": len(hot),
            "calm_net%": calm["net"].sum(), "calm_n": len(calm),
            "long_net%": L["net"].sum(), "long_n": len(L),
            "short_net%": S["net"].sum(), "short_n": len(S)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", default="2026-08-13 16:10",
                    help="граница старый/новый отрезок (время в CSV)")
    ap.add_argument("--vol", type=float, default=5.0,
                    help="порог дневного размаха для «волатильного» дня, %")
    ap.add_argument("--burst", default=None,
                    help="окно дат A:B (включительно) — вклад всплеска")
    args = ap.parse_args()
    cut = pd.Timestamp(args.cut)
    burst = None
    if args.burst:
        a, b = args.burst.split(":")
        burst = (pd.Timestamp(a), pd.Timestamp(b) + pd.Timedelta(days=1))

    engine = BacktestEngine(initial_capital=10_000, commission=0.0004)
    pd.set_option("display.width", 250)
    print(f"cut = {cut}, волатильный день = размах >= {args.vol}%, "
          f"net = gross − комиссия (taker {COST['taker']}%, maker {COST['maker']}%)")

    for sym in SYMBOLS:
        try:
            df = load(sym)
        except FileNotFoundError:
            print(f"{sym}: нет crypto_data/{sym}.csv")
            continue
        g = df.groupby(df.index.date)
        rng = (g["High"].max() - g["Low"].min()) / g["Low"].min() * 100
        rng.index = pd.to_datetime(rng.index)
        old_days, new_days = rng[rng.index < cut.normalize()], rng[rng.index >= cut.normalize()]
        print(f"\n=== {sym}: старый отрезок {len(old_days)} дн (дней >={args.vol}%: "
              f"{(old_days >= args.vol).sum()}, max {old_days.max():.1f}%), "
              f"новый {len(new_days)} дн (>={args.vol}%: {(new_days >= args.vol).sum()}, "
              f"max {new_days.max():.1f}%) ===")

        rows = []
        for vname, strat in variants():
            for tag, mk in (("taker", None), ("maker", MAKER)):
                res = engine.run(strat, df, sym, risk=RISK, allow_short=True,
                                 maker=mk)
                c = closed_trades(res, tag, rng)
                old, new = c[c["time"] < cut], c[c["time"] >= cut]
                o, nw = summarize(old, args.vol), summarize(new, args.vol)
                row = {"variant": vname, "exec": tag,
                       "old_tr": o["trades"], "old_net%": o["net%"], "old_net/tr": o["net/tr"],
                       "new_tr": nw["trades"], "new_net%": nw["net%"], "new_net/tr": nw["net/tr"],
                       "new_vol%": nw["vol_net%"], "vol_n": nw["vol_n"],
                       "new_calm%": nw["calm_net%"], "calm_n": nw["calm_n"],
                       "new_long%": nw["long_net%"], "long_n": nw["long_n"],
                       "new_short%": nw["short_net%"], "short_n": nw["short_n"],
                       "t_all": summarize(c, args.vol)["t"]}
                if burst:
                    bb = c[(c["time"] >= burst[0]) & (c["time"] < burst[1])]
                    row["burst%"] = bb["net"].sum()
                    row["burst_n"] = len(bb)
                rows.append(row)
        tbl = pd.DataFrame(rows).set_index(["variant", "exec"])
        print(tbl.round(3).to_string())


if __name__ == "__main__":
    main()
