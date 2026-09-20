"""Базовый класс стратегии.

Новая стратегия = новый файл в strategies/ с классом-наследником BaseStrategy,
реализующим generate_signals(). Больше ничего менять не нужно.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import pandas as pd

# Значения колонки 'signal'
BUY, SELL, HOLD = 1, -1, 0


class BaseStrategy(ABC):
    """Стратегия получает OHLCV и возвращает колонку сигналов."""

    name: str = "base"

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self.params = params or {}

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Возвращает pd.Series со значениями BUY(1) / SELL(-1) / HOLD(0),
        выровненную по индексу data.

        Сигнал на баре i считается ТОЛЬКО по данным баров <= i
        (исполнение произойдёт по Open бара i+1 — это делает движок).
        """

    def describe(self) -> str:
        p = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({p})"
