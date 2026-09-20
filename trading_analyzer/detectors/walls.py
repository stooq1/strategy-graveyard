"""Детекторы по стакану («плиты»).

Работают с DataFrame, где помимо OHLCV есть минутные признаки стакана:
  bid_wall_price / bid_wall_qty   — цена и объём крупнейшей бид-плиты
  offer_wall_price / offer_wall_qty — то же для офферов
  bid_total / offer_total          — суммарные объёмы сторон

Гипотезы:
  WallBounceDetector  — цена прижалась к крупной плите -> отскок;
  WallPullDetector    — плита, у которой цена тёрлась, исчезла -> пробой;
  ImbalanceDetector   — перекос бид/оффер предсказывает направление.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BaseDetector

_WALL_COLS = ["bid_wall_price", "bid_wall_qty", "offer_wall_price",
              "offer_wall_qty", "bid_total", "offer_total"]


def _walls(data: pd.DataFrame) -> pd.DataFrame:
    """Колонки стакана с NaN вместо нулей (минуты без снимков стакана)."""
    w = data[_WALL_COLS].replace(0, np.nan)
    return w


class WallBounceDetector(BaseDetector):
    """Цена в пределах prox_frac от плиты, плита крупная (в big_mult раз
    больше скользящей медианы) -> голос на отскок: от бида +, от оффера -."""
    name = "wall_bounce"

    def __init__(self, weight: float = 1.0, prox_frac: float = 0.0005,
                 big_mult: float = 3.0, med_window: int = 600):
        super().__init__(weight, {"prox": prox_frac, "big": big_mult})
        self.prox_frac = prox_frac
        self.big_mult = big_mult
        self.med_window = med_window

    def score(self, data: pd.DataFrame) -> pd.Series:
        w = _walls(data)
        close = data["Close"]

        bid_med = w["bid_wall_qty"].rolling(self.med_window, min_periods=60).median()
        offer_med = w["offer_wall_qty"].rolling(self.med_window, min_periods=60).median()
        big_bid = w["bid_wall_qty"] > self.big_mult * bid_med
        big_offer = w["offer_wall_qty"] > self.big_mult * offer_med

        near_bid = (close - w["bid_wall_price"]) / close < self.prox_frac
        near_offer = (w["offer_wall_price"] - close) / close < self.prox_frac

        # Сила = насколько плита больше порога (1.0 при 2x порога)
        bid_strength = (w["bid_wall_qty"] / (self.big_mult * bid_med) / 2).clip(upper=1.0)
        offer_strength = (w["offer_wall_qty"] / (self.big_mult * offer_med) / 2).clip(upper=1.0)

        s = pd.Series(0.0, index=data.index)
        buy = (near_bid & big_bid).fillna(False)
        sell = (near_offer & big_offer).fillna(False)
        s[buy] = bid_strength[buy]
        s[sell] = -offer_strength[sell]
        return s.fillna(0.0)


class WallPullDetector(BaseDetector):
    """Снятие плиты: объём крупнейшей плиты упал ниже pull_frac от максимума
    за lookback минут, при этом цена недавно тёрлась около неё ->
    голос в сторону пробоя (оффер снят -> вверх, бид снят -> вниз)."""
    name = "wall_pull"

    def __init__(self, weight: float = 1.0, lookback: int = 30,
                 pull_frac: float = 0.3, prox_frac: float = 0.001):
        super().__init__(weight, {"lookback": lookback, "pull": pull_frac,
                                  "prox": prox_frac})
        self.lookback = lookback
        self.pull_frac = pull_frac
        self.prox_frac = prox_frac

    def score(self, data: pd.DataFrame) -> pd.Series:
        w = _walls(data)
        close = data["Close"]

        s = pd.Series(0.0, index=data.index)
        for side, sign in (("offer", +1.0), ("bid", -1.0)):
            qty = w[f"{side}_wall_qty"]
            price = w[f"{side}_wall_price"]
            peak = qty.rolling(self.lookback, min_periods=5).max()
            pulled = qty < self.pull_frac * peak
            # цена тёрлась у плиты в последние lookback минут
            dist = ((price - close) * sign) / close  # >=0 когда плита «впереди»
            was_near = (dist.abs() < self.prox_frac).rolling(
                self.lookback, min_periods=1).max().astype(bool)
            event = (pulled & was_near).fillna(False)
            # только фронт события
            edge = event & ~event.shift(1, fill_value=False)
            s[edge] = sign
        return s


class ImbalanceDetector(BaseDetector):
    """Z-score лог-отношения сумм бид/оффер. Перекос в биды -> голос вверх."""
    name = "imbalance"

    def __init__(self, weight: float = 1.0, window: int = 240,
                 max_z: float = 3.0):
        super().__init__(weight, {"window": window, "max_z": max_z})
        self.window = window
        self.max_z = max_z

    def score(self, data: pd.DataFrame) -> pd.Series:
        w = _walls(data)
        ratio = np.log(w["bid_total"] / w["offer_total"])
        mean = ratio.rolling(self.window, min_periods=60).mean()
        std = ratio.rolling(self.window, min_periods=60).std()
        z = (ratio - mean) / std.replace(0, np.nan)
        return (z / self.max_z).clip(-1, 1).fillna(0.0)
