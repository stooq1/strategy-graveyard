"""Прогон ансамбля и экспорт голосов/сделок/equity в БД для Grafana.

По умолчанию пишет в SQLite-файл trading_analyzer.db рядом с проектом.
Для MySQL раскомментируйте MYSQL_CONFIG (нужен pip install pymysql).

Запуск:  python export_signals.py [путь_к_БД]
        (по умолчанию trading_analyzer.db; на сетевых дисках, где SQLite
         не может взять lock, укажите локальный путь)
"""
import sys

from trading_analyzer import StooqData, BacktestEngine
from trading_analyzer.export import SignalExporter
from trading_analyzer.risk import RiskParams
from trading_analyzer.strategies import EnsembleStrategy
from trading_analyzer.detectors import (
    RoundLevelDetector, DivergenceDetector, ExhaustionDetector,
    FairPriceDetector, LeadLagDetector,
)

TIMEFRAME = "d"
START_DATE = "2020-01-01"
PAIRS = {"btc.v": "eth.v", "eth.v": "btc.v"}

RISK = RiskParams(stop_loss_pct=None, atr_stop_mult=3.0, risk_per_trade=0.02)

# Для docker-compose из проекта: --mysql подключается сюда
MYSQL_CONFIG = {"host": "127.0.0.1", "port": 3306, "user": "trading",
                "password": "trading", "database": "trading_analyzer"}


def main():
    use_mysql = "--mysql" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    db_path = args[0] if args else "trading_analyzer.db"
    data = StooqData("stooq_data")
    engine = BacktestEngine(initial_capital=10_000, commission=0.001)
    exporter = SignalExporter(db_path,
                              mysql_config=MYSQL_CONFIG if use_mysql else None)

    closes = {s: data.load(s, TIMEFRAME)["Close"].loc[START_DATE:]
              for s in PAIRS}

    for symbol, sat in PAIRS.items():
        df = data.load(symbol, TIMEFRAME).loc[START_DATE:]
        ensemble = EnsembleStrategy(
            [RoundLevelDetector(weight=1.0),
             DivergenceDetector(weight=1.5),
             ExhaustionDetector(weight=1.0),
             FairPriceDetector(closes[sat], sat, weight=1.2, lookback=50),
             LeadLagDetector(closes[sat], sat, weight=1.0,
                             lookback=10, threshold=0.05)],
            entry_threshold=1.3, exit_threshold=-1.0, cooldown_bars=5)

        result = engine.run(ensemble, df, symbol, risk=RISK)
        counts = exporter.export_run(symbol, TIMEFRAME, df, ensemble, result)
        print(f"{symbol}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    exporter.close()
    target = "MySQL (127.0.0.1:3306)" if use_mysql else db_path
    print(f"\nГотово: {target} (см. GRAFANA.md для дашбордов)")


if __name__ == "__main__":
    main()
