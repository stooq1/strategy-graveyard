"""Детектор дивергенций цены и RSI.

Бычья дивергенция:   цена делает более низкий минимум, RSI — более высокий → BUY.
Медвежья дивергенция: цена делает более высокий максимум, RSI — более низкий → SELL.

Реализация без лукахеда и за O(n):
  * пивот (локальный экстремум) на баре j определяется окном j±order,
    поэтому считается ПОДТВЕРЖДЁННЫМ только на баре j+order — сигнал
    ставится именно там, а не задним числом;
  * пивоты ищутся одним проходом (centered rolling min/max),
    без пересчёта экстремумов на каждом баре.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..strategy import BUY, SELL, HOLD, BaseStrategy


def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """RSI по Уайлдеру (экспоненциальное сглаживание)."""
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


class DivergenceStrategy(BaseStrategy):
    name = "rsi_divergence"

    def __init__(self, rsi_period: int = 14, pivot_order: int = 5,
                 max_pivot_gap: int = 60):
        """
        rsi_period    : период RSI
        pivot_order   : пивот = экстремум в окне ±pivot_order баров
                        (подтверждение приходит через pivot_order баров)
        max_pivot_gap : два пивота дальше этого расстояния не сравниваем
        """
        super().__init__({"rsi_period": rsi_period, "pivot_order": pivot_order,
                          "max_pivot_gap": max_pivot_gap})
        self.rsi_period = rsi_period
        self.order = pivot_order
        self.max_gap = max_pivot_gap

    def _pivots(self, series: pd.Series, find_low: bool) -> np.ndarray:
        """Индексы (позиции) подтверждённых пивотов одним проходом."""
        w = 2 * self.order + 1
        if find_low:
            extreme = series.rolling(w, center=True).min()
        else:
            extreme = series.rolling(w, center=True).max()
        mask = (series == extreme) & extreme.notna()
        return np.flatnonzero(mask.values)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["Close"]
        ind = rsi(close, self.rsi_period)
        n = len(close)
        signals = np.full(n, HOLD)
        c, r = close.values, ind.values

        # Бычьи дивергенции — по парам соседних минимумов цены
        lows = self._pivots(close, find_low=True)
        for k in range(1, len(lows)):
            j_prev, j = lows[k - 1], lows[k]
            confirm = j + self.order  # бар, на котором пивот j стал известен
            if confirm >= n or j - j_prev > self.max_gap:
                continue
            if c[j] < c[j_prev] and r[j] > r[j_prev]:
                signals[confirm] = BUY

        # Медвежьи — по парам соседних максимумов
        highs = self._pivots(close, find_low=False)
        for k in range(1, len(highs)):
            j_prev, j = highs[k - 1], highs[k]
            confirm = j + self.order
            if confirm >= n or j - j_prev > self.max_gap:
                continue
            if c[j] > c[j_prev] and r[j] < r[j_prev]:
                signals[confirm] = SELL

        return pd.Series(signals, index=close.index)
