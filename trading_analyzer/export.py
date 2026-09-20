"""Экспорт голосов детекторов, сделок и equity в БД для Grafana.

По умолчанию — SQLite (файл, ноль настройки). Тот же код пишет в MySQL,
если передать mysql_config (нужен pymysql: pip install pymysql).

Принципы:
  * батч-вставка одним commit'ом, не построчно;
  * score-серии детекторов считаются один раз целиком;
  * сделки спариваются в раунд-трипы (BUY+SELL -> одна строка);
  * повторный экспорт того же (symbol, timeframe) затирает старые строки —
    можно перегонять сколько угодно раз.

Таблицы:
  signals(ts, symbol, timeframe, detector, score, weight)
  ensemble_score(ts, symbol, timeframe, total)
  trades(symbol, timeframe, entry_time, exit_time, entry_price, exit_price,
         size, profit_pct, exit_reason)
  equity(ts, symbol, timeframe, equity, drawdown_pct)
  candles(ts, symbol, timeframe, open, high, low, close, volume)
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

import pandas as pd

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS signals (
        ts TIMESTAMP NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        timeframe VARCHAR(4) NOT NULL,
        detector VARCHAR(50) NOT NULL,
        score FLOAT NOT NULL,
        weight FLOAT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS ensemble_score (
        ts TIMESTAMP NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        timeframe VARCHAR(4) NOT NULL,
        total FLOAT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trades (
        symbol VARCHAR(20) NOT NULL,
        timeframe VARCHAR(4) NOT NULL,
        entry_time TIMESTAMP NOT NULL,
        exit_time TIMESTAMP,
        entry_price FLOAT,
        exit_price FLOAT,
        size FLOAT,
        profit_pct FLOAT,
        exit_reason VARCHAR(30)
    )""",
    """CREATE TABLE IF NOT EXISTS equity (
        ts TIMESTAMP NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        timeframe VARCHAR(4) NOT NULL,
        equity FLOAT NOT NULL,
        drawdown_pct FLOAT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS candles (
        ts TIMESTAMP NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        timeframe VARCHAR(4) NOT NULL,
        open FLOAT, high FLOAT, low FLOAT, close FLOAT, volume FLOAT
    )""",
]

# MySQL 8 не умеет CREATE INDEX IF NOT EXISTS — создаём с try/except
_INDEXES = [
    "CREATE INDEX idx_signals ON signals(symbol, timeframe, ts)",
    "CREATE INDEX idx_score ON ensemble_score(symbol, timeframe, ts)",
    "CREATE INDEX idx_trades ON trades(symbol, timeframe, entry_time)",
    "CREATE INDEX idx_equity ON equity(symbol, timeframe, ts)",
    "CREATE INDEX idx_candles ON candles(symbol, timeframe, ts)",
]

_TABLES = ["signals", "ensemble_score", "trades", "equity", "candles"]


class SignalExporter:
    def __init__(self, sqlite_path: str = "trading_analyzer.db",
                 mysql_config: Optional[Dict[str, Any]] = None):
        if mysql_config:
            import pymysql  # pip install pymysql
            self.conn = pymysql.connect(**mysql_config)
            self.ph = "%s"
        else:
            self.conn = sqlite3.connect(sqlite_path)
            self.ph = "?"
        cur = self.conn.cursor()
        for ddl in _SCHEMA:
            cur.execute(ddl)
        for idx in _INDEXES:
            try:
                cur.execute(idx)
            except Exception:
                pass  # индекс уже есть
        self.conn.commit()

    # ------------------------------------------------------------------ util
    def _wipe(self, symbol: str, timeframe: str):
        cur = self.conn.cursor()
        for t in _TABLES:
            key = "entry_time" if t == "trades" else "ts"  # noqa: F841
            cur.execute(f"DELETE FROM {t} WHERE symbol={self.ph} "
                        f"AND timeframe={self.ph}", (symbol, timeframe))
        self.conn.commit()

    def _insert(self, table: str, cols: List[str], rows: List[tuple]):
        if not rows:
            return
        ph = ",".join([self.ph] * len(cols))
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})"
        self.conn.cursor().executemany(sql, rows)
        self.conn.commit()

    @staticmethod
    def _pair_trades(trades: pd.DataFrame) -> List[dict]:
        """Плоские BUY/SELL -> раунд-трипы."""
        rounds, entry = [], None
        for _, t in trades.iterrows():
            if t["action"] == "BUY":
                entry = t
            elif t["action"] == "SELL" and entry is not None:
                rounds.append({
                    "entry_time": entry["time"], "exit_time": t["time"],
                    "entry_price": float(entry["price"]),
                    "exit_price": float(t["price"]),
                    "size": float(t["size"]),
                    "profit_pct": float(t.get("profit_pct", 0.0)),
                    "exit_reason": str(t.get("reason", "signal")),
                })
                entry = None
        if entry is not None:  # незакрытая позиция
            rounds.append({"entry_time": entry["time"], "exit_time": None,
                           "entry_price": float(entry["price"]),
                           "exit_price": None, "size": float(entry["size"]),
                           "profit_pct": None, "exit_reason": "open"})
        return rounds

    # ------------------------------------------------------------------ main
    def export_run(self, symbol: str, timeframe: str, data: pd.DataFrame,
                   ensemble, result) -> Dict[str, int]:
        """Экспорт одного прогона: данные, голоса, сделки, equity.

        ensemble — EnsembleStrategy (его детекторы дадут score-серии),
        result   — BacktestResult от engine.run(ensemble, data, ...).
        """
        self._wipe(symbol, timeframe)
        ts = [str(t) for t in data.index]

        # Свечи
        self._insert("candles",
                     ["ts", "symbol", "timeframe", "open", "high", "low",
                      "close", "volume"],
                     [(ts[i], symbol, timeframe,
                       float(data["Open"].iloc[i]), float(data["High"].iloc[i]),
                       float(data["Low"].iloc[i]), float(data["Close"].iloc[i]),
                       float(data["Volume"].iloc[i]))
                      for i in range(len(data))])

        # Голоса детекторов: серия целиком, без нулей (экономим строки)
        n_signals = 0
        for d in ensemble.detectors:
            s = d.score(data).clip(-1, 1).fillna(0.0)
            nz = s[s != 0]
            self._insert("signals",
                         ["ts", "symbol", "timeframe", "detector", "score",
                          "weight"],
                         [(str(t), symbol, timeframe, d.name, float(v),
                           float(d.weight)) for t, v in nz.items()])
            n_signals += len(nz)

        # Суммарный голос
        total = ensemble.total_score(data)
        self._insert("ensemble_score", ["ts", "symbol", "timeframe", "total"],
                     [(str(t), symbol, timeframe, float(v))
                      for t, v in total.items()])

        # Сделки
        rounds = self._pair_trades(result.trades) if len(result.trades) else []
        self._insert("trades",
                     ["symbol", "timeframe", "entry_time", "exit_time",
                      "entry_price", "exit_price", "size", "profit_pct",
                      "exit_reason"],
                     [(symbol, timeframe, str(r["entry_time"]),
                       str(r["exit_time"]) if r["exit_time"] is not None else None,
                       r["entry_price"], r["exit_price"], r["size"],
                       r["profit_pct"], r["exit_reason"]) for r in rounds])

        # Equity + просадка
        eq = result.equity
        dd = (eq / eq.cummax() - 1) * 100
        self._insert("equity",
                     ["ts", "symbol", "timeframe", "equity", "drawdown_pct"],
                     [(str(t), symbol, timeframe, float(e), float(d))
                      for t, e, d in zip(eq.index.astype(str), eq.values,
                                         dd.values)])

        return {"candles": len(data), "signals": n_signals,
                "ensemble_score": len(total), "trades": len(rounds),
                "equity": len(eq)}

    def close(self):
        self.conn.close()
