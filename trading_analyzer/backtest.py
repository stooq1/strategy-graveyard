"""Движок бэктестинга и метрики.

Принципы:
  * сигнал бара i исполняется по Open бара i+1 (никакого lookahead);
  * стопы/тейки срабатывают интрабарно по High/Low с учётом гэпов:
    если бар открылся уже за уровнем — исполнение по Open, а не по уровню;
  * при конфликте стопа и тейка в одном баре консервативно берём стоп;
  * allow_short=True: SELL-сигнал открывает шорт (и переворачивает лонг),
    стопы/тейки/трейлинг зеркальны. По умолчанию выключено — старые
    прогоны воспроизводятся бит-в-бит.

Действия в trades: BUY/SELL — лонг, SHORT/COVER — шорт.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .strategy import BUY, SELL, BaseStrategy
from .risk import RiskParams

SECONDS_PER_YEAR = 365.25 * 24 * 3600
NAN = float("nan")


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    metrics: Dict[str, float]
    equity: pd.Series = field(repr=False)
    trades: pd.DataFrame = field(repr=False)


class BacktestEngine:
    def __init__(self, initial_capital: float = 10_000,
                 commission: float = 0.001):
        self.initial_capital = initial_capital
        self.commission = commission

    def run(self, strategy: BaseStrategy, data: pd.DataFrame,
            symbol: str = "?", risk: Optional[RiskParams] = None,
            allow_short: bool = False, maker=None) -> BacktestResult:
        signals = strategy.generate_signals(data)

        cash = self.initial_capital
        pos = 0.0  # подписанное количество: >0 лонг, <0 шорт
        entry_price = 0.0
        entry_atr = NAN
        extreme = 0.0  # high с входа (лонг) / low с входа (шорт)
        # мейкер: ожидающая лимитная заявка на вход
        pend_side = 0          # 1 лонг, -1 шорт, 0 нет заявки
        pend_limit = 0.0
        pend_deadline = -1
        trades: List[Dict[str, Any]] = []
        equity = np.empty(len(data))

        open_ = data["Open"].values
        high = data["High"].values
        low = data["Low"].values
        close = data["Close"].values
        sig = signals.values
        com = self.commission

        atr = None
        if risk is not None and risk.uses_atr():
            atr = self._atr(data, risk.atr_period).values

        def frac_for(price: float, short: bool) -> float:
            if risk is None:
                return 1.0
            if short:
                stop0 = risk.stop_level_short(price, price, entry_atr)
                dist = (stop0 - price) / price if stop0 else None
            else:
                stop0 = risk.stop_level(price, price, entry_atr)
                dist = (price - stop0) / price if stop0 else None
            return risk.position_frac(dist)

        def open_long(price: float, fee: float, when):
            nonlocal cash, pos, entry_price, extreme, entry_atr
            invest = cash * frac_for(price, short=False)
            pos = invest / price * (1 - fee)
            cash -= invest
            entry_price, extreme = price, price
            trades.append({"time": when, "action": "BUY",
                           "price": price, "size": pos})

        def open_short(price: float, fee: float, when):
            nonlocal cash, pos, entry_price, extreme, entry_atr
            size = cash * frac_for(price, short=True) / price
            pos = -size
            cash += size * price * (1 - fee)
            entry_price, extreme = price, price
            trades.append({"time": when, "action": "SHORT",
                           "price": price, "size": size})

        for i in range(len(data)):
            o = open_[i]
            # --- 1. Входы по сигналу предыдущего бара ----------------------
            if i > 0 and o > 0 and maker is None:
                # TAKER: мгновенно по Open текущего бара
                s_prev = sig[i - 1]
                if s_prev == BUY and pos <= 0:
                    if pos < 0:
                        cash -= (-pos) * o * (1 + com)
                        trades.append({"time": data.index[i], "action": "COVER",
                                       "price": o, "size": -pos,
                                       "reason": "signal",
                                       "profit_pct": (entry_price / o - 1) * 100})
                        pos = 0.0
                    entry_atr = atr[i - 1] if atr is not None else NAN
                    open_long(o, com, data.index[i])
                elif s_prev == SELL and pos >= 0:
                    if pos > 0:
                        cash += pos * o * (1 - com)
                        trades.append({"time": data.index[i], "action": "SELL",
                                       "price": o, "size": pos,
                                       "reason": "signal",
                                       "profit_pct": (o / entry_price - 1) * 100})
                        pos = 0.0
                    if allow_short:
                        entry_atr = atr[i - 1] if atr is not None else NAN
                        open_short(o, com, data.index[i])
            elif i > 0 and o > 0 and maker is not None:
                # MAKER: выход по сигналу — takerом (как стоп), вход — лимитом
                s_prev = sig[i - 1]
                if s_prev == BUY and pos < 0:
                    cash -= (-pos) * o * (1 + com)
                    trades.append({"time": data.index[i], "action": "COVER",
                                   "price": o, "size": -pos, "reason": "signal",
                                   "profit_pct": (entry_price / o - 1) * 100})
                    pos = 0.0
                elif s_prev == SELL and pos > 0:
                    cash += pos * o * (1 - com)
                    trades.append({"time": data.index[i], "action": "SELL",
                                   "price": o, "size": pos, "reason": "signal",
                                   "profit_pct": (o / entry_price - 1) * 100})
                    pos = 0.0
                # постановка лимитной заявки, если плоско и заявки ещё нет
                if pos == 0 and pend_side == 0:
                    if s_prev == BUY:
                        pend_side = 1
                        pend_limit = o * (1 - maker.offset)
                        pend_deadline = i + maker.fill_window
                    elif s_prev == SELL and allow_short:
                        pend_side = -1
                        pend_limit = o * (1 + maker.offset)
                        pend_deadline = i + maker.fill_window
                # проверка исполнения заявки на этом баре (adverse selection)
                if pend_side != 0 and pos == 0:
                    entry_atr = atr[i - 1] if atr is not None else NAN
                    if pend_side > 0 and low[i] <= pend_limit:
                        open_long(pend_limit, maker.fee, data.index[i])
                        pend_side = 0
                    elif pend_side < 0 and high[i] >= pend_limit:
                        open_short(pend_limit, maker.fee, data.index[i])
                        pend_side = 0
                    elif i >= pend_deadline:
                        pend_side = 0  # не исполнилась — пропуск входа

            # --- 2. Интрабарные стопы/тейки --------------------------------
            if pos != 0 and risk is not None:
                exit_price, reason = None, None
                if pos > 0:
                    stop = risk.stop_level(entry_price, extreme, entry_atr)
                    tp = risk.take_level(entry_price, entry_atr)
                    if stop is not None and low[i] <= stop:
                        exit_price = o if o < stop else stop
                        reason = "stop"
                    elif tp is not None and high[i] >= tp:
                        exit_price = o if o > tp else tp
                        reason = "take_profit"
                    if exit_price is not None:
                        cash += pos * exit_price * (1 - com)
                        trades.append({"time": data.index[i], "action": "SELL",
                                       "price": exit_price, "size": pos,
                                       "reason": reason,
                                       "profit_pct": (exit_price / entry_price - 1) * 100})
                        pos = 0.0
                    else:
                        extreme = max(extreme, high[i])
                else:
                    stop = risk.stop_level_short(entry_price, extreme, entry_atr)
                    tp = risk.take_level_short(entry_price, entry_atr)
                    if stop is not None and high[i] >= stop:
                        exit_price = o if o > stop else stop
                        reason = "stop"
                    elif tp is not None and low[i] <= tp:
                        exit_price = o if o < tp else tp
                        reason = "take_profit"
                    if exit_price is not None:
                        cash -= (-pos) * exit_price * (1 + com)
                        trades.append({"time": data.index[i], "action": "COVER",
                                       "price": exit_price, "size": -pos,
                                       "reason": reason,
                                       "profit_pct": (entry_price / exit_price - 1) * 100})
                        pos = 0.0
                    else:
                        extreme = min(extreme, low[i])

            equity[i] = cash + pos * close[i]

        equity_s = pd.Series(equity, index=data.index, name="equity")
        trades_df = pd.DataFrame(trades)
        metrics = self._metrics(equity_s, trades_df, data)
        name = strategy.describe() + (f" | risk[{risk.describe()}]" if risk else "")
        if allow_short:
            name += " | long-short"
        if maker is not None:
            name += f" | {maker.describe()}"
        return BacktestResult(name, symbol, metrics, equity_s, trades_df)

    @staticmethod
    def _atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Average True Range (скользящее среднее истинного диапазона)."""
        high, low, close = data["High"], data["Low"], data["Close"]
        prev_close = close.shift()
        tr = pd.concat([high - low,
                        (high - prev_close).abs(),
                        (low - prev_close).abs()], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    # ---------------------------------------------------------------- metrics
    def _metrics(self, equity: pd.Series, trades: pd.DataFrame,
                 data: pd.DataFrame) -> Dict[str, float]:
        returns = equity.pct_change().dropna()
        ppy = self._periods_per_year(equity.index)

        sharpe = 0.0
        if len(returns) > 1 and returns.std() > 0:
            sharpe = float(np.sqrt(ppy) * returns.mean() / returns.std())

        if len(trades):
            closed = trades[trades["action"].isin(("SELL", "COVER"))]
        else:
            closed = trades
        win_rate = 0.0
        if len(closed):
            win_rate = float((closed["profit_pct"] > 0).mean() * 100)

        bh_return = float((data["Close"].iloc[-1] / data["Open"].iloc[0] - 1) * 100)

        return {
            "total_return_%": float((equity.iloc[-1] / equity.iloc[0] - 1) * 100),
            "buy_hold_%": bh_return,
            "sharpe": sharpe,
            "max_drawdown_%": float(
                ((equity / equity.cummax()) - 1).min() * 100),
            "trades": int(len(closed)),
            "win_rate_%": win_rate,
        }

    @staticmethod
    def _periods_per_year(index: pd.DatetimeIndex) -> float:
        """Годовая частота баров из медианного интервала (работает для d/h/5m)."""
        if len(index) < 3:
            return 252.0
        median_dt = pd.Series(index).diff().median().total_seconds()
        return SECONDS_PER_YEAR / median_dt if median_dt > 0 else 252.0
