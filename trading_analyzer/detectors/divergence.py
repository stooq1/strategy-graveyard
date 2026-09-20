"""Детектор RSI-дивергенций — обёртка над логикой DivergenceStrategy.

Сила голоса масштабируется глубиной дивергенции: насколько сильно RSI
не подтвердил новый экстремум цены (разница RSI на двух пивотах / 20 пунктов).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BaseDetector
from ..strategies.divergence import rsi


class DivergenceDetector(BaseDetector):
    name = "rsi_divergence"

    def __init__(self, weight: float = 1.0, rsi_period: int = 14,
                 pivot_order: int = 5, max_pivot_gap: int = 60,
                 rsi_scale: float = 20.0):
        super().__init__(weight, {"rsi": rsi_period, "order": pivot_order,
                                  "gap": max_pivot_gap})
        self.rsi_period = rsi_period
        self.order = pivot_order
        self.max_gap = max_pivot_gap
        self.rsi_scale = rsi_scale

    def _pivots(self, series: pd.Series, find_low: bool) -> np.ndarray:
        w = 2 * self.order + 1
        extreme = (series.rolling(w, center=True).min() if find_low
                   else series.rolling(w, center=True).max())
        mask = (series == extreme) & extreme.notna()
        return np.flatnonzero(mask.values)

    def score(self, data: pd.DataFrame) -> pd.Series:
        close = data["Close"]
        ind = rsi(close, self.rsi_period)
        n = len(close)
        scores = np.zeros(n)
        c, r = close.values, ind.values

        for find_low, sign in ((True, +1.0), (False, -1.0)):
            pivots = self._pivots(close, find_low)
            for k in range(1, len(pivots)):
                j_prev, j = pivots[k - 1], pivots[k]
                confirm = j + self.order
                if confirm >= n or j - j_prev > self.max_gap:
                    continue
                price_worse = c[j] < c[j_prev] if find_low else c[j] > c[j_prev]
                rsi_better = r[j] > r[j_prev] if find_low else r[j] < r[j_prev]
                if price_worse and rsi_better:
                    depth = min(1.0, abs(r[j] - r[j_prev]) / self.rsi_scale)
                    scores[confirm] = sign * max(0.3, depth)

        return pd.Series(scores, index=data.index)
