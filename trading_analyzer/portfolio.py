"""Портфельный walk-forward: одна стратегия на корзине инструментов.

Каждый тикер проходит свой walk-forward (переоптимизация на train-окнах,
торговля на test-окнах), затем OOS-кривые объединяются в равновзвешенный
портфель с ежедневной ребалансировкой.

Выравнивание — по ОБЪЕДИНЕНИЮ периодов, а не пересечению: у тикеров разная
глубина истории, и пересечение может оказаться пустым. Пока инструмент
не торгуется, его вес распределяется на остальные (mean по доступным).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .backtest import BacktestEngine
from .optimize import ParamGrid
from .risk import RiskParams
from .walkforward import WalkForward


@dataclass
class PortfolioWFResult:
    summary: Dict[str, Any]
    details: pd.DataFrame
    equity: pd.Series = field(repr=False)


class PortfolioWalkForward:
    def __init__(self, engine: Optional[BacktestEngine] = None,
                 metric: str = "sharpe", risk: Optional[RiskParams] = None):
        self.engine = engine or BacktestEngine()
        self.metric = metric
        self.risk = risk

    def run(self, datasets: Dict[str, pd.DataFrame], grid: ParamGrid,
            train_bars: int = 400, test_bars: int = 100,
            verbose: bool = True) -> PortfolioWFResult:
        """datasets: {symbol: OHLCV DataFrame}."""
        wf = WalkForward(self.engine, metric=self.metric, risk=self.risk)
        oos_returns: Dict[str, pd.Series] = {}
        details_rows = []

        for symbol, df in datasets.items():
            try:
                res = wf.run(symbol, df, grid, train_bars, test_bars,
                             verbose=False)
            except (ValueError, KeyError) as e:
                if verbose:
                    print(f"  {symbol:<10} пропуск: {e}")
                continue
            s = res["summary"]
            oos_returns[symbol] = res["oos_equity"].pct_change().fillna(0.0)
            details_rows.append({
                "symbol": symbol,
                "oos_return_%": s["oos_total_return_%"],
                "oos_sharpe": s["oos_sharpe"],
                "oos_max_dd_%": s["oos_max_drawdown_%"],
                "trades": s["total_trades"],
                "n_windows": s["n_windows"],
            })
            if verbose:
                print(f"  {symbol:<10} return {s['oos_total_return_%']:>7.1f}%  "
                      f"sharpe {s['oos_sharpe']:>5.2f}  "
                      f"maxDD {s['oos_max_drawdown_%']:>6.1f}%  "
                      f"trades {s['total_trades']:>3}")

        if not oos_returns:
            raise ValueError("Ни один тикер не прошёл walk-forward")

        # --- Объединение: outer join, равные веса по доступным -----------
        rets = pd.DataFrame(oos_returns)  # union индексов, NaN где не торгуется
        port_ret = rets.mean(axis=1, skipna=True).fillna(0.0)
        equity = (1 + port_ret).cumprod()

        ppy = BacktestEngine._periods_per_year(port_ret.index)
        sharpe = 0.0
        if len(port_ret) > 1 and port_ret.std() > 0:
            sharpe = float(np.sqrt(ppy) * port_ret.mean() / port_ret.std())

        details = pd.DataFrame(details_rows).sort_values(
            "oos_sharpe", ascending=False)
        summary = {
            "n_symbols": len(details),
            "total_trades": int(details["trades"].sum()),
            "portfolio_oos_return_%": float((equity.iloc[-1] - 1) * 100),
            "portfolio_oos_sharpe": sharpe,
            "portfolio_oos_max_dd_%": float(
                ((equity / equity.cummax()) - 1).min() * 100),
            "symbols_sharpe>0": int((details["oos_sharpe"] > 0).sum()),
            "median_symbol_sharpe": float(details["oos_sharpe"].median()),
            "period": f"{port_ret.index[0].date()}–{port_ret.index[-1].date()}",
        }
        return PortfolioWFResult(summary, details, equity)
