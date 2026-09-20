"""Спутниковые детекторы: сигналы из связанных инструментов.

1. FairPriceDetector — «цена, которая предполагается с учётом спутников»:
   скользящая OLS-регрессия нашей цены по спутнику даёт fair price,
   z-score отклонения от неё -> голос против отклонения.
2. LeadLagDetector — «спутник уже пошёл, наш мешкает»: голос вдогонку.
3. CorrBreakDetector — «10 пунктов корреляция норм, 4 — сломалась, потом
   выровнялась»: на восстановлении корреляции голос в сторону того, кто
   отстал за время поломки.

Все детекторы векторизованы (rolling cov/var вместо цикла с регрессией)
и не глушатся, если у спутника короче история — score=0 там, где данных нет.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BaseDetector


def _align(series: pd.Series, index: pd.Index) -> pd.Series:
    """Выравнивание спутника по нашему индексу. ffill — только прошлое."""
    return series.reindex(index).ffill()


class FairPriceDetector(BaseDetector):
    """Отклонение от равновесной цены, предсказанной по спутнику.

    fair = a*sat + b (rolling OLS: a = cov(close,sat)/var(sat)).
    score = -clip(zscore(отклонение)/max_z): выше fair -> голос вниз.
    """
    name = "fair_price"

    def __init__(self, satellite: pd.Series, sat_name: str = "sat",
                 weight: float = 1.0, lookback: int = 50, max_z: float = 2.0):
        super().__init__(weight, {"sat": sat_name, "lookback": lookback,
                                  "max_z": max_z})
        self.satellite = satellite
        self.lookback = lookback
        self.max_z = max_z

    def score(self, data: pd.DataFrame) -> pd.Series:
        close = data["Close"]
        sat = _align(self.satellite, close.index)

        cov = close.rolling(self.lookback).cov(sat)
        var = sat.rolling(self.lookback).var()
        a = cov / var.replace(0, np.nan)
        b = close.rolling(self.lookback).mean() - a * sat.rolling(self.lookback).mean()
        fair = a * sat + b

        deviation = (close - fair) / fair
        z = deviation / deviation.rolling(self.lookback).std().replace(0, np.nan)
        # Регрессия с a<=0 — связь развалилась, не голосуем
        z = z.where(a > 0)
        return (-z / self.max_z).clip(-1, 1).fillna(0.0)


class LeadLagDetector(BaseDetector):
    """Лидер сдвинулся на threshold за lookback баров, наш прошёл меньше
    catchup_frac этого пути — голос вдогонку, сила растёт с движением лидера.
    """
    name = "lead_lag"

    def __init__(self, leader: pd.Series, leader_name: str = "leader",
                 weight: float = 1.0, lookback: int = 10,
                 threshold: float = 0.05, catchup_frac: float = 0.3):
        super().__init__(weight, {"leader": leader_name, "lookback": lookback,
                                  "thr": threshold, "catchup": catchup_frac})
        self.leader = leader
        self.lookback = lookback
        self.threshold = threshold
        self.catchup_frac = catchup_frac

    def score(self, data: pd.DataFrame) -> pd.Series:
        close = data["Close"]
        leader = _align(self.leader, close.index)

        lead_ret = leader.pct_change(self.lookback)
        our_ret = close.pct_change(self.lookback)
        strength = (lead_ret.abs() / (2 * self.threshold)).clip(upper=1.0)

        s = pd.Series(0.0, index=close.index)
        lag_up = (lead_ret > self.threshold) & (our_ret < lead_ret * self.catchup_frac)
        lag_down = (lead_ret < -self.threshold) & (our_ret > lead_ret * self.catchup_frac)
        s[lag_up] = strength[lag_up]
        s[lag_down] = -strength[lag_down]
        return s.fillna(0.0)


class CorrBreakDetector(BaseDetector):
    """Восстановление сломанной корреляции.

    Если в последние window баров корреляция падала ниже break_thr,
    а сейчас снова выше recovery_thr — голосуем в сторону инструмента,
    отставшего за период поломки (ожидаем схождение). Голос только на
    баре восстановления (фронт события), не размазан.
    """
    name = "corr_break"

    def __init__(self, pair: pd.Series, pair_name: str = "pair",
                 weight: float = 1.0, window: int = 30,
                 break_thr: float = 0.4, recovery_thr: float = 0.7,
                 rel_scale: float = 0.10):
        super().__init__(weight, {"pair": pair_name, "window": window,
                                  "break": break_thr, "recovery": recovery_thr})
        self.pair = pair
        self.window = window
        self.break_thr = break_thr
        self.recovery_thr = recovery_thr
        self.rel_scale = rel_scale

    def score(self, data: pd.DataFrame) -> pd.Series:
        close = data["Close"]
        pair = _align(self.pair, close.index)

        corr = close.pct_change().rolling(self.window).corr(pair.pct_change())
        was_broken = corr.rolling(self.window).min() < self.break_thr
        recovering = corr > self.recovery_thr
        condition = (was_broken & recovering).fillna(False)
        # Только фронт события: первый бар восстановления
        edge = condition & ~condition.shift(1, fill_value=False)

        # Кто отстал за период поломки: разница накопленных доходностей
        rel = close.pct_change(self.window) - pair.pct_change(self.window)
        direction = (-rel / self.rel_scale).clip(-1, 1)  # отстали -> голос вверх

        s = pd.Series(0.0, index=close.index)
        s[edge] = direction[edge]
        return s.fillna(0.0)
