"""Детектор затухания импульса: «шёл, снизился потенциал = разворот».

Сильное движение за lookback баров, но «топливо» кончается: объём остывает
(короткое среднее ниже длинного) и диапазоны баров сжимаются. Голос — против
движения:
  цена сильно падала и затухает  -> +score (ставка на разворот вверх)
  цена сильно росла и затухает   -> -score (потенциал исчерпан)
Сила голоса растёт с величиной движения.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BaseDetector


class ExhaustionDetector(BaseDetector):
    name = "exhaustion"

    def __init__(self, weight: float = 1.0, lookback: int = 10,
                 roc_threshold: float = 0.10,
                 vol_fast: int = 3, vol_slow: int = 10):
        """
        lookback      : окно импульса (баров)
        roc_threshold : минимальное движение за окно, считающееся «шёл»
                        (0.10 = 10%)
        vol_fast/slow : объём остывает, если fast-среднее < slow-среднего
        """
        super().__init__(weight, {"lookback": lookback,
                                  "roc_thr": roc_threshold})
        self.lookback = lookback
        self.roc_threshold = roc_threshold
        self.vol_fast = vol_fast
        self.vol_slow = vol_slow

    def score(self, data: pd.DataFrame) -> pd.Series:
        close, volume = data["Close"], data["Volume"]
        roc = close.pct_change(self.lookback)

        vol_cooling = (volume.rolling(self.vol_fast).mean()
                       < volume.rolling(self.vol_slow).mean())
        rng = (data["High"] - data["Low"]) / close
        range_shrinking = (rng.rolling(self.vol_fast).mean()
                           < rng.rolling(self.vol_slow).mean())
        fading = vol_cooling & range_shrinking

        # Сила движения: 1.0 при 2x порога
        strength = (roc.abs() / (2 * self.roc_threshold)).clip(upper=1.0)

        s = pd.Series(0.0, index=data.index)
        s[(roc < -self.roc_threshold) & fading] = strength
        s[(roc > self.roc_threshold) & fading] = -strength
        return s.fillna(0.0)
