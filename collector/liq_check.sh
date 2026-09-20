#!/bin/bash
# Ликвидации по дням: сколько, на какую сумму, лонги vs шорты, пик за 5 минут.
# Отличает каскад ликвидаций (паника) от просто волатильного дня.
# side=SELL — принудительно продан лонг, side=BUY — принудительно выкуплен шорт
# (семантика стрима forceOrder Binance). Стрим отдаёт не больше одного события
# в секунду на символ, так что счётчики — нижняя оценка. Данные с 2026-07-16.
# Запуск на СЕРВЕРЕ:  bash collector/liq_check.sh
cd "$(dirname "$0")"
sudo docker compose exec -T clickhouse clickhouse-client -q "
SELECT symbol, day,
       sum(cnt)                  AS liqs,
       round(sum(usd) / 1e6, 2)  AS notional_musd,
       sum(long_cnt)             AS long_liqs,
       sum(short_cnt)            AS short_liqs,
       max(cnt)                  AS peak_5min
FROM (
  SELECT symbol, toDate(ts) AS day, toStartOfFiveMinutes(toDateTime(ts)) AS b,
         count() AS cnt, sum(price * qty) AS usd,
         countIf(side = 'SELL') AS long_cnt, countIf(side = 'BUY') AS short_cnt
  FROM crypto.liquidations
  GROUP BY symbol, day, b
)
GROUP BY symbol, day
ORDER BY symbol, day
FORMAT PrettyCompact"
