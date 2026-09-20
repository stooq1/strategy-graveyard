"""Фильтры рыночного режима (ворота для ансамбля).

Ворота не голосуют — они глушат голоса ансамбля, когда режим не подходит.
Выходы по стопам/тейкам продолжают работать (риск-менеджмент не отключается).
"""
from __future__ import annotations

import pandas as pd


class VolatilityGate:
    """Пропускает сигналы только в «горячем» режиме: реализованная
    волатильность выше mult * её скользящей медианы.

    vol_window : окно реализованной волатильности (баров)
    ref_window : окно медианы-эталона (баров, ~неделя минуток)
    mult       : насколько текущая волатильность должна превышать эталон
    """

    def __init__(self, vol_window: int = 240, ref_window: int = 4200,
                 mult: float = 1.3):
        self.vol_window = vol_window
        self.ref_window = ref_window
        self.mult = mult

    def mask(self, data: pd.DataFrame) -> pd.Series:
        vol = data["Close"].pct_change().rolling(
            self.vol_window, min_periods=self.vol_window // 2).std()
        ref = vol.rolling(self.ref_window,
                          min_periods=self.ref_window // 4).median()
        return (vol > self.mult * ref).fillna(False)

    def describe(self) -> str:
        return f"vol>{self.mult}x_med({self.vol_window}/{self.ref_window})"


class TrendGate:
    """Пропускает сигналы, только когда цена заметно ушла от своей средней
    (рынок в движении, а не в дрейфе вокруг MA).

    ma_window : окно средней (баров; 840 минуток ~ торговый день FORTS)
    threshold : минимальное |close/MA - 1| (0.003 = 0.3%)
    """

    def __init__(self, ma_window: int = 840, threshold: float = 0.003):
        self.ma_window = ma_window
        self.threshold = threshold

    def mask(self, data: pd.DataFrame) -> pd.Series:
        ma = data["Close"].rolling(self.ma_window,
                                   min_periods=self.ma_window // 4).mean()
        return ((data["Close"] / ma - 1).abs() > self.threshold).fillna(False)

    def describe(self) -> str:
        return f"trend|c/ma({self.ma_window})-1|>{self.threshold}"


class PrevDayVolGate:
    """Предиктивный фильтр: торговать весь день D, только если размах
    ПРЕДЫДУЩЕГО дня (D-1) был не ниже порога.

    Опирается на кластеризацию волатильности (за бурным днём чаще бурный).
    Причинный (вчерашний размах известен сегодня) и, в отличие от реактивного
    VolatilityGate, не запаздывает внутри дня — решение принято до его начала.

    min_range : минимальный размах (high-low)/low предыдущего дня, доля (0.05=5%)
    """

    def __init__(self, min_range: float = 0.05):
        self.min_range = min_range

    def mask(self, data):
        import pandas as pd
        day = pd.Series(data.index.date, index=data.index)
        hi = data["High"].groupby(day).transform("max")
        lo = data["Low"].groupby(day).transform("min")
        day_range = (hi - lo) / lo
        # размах каждого дня -> сдвиг на 1 день (вчерашний для сегодняшнего)
        per_day = day_range.groupby(day).first()
        prev = per_day.shift(1)
        mapped = day.map(prev)
        return (mapped >= self.min_range).fillna(False)

    def describe(self) -> str:
        return f"prevday_range>={self.min_range:.0%}"
