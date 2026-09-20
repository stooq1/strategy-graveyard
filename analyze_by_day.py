"""Прицельная проверка гипотезы «край стакан-сигналов живёт в волатильности».

Прогоняем детектор/ансамбль на всём периоде одним прогоном (непрерывная
история — детекторы видят свои rolling-окна), затем раскладываем P&L
закрытых сделок по дням и сопоставляем с дневным размахом цены.

Если гипотеза верна — прибыльные дни должны совпадать с волатильными.

Запуск:  python analyze_by_day.py   (после retest_crypto.sh / свежих CSV)
"""
import pandas as pd

from trading_analyzer import BacktestEngine
from trading_analyzer.risk import RiskParams
from trading_analyzer.maker import MakerParams
from trading_analyzer.strategies import EnsembleStrategy
from trading_analyzer.filters import VolatilityGate, TrendGate
from trading_analyzer.detectors import (
    WallBounceDetector, WallPullDetector, ImbalanceDetector, GatedDetector,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "LTCUSDT", "ATOMUSDT"]
RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=5.0, atr_take_mult=10.0)
MAKER = MakerParams(fee=-0.0001, offset=0.0003, fill_window=5)
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


def per_day(df, result):
    """Дневной P&L закрытых сделок + дневной размах цены."""
    daily_range = ((df["High"].groupby(df.index.date).max()
                    - df["Low"].groupby(df.index.date).min())
                   / df["Low"].groupby(df.index.date).min() * 100)
    trades = result.trades
    if len(trades) == 0 or "profit_pct" not in trades.columns:
        pnl = pd.Series(dtype=float)
    else:
        closed = trades.dropna(subset=["profit_pct"]).copy()
        closed["day"] = pd.to_datetime(closed["time"]).dt.date
        pnl = closed.groupby("day")["profit_pct"].agg(["sum", "count"])
    out = pd.DataFrame({"range_%": daily_range.round(2)})
    out["pnl_%"] = pnl["sum"].round(2) if len(pnl) else 0.0
    out["trades"] = pnl["count"] if len(pnl) else 0
    return out.fillna(0)


def main():
    engine = BacktestEngine(initial_capital=10_000, commission=0.0004)
    for sym in SYMBOLS:
        try:
            df = load(sym)
        except FileNotFoundError:
            print(f"{sym}: нет crypto_data/{sym}.csv")
            continue
        strat = EnsembleStrategy(specialized(), entry_threshold=0.7,
                                 exit_threshold=-0.5, cooldown_bars=10)
        res = engine.run(strat, df, sym, risk=RISK, allow_short=True, maker=MAKER)
        tbl = per_day(df, res)

        print(f"\n=== {sym}: P&L специализир. ансамбля (maker) по дням ===")
        print(tbl.to_string())

        # корреляция волатильность <-> прибыль
        if tbl["pnl_%"].abs().sum() > 0:
            corr = tbl["range_%"].corr(tbl["pnl_%"])
            vol_days = tbl[tbl["range_%"] >= 5]
            calm_days = tbl[tbl["range_%"] < 5]
            print(f"  корреляция размах<->P&L: {corr:+.2f}")
            print(f"  волатильные дни (>=5%): P&L {vol_days['pnl_%'].sum():+.2f}% "
                  f"за {len(vol_days)} дн")
            print(f"  спокойные дни  (<5%):  P&L {calm_days['pnl_%'].sum():+.2f}% "
                  f"за {len(calm_days)} дн")


if __name__ == "__main__":
    main()
