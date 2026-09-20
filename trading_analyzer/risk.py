"""Параметры риск-менеджмента: фиксированные и ATR-адаптивные стопы.

Один класс, а не два: ATR-поля опциональны и комбинируются с фиксированными
(действующий стоп — самый консервативный из заданных). ATR фиксируется в
момент входа — стоп не "дышит" задним числом.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


def _valid(x: Optional[float]) -> bool:
    return x is not None and not math.isnan(x)


@dataclass
class RiskParams:
    stop_loss_pct: Optional[float] = 0.05      # стоп-лосс от цены входа
    take_profit_pct: Optional[float] = None    # тейк-профит от цены входа
    trailing_stop_pct: Optional[float] = None  # трейлинг от максимума с входа
    atr_stop_mult: Optional[float] = None      # стоп на N*ATR ниже входа
    atr_take_mult: Optional[float] = None      # тейк на N*ATR выше входа
    atr_period: int = 14
    max_position_frac: float = 1.0             # макс. доля капитала в позиции
    risk_per_trade: Optional[float] = None     # доля капитала под риском (0.02 = 2%)

    def uses_atr(self) -> bool:
        return bool(self.atr_stop_mult or self.atr_take_mult)

    def stop_level(self, entry_price: float, high_since_entry: float,
                   entry_atr: Optional[float] = None) -> Optional[float]:
        """Действующий стоп: максимум (консервативнее) из заданных уровней."""
        levels = []
        if self.stop_loss_pct:
            levels.append(entry_price * (1 - self.stop_loss_pct))
        if self.atr_stop_mult and _valid(entry_atr):
            levels.append(entry_price - self.atr_stop_mult * entry_atr)
        if self.trailing_stop_pct:
            levels.append(high_since_entry * (1 - self.trailing_stop_pct))
        return max(levels) if levels else None

    def take_level(self, entry_price: float,
                   entry_atr: Optional[float] = None) -> Optional[float]:
        """Действующий тейк: минимум (ближайший) из заданных уровней."""
        levels = []
        if self.take_profit_pct:
            levels.append(entry_price * (1 + self.take_profit_pct))
        if self.atr_take_mult and _valid(entry_atr):
            levels.append(entry_price + self.atr_take_mult * entry_atr)
        return min(levels) if levels else None

    # ----------------------------------------------------- зеркало для шорта
    def stop_level_short(self, entry_price: float, low_since_entry: float,
                         entry_atr: Optional[float] = None) -> Optional[float]:
        """Стоп шорта НАД ценой: минимум (консервативнее) из уровней."""
        levels = []
        if self.stop_loss_pct:
            levels.append(entry_price * (1 + self.stop_loss_pct))
        if self.atr_stop_mult and _valid(entry_atr):
            levels.append(entry_price + self.atr_stop_mult * entry_atr)
        if self.trailing_stop_pct:
            levels.append(low_since_entry * (1 + self.trailing_stop_pct))
        return min(levels) if levels else None

    def take_level_short(self, entry_price: float,
                         entry_atr: Optional[float] = None) -> Optional[float]:
        """Тейк шорта ПОД ценой: максимум (ближайший) из уровней."""
        levels = []
        if self.take_profit_pct:
            levels.append(entry_price * (1 - self.take_profit_pct))
        if self.atr_take_mult and _valid(entry_atr):
            levels.append(entry_price - self.atr_take_mult * entry_atr)
        return max(levels) if levels else None

    def position_frac(self, stop_distance_frac: Optional[float] = None) -> float:
        """Доля капитала в сделку (fixed-fractional sizing).

        stop_distance_frac — фактическое расстояние до стопа в долях цены
        входа (учитывает и ATR-стоп). Если задан risk_per_trade, размер
        подбирается так, чтобы срабатывание стопа стоило ~risk_per_trade
        капитала.
        """
        if self.risk_per_trade and stop_distance_frac and stop_distance_frac > 0:
            return min(self.max_position_frac,
                       self.risk_per_trade / stop_distance_frac)
        return self.max_position_frac

    def describe(self) -> str:
        parts = []
        if self.stop_loss_pct:
            parts.append(f"sl={self.stop_loss_pct:.0%}")
        if self.take_profit_pct:
            parts.append(f"tp={self.take_profit_pct:.0%}")
        if self.trailing_stop_pct:
            parts.append(f"trail={self.trailing_stop_pct:.0%}")
        if self.atr_stop_mult:
            parts.append(f"atr_sl={self.atr_stop_mult}x")
        if self.atr_take_mult:
            parts.append(f"atr_tp={self.atr_take_mult}x")
        if self.risk_per_trade:
            parts.append(f"rpt={self.risk_per_trade:.0%}")
        return ",".join(parts) or "none"
