"""Ансамбль детекторов = стратегия.

Каждый детектор голосует score * weight, голоса складываются.
  total >= entry_threshold  -> BUY
  total <= exit_threshold   -> SELL
Совместим с движком и walk-forward без изменений, поэтому ансамбль
проверяется тем же честным бэктестом, что и одиночные стратегии.

Перебитие («заявка не чаще раза в час, но если вес больше — ставим новую»):
cooldown_bars подавляет повторные BUY после недавнего, если новый суммарный
голос не превышает прошлый минимум на rearm_factor.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ..detectors.base import BaseDetector
from ..strategy import BUY, SELL, HOLD, BaseStrategy


def signals_from_score(total: np.ndarray, entry_threshold: float,
                       exit_threshold: float, cooldown_bars: int = 0,
                       rearm_factor: float = 1.25) -> np.ndarray:
    """Сигналы из ряда суммарных голосов (общая логика ансамбля)."""
    n = len(total)
    signals = np.full(n, HOLD)
    last_buy_bar = -10 ** 9
    last_buy_score = 0.0
    for i in range(n):
        if total[i] >= entry_threshold:
            in_cooldown = (i - last_buy_bar) < cooldown_bars
            stronger = total[i] >= last_buy_score * rearm_factor
            if not in_cooldown or stronger:
                signals[i] = BUY
                last_buy_bar = i
                last_buy_score = total[i]
        elif total[i] <= exit_threshold:
            signals[i] = SELL
    return signals


class EnsembleStrategy(BaseStrategy):
    name = "ensemble"

    def __init__(self, detectors: List[BaseDetector],
                 entry_threshold: float = 1.0,
                 exit_threshold: float = -1.0,
                 cooldown_bars: int = 0,
                 rearm_factor: float = 1.25,
                 gate=None, invert=False):
        """gate — фильтр режима (например VolatilityGate): где gate.mask()
        False, голоса ансамбля глушатся (новые входы запрещены, стопы
        движка продолжают работать).
        invert — вставать ПРОТИВ суммарного голоса (fade): total -> -total.
        Имеет смысл для mean-reversion детекторов в боковике."""
        super().__init__({"entry": entry_threshold, "exit": exit_threshold,
                          "cooldown": cooldown_bars,
                          "gate": gate.describe() if gate else None,
                          "invert": invert,
                          "detectors": [d.describe() for d in detectors]})
        self.detectors = detectors
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.cooldown_bars = cooldown_bars
        self.rearm_factor = rearm_factor
        self.gate = gate
        self.invert = invert

    def total_score(self, data: pd.DataFrame) -> pd.Series:
        """Суммарный взвешенный голос всех детекторов (для анализа/Grafana)."""
        total = pd.Series(0.0, index=data.index)
        for d in self.detectors:
            s = d.score(data).clip(-1, 1).fillna(0.0)
            total = total.add(s * d.weight, fill_value=0.0)
        return total

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        total = self.total_score(data)
        if self.invert:
            total = -total
        if self.gate is not None:
            total = total.where(self.gate.mask(data), 0.0)
        signals = signals_from_score(total.values, self.entry_threshold,
                                     self.exit_threshold, self.cooldown_bars,
                                     self.rearm_factor)
        return pd.Series(signals, index=data.index)


class FixedScoreStrategy(BaseStrategy):
    """Стратегия поверх ПРЕДРАССЧИТАННОГО суммарного голоса.

    Нужна для быстрого перебора весов: score детекторов не зависят от весов,
    поэтому матрица голосов считается один раз, а взвешенные суммы и сигналы
    — почти бесплатно. Без мутации общих объектов-детекторов.
    """
    name = "fixed_score"

    def __init__(self, total_score: pd.Series, entry_threshold: float,
                 exit_threshold: float, cooldown_bars: int = 0,
                 rearm_factor: float = 1.25):
        super().__init__({"entry": entry_threshold, "exit": exit_threshold})
        self.total = total_score
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.cooldown_bars = cooldown_bars
        self.rearm_factor = rearm_factor

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        total = self.total.reindex(data.index).fillna(0.0).values
        signals = signals_from_score(total, self.entry_threshold,
                                     self.exit_threshold, self.cooldown_bars,
                                     self.rearm_factor)
        return pd.Series(signals, index=data.index)
