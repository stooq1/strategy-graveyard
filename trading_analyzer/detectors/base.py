"""Базовый класс детектора — кирпичик ансамбля.

Детектор не торгует. Он смотрит на данные и на каждом баре выдаёт score:
  +1.0  — сильный голос за лонг (точка входа)
  -1.0  — сильный голос за выход/против лонга
   0.0  — молчит
Промежуточные значения — уверенность. У детектора есть вес: вклад в общий
счёт ансамбля = score * weight.

Правило то же, что у стратегий: score бара i считается ТОЛЬКО по данным
баров <= i.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import pandas as pd


class BaseDetector(ABC):
    name: str = "base"

    def __init__(self, weight: float = 1.0,
                 params: Optional[Dict[str, Any]] = None):
        self.weight = weight
        self.params = params or {}

    @abstractmethod
    def score(self, data: pd.DataFrame) -> pd.Series:
        """pd.Series float в диапазоне [-1, +1], индекс = data.index."""

    def describe(self) -> str:
        p = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}(w={self.weight}{', ' + p if p else ''})"


class GatedDetector(BaseDetector):
    """Детектор + персональные ворота режима: голос пропускается, только
    когда gate.mask(data) истинен. Режимная специализация: каждый детектор
    работает в «своём» рынке."""

    def __init__(self, detector: BaseDetector, gate):
        super().__init__(detector.weight,
                         {"inner": detector.describe(),
                          "gate": gate.describe()})
        self.name = f"{detector.name}@{gate.describe()}"
        self.detector = detector
        self.gate = gate

    def score(self, data):
        s = self.detector.score(data)
        return s.where(self.gate.mask(data), 0.0)
