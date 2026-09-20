"""Walk-forward оптимизация: переоптимизация на скользящих окнах.

Схема: [train N баров][test M баров] -> сдвиг на M -> снова.
Test-окна не пересекаются и стыкуются — из них сшивается единая
out-of-sample кривая доходности. Это и есть честная оценка: каждая сделка
совершена на данных, которых оптимизатор не видел.

Агрегация — компаундингом доходностей (произведение, не сумма процентов),
сводный Sharpe считается по сшитому ряду OOS-доходностей.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .backtest import BacktestEngine
from .optimize import ParamGrid
from .risk import RiskParams


class WalkForward:
    def __init__(self, engine: Optional[BacktestEngine] = None,
                 metric: str = "sharpe", risk: Optional[RiskParams] = None,
                 allow_short: bool = False, maker=None):
        self.engine = engine or BacktestEngine()
        self.metric = metric
        self.risk = risk
        self.allow_short = allow_short
        self.maker = maker

    def run(self, symbol: str, data: pd.DataFrame, grid: ParamGrid,
            train_bars: int = 500, test_bars: int = 125,
            verbose: bool = True) -> Dict[str, Any]:
        """Возвращает {'windows': DataFrame, 'summary': dict, 'oos_equity': Series}."""
        windows: List[Dict[str, Any]] = []
        oos_returns: List[pd.Series] = []

        start = 0
        while start + train_bars + test_bars <= len(data):
            train = data.iloc[start:start + train_bars]
            test = data.iloc[start + train_bars:start + train_bars + test_bars]

            best_params, best_score = None, -np.inf
            for params in grid.configs():
                strategy = grid.strategy_class(**grid.fixed_params, **params)
                res = self.engine.run(strategy, train, symbol, risk=self.risk,
                                      allow_short=self.allow_short,
                                      maker=self.maker)
                if res.metrics[self.metric] > best_score:
                    best_score = res.metrics[self.metric]
                    best_params = params

            strategy = grid.strategy_class(**grid.fixed_params, **best_params)
            test_res = self.engine.run(strategy, test, symbol, risk=self.risk,
                                       allow_short=self.allow_short,
                                       maker=self.maker)
            oos_returns.append(test_res.equity.pct_change().dropna())

            windows.append({
                "train": f"{train.index[0].date()}–{train.index[-1].date()}",
                "test": f"{test.index[0].date()}–{test.index[-1].date()}",
                **best_params,
                f"train_{self.metric}": round(best_score, 3),
                f"test_{self.metric}": round(test_res.metrics[self.metric], 3),
                "test_return_%": round(test_res.metrics["total_return_%"], 2),
                "test_trades": test_res.metrics["trades"],
            })
            if verbose:
                w = windows[-1]
                print(f"  окно {len(windows):>2}: test {w['test']}  "
                      f"{self.metric} {best_score:>6.2f} -> {w[f'test_{self.metric}']:>6.2f}  "
                      f"return {w['test_return_%']:>7.2f}%  "
                      f"params {best_params}")
            start += test_bars  # окна теста стык в стык, без пересечений

        if not windows:
            raise ValueError(f"Мало данных: {len(data)} баров, "
                             f"нужно >= {train_bars + test_bars}")

        # --- Сшивка OOS: компаундинг, не сумма процентов -----------------
        all_ret = pd.concat(oos_returns)
        oos_equity = (1 + all_ret).cumprod()
        ppy = BacktestEngine._periods_per_year(all_ret.index)
        sharpe = 0.0
        if len(all_ret) > 1 and all_ret.std() > 0:
            sharpe = float(np.sqrt(ppy) * all_ret.mean() / all_ret.std())

        summary = {
            "symbol": symbol,
            "strategy": grid.strategy_class.__name__,
            "n_windows": len(windows),
            "oos_total_return_%": float((oos_equity.iloc[-1] - 1) * 100),
            "oos_sharpe": sharpe,
            "oos_max_drawdown_%": float(
                ((oos_equity / oos_equity.cummax()) - 1).min() * 100),
            "oos_period": f"{all_ret.index[0].date()}–{all_ret.index[-1].date()}",
            "total_trades": int(sum(w["test_trades"] for w in windows)),
        }
        if verbose:
            print(f"  итог OOS: return {summary['oos_total_return_%']:.1f}%  "
                  f"sharpe {summary['oos_sharpe']:.2f}  "
                  f"maxDD {summary['oos_max_drawdown_%']:.1f}%  "
                  f"trades {summary['total_trades']}")

        return {"windows": pd.DataFrame(windows), "summary": summary,
                "oos_equity": oos_equity}

    # ------------------------------------------------------------ веса
    def run_weights(self, symbol: str, data: pd.DataFrame,
                    detectors: List[Any], weight_options: List[List[float]],
                    entry_options: List[float], exit_options: List[float],
                    train_bars: int = 500, test_bars: int = 125,
                    cooldown_bars: int = 5,
                    verbose: bool = True) -> Dict[str, Any]:
        """Walk-forward с перебором ВЕСОВ детекторов и порогов.

        Score каждого детектора считается один раз на окно (он не зависит
        от весов), перебор взвешенных сумм — дешёвые векторные операции.
        weight_options[i] — допустимые веса detectors[i].
        """
        import itertools

        from .strategies.ensemble import FixedScoreStrategy

        windows: List[Dict[str, Any]] = []
        oos_returns: List[pd.Series] = []
        names = [d.name for d in detectors]

        start = 0
        while start + train_bars + test_bars <= len(data):
            train = data.iloc[start:start + train_bars]
            test = data.iloc[start + train_bars:start + train_bars + test_bars]

            # Матрицы голосов: один раз на окно
            s_train = [d.score(train).clip(-1, 1).fillna(0.0).values
                       for d in detectors]
            s_test = [d.score(test).clip(-1, 1).fillna(0.0).values
                      for d in detectors]

            best, best_score = None, -np.inf
            for weights in itertools.product(*weight_options):
                if not any(weights):
                    continue
                total = sum(w * s for w, s in zip(weights, s_train))
                total_s = pd.Series(total, index=train.index)
                for entry in entry_options:
                    for exit_ in exit_options:
                        strat = FixedScoreStrategy(total_s, entry, exit_,
                                                   cooldown_bars)
                        res = self.engine.run(strat, train, symbol,
                                              risk=self.risk)
                        if res.metrics[self.metric] > best_score:
                            best_score = res.metrics[self.metric]
                            best = (weights, entry, exit_)

            weights, entry, exit_ = best
            total_test = pd.Series(
                sum(w * s for w, s in zip(weights, s_test)), index=test.index)
            test_res = self.engine.run(
                FixedScoreStrategy(total_test, entry, exit_, cooldown_bars),
                test, symbol, risk=self.risk)
            oos_returns.append(test_res.equity.pct_change().dropna())

            row = {"test": f"{test.index[0].date()}–{test.index[-1].date()}",
                   **{f"w_{n}": w for n, w in zip(names, weights)},
                   "entry": entry, "exit": exit_,
                   f"train_{self.metric}": round(best_score, 3),
                   f"test_{self.metric}": round(test_res.metrics[self.metric], 3),
                   "test_return_%": round(test_res.metrics["total_return_%"], 2),
                   "test_trades": test_res.metrics["trades"]}
            windows.append(row)
            if verbose:
                w_str = " ".join(f"{n}={w}" for n, w in zip(names, weights))
                print(f"  окно {len(windows):>2}: {row['test']}  "
                      f"{self.metric} {best_score:>5.2f} -> "
                      f"{row[f'test_{self.metric}']:>6.2f}  [{w_str}] "
                      f"entry={entry}")
            start += test_bars

        if not windows:
            raise ValueError("Мало данных для walk-forward")

        all_ret = pd.concat(oos_returns)
        oos_equity = (1 + all_ret).cumprod()
        ppy = BacktestEngine._periods_per_year(all_ret.index)
        sharpe = 0.0
        if len(all_ret) > 1 and all_ret.std() > 0:
            sharpe = float(np.sqrt(ppy) * all_ret.mean() / all_ret.std())
        summary = {
            "symbol": symbol, "n_windows": len(windows),
            "oos_total_return_%": float((oos_equity.iloc[-1] - 1) * 100),
            "oos_sharpe": sharpe,
            "oos_max_drawdown_%": float(
                ((oos_equity / oos_equity.cummax()) - 1).min() * 100),
            "total_trades": int(sum(w["test_trades"] for w in windows)),
        }
        if verbose:
            print(f"  итог OOS: return {summary['oos_total_return_%']:.1f}%  "
                  f"sharpe {summary['oos_sharpe']:.2f}  "
                  f"maxDD {summary['oos_max_drawdown_%']:.1f}%  "
                  f"trades {summary['total_trades']}")
        return {"windows": pd.DataFrame(windows), "summary": summary,
                "oos_equity": oos_equity}
