"""Детектор круглых уровней с весом по «круглости».

Отскок от 70000 весомее отскока от 69500: уровни ищутся на двух сетках —
обычной (шаг ~5% цены) и грубой (в 10 раз крупнее). Голос за событие на
грубой сетке сильнее (coarse_weight).

  +score — отскок от поддержки (Low коснулся уровня, Close удержался выше);
  -score — отбой от сопротивления.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BaseDetector


class RoundLevelDetector(BaseDetector):
    name = "round_levels"

    def __init__(self, weight: float = 1.0, level_step_frac: float = 0.05,
                 touch_threshold: float = 0.003, coarse_weight: float = 2.0):
        super().__init__(weight, {"step": level_step_frac,
                                  "touch": touch_threshold,
                                  "coarse_w": coarse_weight})
        self.level_step_frac = level_step_frac
        self.touch_threshold = touch_threshold
        self.coarse_weight = coarse_weight

    def _bounces(self, data: pd.DataFrame, step: pd.Series):
        close, low, high = data["Close"], data["Low"], data["High"]
        support = np.floor(close / step) * step
        resistance = support + step
        tol = close * self.touch_threshold
        bounce_up = (low <= support + tol) & (close > support + tol)
        bounce_down = (high >= resistance - tol) & (close < resistance - tol)
        return bounce_up, bounce_down

    def score(self, data: pd.DataFrame) -> pd.Series:
        close = data["Close"]
        raw = close * self.level_step_frac
        fine_step = 10.0 ** np.round(np.log10(raw.clip(lower=1e-12)))
        coarse_step = fine_step * 10  # на порядок круглее

        s = pd.Series(0.0, index=data.index)
        up_f, down_f = self._bounces(data, fine_step)
        up_c, down_c = self._bounces(data, coarse_step)

        s[up_f] += 1.0
        s[down_f] -= 1.0
        s[up_c] += self.coarse_weight
        s[down_c] -= self.coarse_weight
        # Нормировка в [-1, 1]: максимум = событие на обеих сетках сразу
        return (s / (1.0 + self.coarse_weight)).clip(-1, 1)
