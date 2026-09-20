"""Стратегия "круглых уровней": отскок цены от психологических уровней.

Круглый уровень — ближайшее число вида k * 10^n (например 70000, 75000 для BTC;
190, 200 для AAPL). Шаг уровня подбирается от порядка цены: ~5-10% от неё.

Логика (long-only):
  BUY  — цена сверху коснулась уровня и закрылась над ним (отскок от поддержки);
  SELL — цена снизу коснулась уровня и закрылась под ним (отбой от сопротивления,
         выход из позиции).
Всё векторизовано, без циклов по барам.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..strategy import BUY, SELL, HOLD, BaseStrategy


class RoundNumberStrategy(BaseStrategy):
    name = "round_numbers"

    def __init__(self, level_step_frac: float = 0.05,
                 touch_threshold: float = 0.003):
        """
        level_step_frac : шаг сетки уровней как доля цены
                          (0.05 -> для цены ~70000 уровни через ~5000... фактически
                          шаг = 10^round(log10(price * frac)), т.е. круглый)
        touch_threshold : насколько близко Low/High должен подойти к уровню,
                          чтобы считать касание (доля от цены, 0.003 = 0.3%)
        """
        super().__init__({"level_step_frac": level_step_frac,
                          "touch_threshold": touch_threshold})
        self.level_step_frac = level_step_frac
        self.touch_threshold = touch_threshold

    def _level_step(self, close: pd.Series) -> pd.Series:
        """Круглый шаг сетки: степень десятки, ближайшая к price * frac."""
        raw = close * self.level_step_frac
        return 10.0 ** np.round(np.log10(raw.clip(lower=1e-12)))

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close, low, high = data["Close"], data["Low"], data["High"]
        step = self._level_step(close)

        # Ближайший уровень снизу и сверху от закрытия
        support = np.floor(close / step) * step
        resistance = support + step
        tol = close * self.touch_threshold

        # Отскок от поддержки: Low пробил/коснулся уровня, Close удержался выше
        bounce_up = (low <= support + tol) & (close > support + tol)
        # Отбой от сопротивления: High коснулся уровня, Close не смог закрепиться
        bounce_down = (high >= resistance - tol) & (close < resistance - tol)

        signals = pd.Series(HOLD, index=data.index)
        signals[bounce_up] = BUY
        signals[bounce_down] = SELL
        return signals
