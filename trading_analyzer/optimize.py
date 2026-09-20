"""Grid search с разбиением train/test против оверфита.

Подбор параметров на всей истории — самообман: лучшая комбинация почти
всегда подогнана под шум. Поэтому каждая комбинация прогоняется отдельно
на train-периоде (in-sample, подбор) и test-периоде (out-of-sample, честная
проверка). Доверять можно только тем параметрам, что выживают на test.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

import pandas as pd

from .backtest import BacktestEngine
from .risk import RiskParams
from .strategy import BaseStrategy


@dataclass
class ParamGrid:
    """Сетка параметров стратегии.

    fixed_params — то, что не перебирается (например pair_close для
    корреляционной стратегии).
    """
    strategy_class: Type[BaseStrategy]
    param_ranges: Dict[str, List[Any]]
    fixed_params: Dict[str, Any] = field(default_factory=dict)

    def configs(self):
        keys = list(self.param_ranges)
        for combo in itertools.product(*self.param_ranges.values()):
            yield dict(zip(keys, combo))

    def __len__(self):
        n = 1
        for v in self.param_ranges.values():
            n *= len(v)
        return n


class GridSearch:
    def __init__(self, engine: Optional[BacktestEngine] = None,
                 metric: str = "sharpe", test_frac: float = 0.3,
                 risk: Optional[RiskParams] = None):
        """
        metric    : по какой метрике ранжировать (sharpe / total_return_% / ...)
        test_frac : доля истории, отложенная под out-of-sample проверку
        risk      : общий риск-менеджмент для всех прогонов (опционально)
        """
        self.engine = engine or BacktestEngine()
        self.metric = metric
        self.test_frac = test_frac
        self.risk = risk
        self.results: List[Dict[str, Any]] = []

    def run(self, symbol: str, data: pd.DataFrame, grid: ParamGrid,
            verbose: bool = True) -> pd.DataFrame:
        """Перебор сетки на одном инструменте. Возвращает DataFrame результатов."""
        split = int(len(data) * (1 - self.test_frac))
        train, test = data.iloc[:split], data.iloc[split:]
        if verbose:
            print(f"\n{grid.strategy_class.__name__} на {symbol}: "
                  f"{len(grid)} комбинаций | train {len(train)} баров "
                  f"[{train.index[0].date()}–{train.index[-1].date()}] | "
                  f"test {len(test)} баров "
                  f"[{test.index[0].date()}–{test.index[-1].date()}]")

        rows = []
        for params in grid.configs():
            strategy = grid.strategy_class(**grid.fixed_params, **params)
            try:
                tr = self.engine.run(strategy, train, symbol, risk=self.risk)
                te = self.engine.run(strategy, test, symbol, risk=self.risk)
            except Exception as e:
                print(f"  ошибка {params}: {e}")
                continue
            row = {"strategy": strategy.name, "symbol": symbol, **params}
            row.update({f"train_{k}": v for k, v in tr.metrics.items()})
            row.update({f"test_{k}": v for k, v in te.metrics.items()})
            rows.append(row)

        self.results.extend(rows)
        df = pd.DataFrame(rows).sort_values(f"train_{self.metric}",
                                            ascending=False)
        if verbose and len(df):
            self._report(df)
        return df

    def _report(self, df: pd.DataFrame, top: int = 5):
        """Топ по train-метрике и их же результаты на test."""
        param_cols = [c for c in df.columns
                      if not c.startswith(("train_", "test_"))
                      and c not in ("strategy", "symbol")]
        cols = (param_cols
                + [f"train_{self.metric}", f"test_{self.metric}",
                   "train_total_return_%", "test_total_return_%",
                   "test_max_drawdown_%", "test_trades"])
        print(f"  Топ-{top} по train_{self.metric} (смотрите на test_*!):")
        with pd.option_context("display.width", 200):
            out = df.head(top)[cols].copy()
            metric_cols = [c for c in cols if c.startswith(("train_", "test_"))]
            out[metric_cols] = out[metric_cols].round(2)
            print(out.to_string(index=False))

    def save(self, filename: str = "optimization_results.csv"):
        if not self.results:
            print("Нет результатов")
            return
        df = pd.DataFrame(self.results).sort_values(
            f"train_{self.metric}", ascending=False)
        df.to_csv(filename, index=False)
        print(f"\nСохранено {len(df)} строк в {filename}")
