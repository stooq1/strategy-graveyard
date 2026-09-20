#!/bin/bash
# Экспорт минутных баров + признаков стакана из crypto.* в crypto_data/*.csv
# Запуск на СЕРВЕРЕ:  bash collector/export_crypto.sh
# Затем тянем на мак:  scp ...:~/collector/crypto_data/*.csv ./crypto_data/
set -e
cd "$(dirname "$0")"
mkdir -p crypto_data

SYMBOLS="BTCUSDT ETHUSDT SOLUSDT LINKUSDT LTCUSDT ATOMUSDT"
CH="sudo docker compose exec -T clickhouse clickhouse-client"

for SYM in $SYMBOLS; do
  echo "Экспорт $SYM..."
  $CH -q "
SELECT t.minute AS time, t.open AS open, t.high AS high, t.low AS low,
       t.close AS close, t.volume AS volume,
       g.bid_wall_price AS bid_wall_price, g.bid_wall_qty AS bid_wall_qty,
       g.offer_wall_price AS offer_wall_price, g.offer_wall_qty AS offer_wall_qty,
       g.bid_total AS bid_total, g.offer_total AS offer_total
FROM (
  SELECT toStartOfMinute(ts) AS minute,
         argMin(price, ts) AS open, max(price) AS high,
         min(price) AS low, argMax(price, ts) AS close,
         sum(qty) AS volume
  FROM crypto.trades WHERE symbol = '$SYM'
  GROUP BY minute
) AS t
LEFT JOIN (
  SELECT toStartOfMinute(ts) AS minute,
         argMaxIf(price, quantity, side = 'BID') AS bid_wall_price,
         maxIf(quantity, side = 'BID') AS bid_wall_qty,
         argMaxIf(price, quantity, side = 'OFFER') AS offer_wall_price,
         maxIf(quantity, side = 'OFFER') AS offer_wall_qty,
         sumIf(quantity, side = 'BID') / uniqExact(ts) AS bid_total,
         sumIf(quantity, side = 'OFFER') / uniqExact(ts) AS offer_total
  FROM crypto.glass WHERE symbol = '$SYM'
  GROUP BY minute
) AS g USING (minute)
ORDER BY time
FORMAT CSVWithNames" > "crypto_data/$SYM.csv"
  wc -l "crypto_data/$SYM.csv"
done
echo "Готово: collector/crypto_data/"
