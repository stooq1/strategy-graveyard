# Grafana поверх голосов детекторов

## Данные

`python export_signals.py` создаёт `trading_analyzer.db` (SQLite) с таблицами:

| таблица | что внутри |
|---|---|
| `candles` | OHLCV по бару |
| `signals` | голос каждого детектора (только ненулевые), вес |
| `ensemble_score` | суммарный взвешенный голос ансамбля |
| `trades` | раунд-трипы: вход/выход, P&L, причина выхода (signal/stop/take_profit/open) |
| `equity` | кривая капитала и просадка |

Повторный запуск перезаписывает данные того же (symbol, timeframe) — безопасно гонять после каждого изменения.

## Вариант 1: SQLite (быстрый старт, без MySQL)

1. `grafana-cli plugins install frser-sqlite-datasource`, перезапустить Grafana.
2. Data source → SQLite → путь к `trading_analyzer.db`.

## Вариант 2: MySQL (ваш целевой стек)

```yaml
# docker-compose.yml
services:
  mysql:
    image: mysql:8
    environment:
      MYSQL_DATABASE: trading_analyzer
      MYSQL_USER: trading
      MYSQL_PASSWORD: trading
      MYSQL_ROOT_PASSWORD: root
    ports: ["3306:3306"]
    volumes: [mysql_data:/var/lib/mysql]
  grafana:
    image: grafana/grafana:latest
    ports: ["3000:3000"]
    depends_on: [mysql]
    volumes: [grafana_data:/var/lib/grafana]
volumes:
  mysql_data:
  grafana_data:
```

`docker compose up -d`, затем в `export_signals.py` раскомментировать
`MYSQL_CONFIG` (и `pip install pymysql`). Grafana: data source MySQL,
host `mysql:3306`.

## Панели (SQL для дашборда)

Везде `$symbol` — переменная дашборда (`SELECT DISTINCT symbol FROM candles`).

**Цена + сделки** (time series + аннотации):
```sql
SELECT ts AS time, close FROM candles
WHERE symbol = '$symbol' AND $__timeFilter(ts) ORDER BY ts;
-- аннотации входов/выходов:
SELECT entry_time AS time, 'BUY ' || ROUND(entry_price) AS text FROM trades
WHERE symbol = '$symbol';
SELECT exit_time AS time, exit_reason || ' ' || ROUND(profit_pct,1) || '%' AS text
FROM trades WHERE symbol = '$symbol' AND exit_time IS NOT NULL;
```

**Суммарный голос ансамбля** (рядом с ценой, отдельная панель):
```sql
SELECT ts AS time, total FROM ensemble_score
WHERE symbol = '$symbol' AND $__timeFilter(ts) ORDER BY ts;
```

**Голоса детекторов** (time series, по серии на детектор):
```sql
SELECT ts AS time, detector, score * weight AS vote FROM signals
WHERE symbol = '$symbol' AND $__timeFilter(ts) ORDER BY ts;
```

**Equity и просадка:**
```sql
SELECT ts AS time, equity FROM equity
WHERE symbol = '$symbol' AND $__timeFilter(ts) ORDER BY ts;
SELECT ts AS time, drawdown_pct FROM equity
WHERE symbol = '$symbol' AND $__timeFilter(ts) ORDER BY ts;
```

**Качество голосов детектора** (таблица: прав ли детектор через N баров —
ключевая панель для отбраковки):
```sql
SELECT s.detector,
       COUNT(*) AS votes,
       ROUND(AVG(CASE WHEN s.score > 0 AND f.close > c.close THEN 1
                      WHEN s.score < 0 AND f.close < c.close THEN 1
                      ELSE 0 END) * 100, 1) AS hit_rate_10bar
FROM signals s
JOIN candles c ON c.ts = s.ts AND c.symbol = s.symbol
JOIN candles f ON f.symbol = s.symbol
              AND f.ts = (SELECT MIN(ts) FROM candles
                          WHERE symbol = s.symbol AND ts > datetime(s.ts, '+10 days'))
WHERE s.symbol = '$symbol'
GROUP BY s.detector ORDER BY hit_rate_10bar DESC;
```
(для MySQL замените `datetime(s.ts, '+10 days')` на
`DATE_ADD(s.ts, INTERVAL 10 DAY)`)

**P&L по причинам выхода** (bar chart — видно, что забирают стопы):
```sql
SELECT exit_reason, COUNT(*) AS n, ROUND(AVG(profit_pct),2) AS avg_pnl,
       ROUND(SUM(profit_pct),1) AS total_pnl
FROM trades WHERE symbol = '$symbol' AND exit_time IS NOT NULL
GROUP BY exit_reason;
```

## Что искать на дашборде

1. Бары, где ансамбль голосовал сильно и ошибся — какой режим рынка был?
2. Детекторы с hit_rate < 50% — кандидаты на отбраковку или инверсию.
3. Стопы подряд в одном периоде — признак режима, где торговать не надо
   (заготовка под фильтр режима).
