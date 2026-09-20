#!/bin/bash
# Экспорт минутных баров + признаков стакана из ClickHouse в moex_data/*.csv
# Запуск:  ./export_moex.sh   (контейнер clickhouse должен быть healthy)
set -e
mkdir -p moex_data

CODES="SiH0 SiM0 SiU0 RIH0 RIM0 RIU0"

for CODE in $CODES; do
  echo "Экспорт $CODE..."
  docker compose exec -T clickhouse clickhouse-client -q "
SELECT t.time AS time, t.open AS open, t.high AS high, t.low AS low,
       t.close AS close, t.volume AS volume,
       g.bid_wall_price AS bid_wall_price, g.bid_wall_qty AS bid_wall_qty,
       g.offer_wall_price AS offer_wall_price, g.offer_wall_qty AS offer_wall_qty,
       g.bid_total AS bid_total, g.offer_total AS offer_total
FROM (
  SELECT toStartOfMinute(datetime) AS time,
         argMin(price, (datetime, trancId)) AS open,
         max(price) AS high, min(price) AS low,
         argMax(price, (datetime, trancId)) AS close,
         sum(qty) AS volume
  FROM quik.anonymous_transactions
  WHERE code = '$CODE' AND type = 'Futures' AND event_date >= '2020-02-22'
  GROUP BY time
) AS t
LEFT JOIN (
  SELECT toStartOfMinute(datetime) AS time,
         argMaxIf(price, quantity, type = 'BID') AS bid_wall_price,
         maxIf(quantity, type = 'BID') AS bid_wall_qty,
         argMaxIf(price, quantity, type = 'OFFER') AS offer_wall_price,
         maxIf(quantity, type = 'OFFER') AS offer_wall_qty,
         sumIf(quantity, type = 'BID') AS bid_total,
         sumIf(quantity, type = 'OFFER') AS offer_total
  FROM quik.glass
  WHERE code = '$CODE' AND event_date >= '2020-02-22'
  GROUP BY time
) AS g USING (time)
ORDER BY time
FORMAT CSVWithNames" > "moex_data/$CODE.csv"
  wc -l "moex_data/$CODE.csv"
done
echo "Готово: moex_data/"
