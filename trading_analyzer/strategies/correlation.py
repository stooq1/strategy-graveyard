"""Корреляционная (парная) стратегия: возврат спреда к среднему.

Если два инструмента исторически коррелируют, но спред между ними временно
разошёлся — ставим на схождение. Long-only вариант для нашего движка:

  BUY  — основной инструмент аномально дёшев относительно парного
         (z-score лог-спреда < -entry_z) при высокой текущей корреляции;
  SELL — спред вернулся к среднему (z-score > -exit_z) — фиксируем.

Парный инструмент передаётся в конструктор серией Close, поэтому интерфейс
BaseStrategy.generate_signals(data) не меняется и движок остаётся прежним.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..strategy import BUY, SELL, HOLD, BaseStrategy


class CorrelationStrategy(BaseStrategy):
    name = "correlation"

    def __init__(self, pair_close: pd.Series, pair_name: str = "pair",
                 window: int = 30, entry_z: float = 2.0, exit_z: float = 0.0,
                 min_corr: float = 0.7):
        """
        pair_close : серия Close парного инструмента (например ETH при торговле BTC)
        window     : окно z-score и скользящей корреляции
        entry_z    : вход при z < -entry_z (основной недооценён к парному)
        exit_z     : выход при z > -exit_z (спред вернулся к среднему)
        min_corr   : торгуем только при корреляции доходностей выше порога
        """
        super().__init__({"pair": pair_name, "window": window,
                          "entry_z": entry_z, "exit_z": exit_z,
                          "min_corr": min_corr})
        self.pair_close = pair_close
        self.window = window
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.min_corr = min_corr

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["Close"]
        # ffill использует только прошлые значения — лукахеда нет
        pair = self.pair_close.reindex(close.index).ffill()

        # Лог-спред (эквивалентен cumsum разницы лог-доходностей с точностью
        # до константы, но без зависимости от точки старта ряда)
        spread = np.log(close) - np.log(pair)
        mean = spread.rolling(self.window).mean()
        std = spread.rolling(self.window).std()
        z = (spread - mean) / std

        corr = close.pct_change().rolling(self.window).corr(pair.pct_change())

        signals = pd.Series(HOLD, index=close.index)
        signals[(z < -self.entry_z) & (corr > self.min_corr)] = BUY
        signals[z > -self.exit_z] = SELL  # движок применит только в позиции
        return signals
