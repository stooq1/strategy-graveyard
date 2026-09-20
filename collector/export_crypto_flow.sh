#!/bin/bash
# Экспорт минуток + ПОТОК АГРЕССОРОВ + ликвидации/OI/funding из crypto.* в crypto_data/flow_*.csv
# Запуск на СЕРВЕРЕ:  bash collector/export_crypto_flow.sh
# Локально одной командой: bash fetch_crypto_flow.sh
set -e
cd "$(dirname "$0")"
mkdir -p crypto_data

SYMBOLS="BTCUSDT ETHUSDT SOLUSDT LINKUSDT LTCUSDT ATOMUSDT"
CH="sudo docker compose exec -T clickhouse clickhouse-client"

for SYM in $SYMBOLS; do
  echo "Экспорт $SYM (flow)..."
  $CH -q "
SELECT * FROM (
SELECT * FROM (
SELECT * FROM (
SELECT * FROM (
SELECT * FROM (
SELECT * FROM (
SELECT * FROM (
  SELECT toStartOfMinute(ts) AS time,
         argMin(price, ts) AS open, max(price) AS high, min(price) AS low,
         argMax(price, ts) AS close, sum(qty) AS volume,
         sumIf(qty, is_buyer_maker = 0) AS taker_buy,
         sumIf(qty, is_buyer_maker = 1) AS taker_sell,
         count() AS n_trades, uniqExact(price) AS n_prices,
         arrayCount(x -> x = max(price), groupArray(price)) AS touch_high,
         arrayCount(x -> x = min(price), groupArray(price)) AS touch_low,
         arraySum((q, p) -> q * (p = max(price)), groupArray(qty), groupArray(price)) AS vol_at_high,
         arraySum((q, p) -> q * (p = min(price)), groupArray(qty), groupArray(price)) AS vol_at_low
  FROM crypto.trades WHERE symbol = '$SYM'
  GROUP BY time
) AS t
) AS j_s
LEFT JOIN (
  SELECT toStartOfMinute(sec) AS time,
         stddevPop(ps) / avg(ps) * 10000 AS sec_std_bp, count() AS n_sec
  FROM (
    SELECT toStartOfSecond(ts) AS sec, argMax(price, ts) AS ps
    FROM crypto.trades WHERE symbol = '$SYM'
    GROUP BY sec
  )
  GROUP BY time
) AS s USING (time)
) AS j_g
LEFT JOIN (
  SELECT toStartOfMinute(ts) AS time,
         argMaxIf(price, quantity, side = 'BID') AS bid_wall_price,
         maxIf(quantity, side = 'BID') AS bid_wall_qty,
         argMaxIf(price, quantity, side = 'OFFER') AS offer_wall_price,
         maxIf(quantity, side = 'OFFER') AS offer_wall_qty,
         sumIf(quantity, side = 'BID') / uniqExact(ts) AS bid_total,
         sumIf(quantity, side = 'OFFER') / uniqExact(ts) AS offer_total
  FROM crypto.glass WHERE symbol = '$SYM'
  GROUP BY time
) AS g USING (time)
) AS j_l
LEFT JOIN (
  SELECT toStartOfMinute(ts) AS time,
         argMaxIf(price, quantity, side = 'BID') AS last_bid_wall_price,
         maxIf(quantity, side = 'BID') AS last_bid_wall_qty,
         argMaxIf(price, quantity, side = 'OFFER') AS last_offer_wall_price,
         maxIf(quantity, side = 'OFFER') AS last_offer_wall_qty
  FROM crypto.glass
  WHERE symbol = '$SYM'
    AND (toStartOfMinute(ts), ts) IN (
      SELECT toStartOfMinute(ts), max(ts)
      FROM crypto.glass WHERE symbol = '$SYM'
      GROUP BY toStartOfMinute(ts)
    )
  GROUP BY time
) AS l USING (time)
) AS j_q
LEFT JOIN (
  SELECT toStartOfMinute(ts) AS time,
         sumIf(qty, side = 'SELL') AS liq_long,
         sumIf(qty, side = 'BUY') AS liq_short,
         count() AS liq_n
  FROM crypto.liquidations WHERE symbol = '$SYM'
  GROUP BY time
) AS q USING (time)
) AS j_o
LEFT JOIN (
  SELECT toStartOfMinute(ts) AS time, argMax(open_interest, ts) AS oi
  FROM crypto.open_interest WHERE symbol = '$SYM'
  GROUP BY time
) AS o USING (time)
) AS j_m
LEFT JOIN (
  SELECT toStartOfMinute(ts) AS time,
         argMax(funding_rate, ts) AS funding_rate,
         argMax(mark_price, ts) AS mark_price
  FROM crypto.mark WHERE symbol = '$SYM'
  GROUP BY time
) AS m USING (time)
ORDER BY time
FORMAT CSVWithNames" </dev/null > "crypto_data/flow_$SYM.csv"
  wc -l "crypto_data/flow_$SYM.csv"
done
# Индексы-проводники: минутные закрытия по каждому источнику ->
# crypto_data/idx_<source>_<SYM>.csv (kraken/bybit/gate — токен xStocks,
# yahoo — эталон ETF, pyth — оракул)
IDX=$($CH -q "SELECT DISTINCT concat(source, ' ', symbol) FROM crypto.index_quotes FORMAT TSVRaw" </dev/null 2>/dev/null || true)
echo "$IDX" | while read -r SRC SYM; do
  [ -z "$SYM" ] && continue
  $CH -q "SELECT toStartOfMinute(ts) AS time, argMax(price, ts) AS close, count() AS n_ticks
          FROM crypto.index_quotes WHERE source = '$SRC' AND symbol = '$SYM'
          GROUP BY time ORDER BY time FORMAT CSVWithNames" </dev/null > "crypto_data/idx_${SRC}_$SYM.csv"
  echo "  idx_${SRC}_$SYM.csv: $(wc -l < "crypto_data/idx_${SRC}_$SYM.csv") строк"
done
echo "Готово: collector/crypto_data/flow_*.csv, idx_*.csv"
