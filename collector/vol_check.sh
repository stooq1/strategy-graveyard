#!/bin/bash
# Дневная волатильность собранных данных: размах (high-low)/low и число сделок.
# Помогает решить, появился ли волатильный день для перепрогона гипотез.
# Запуск на СЕРВЕРЕ:  bash collector/vol_check.sh
cd "$(dirname "$0")"
sudo docker compose exec -T clickhouse clickhouse-client -q "
SELECT symbol,
       toDate(ts) AS day,
       round((max(price) - min(price)) / min(price) * 100, 2) AS range_pct,
       count() AS trades
FROM crypto.trades
GROUP BY symbol, day
ORDER BY symbol, day
FORMAT PrettyCompact"
