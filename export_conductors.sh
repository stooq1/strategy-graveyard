#!/bin/bash
# Экспорт из архива FORTS 2020 для проверки ПРОВОДНИКОВ и ПОТОКА АГРЕССОРОВ.
# Запуск на маке (контейнер clickhouse должен быть healthy):
#   bash export_conductors.sh                     # разведка + investing + flow по списку DEFAULT_CODES
#   bash export_conductors.sh --flow-only         # только flow_*.csv по DEFAULT_CODES
#   bash export_conductors.sh --flow-only BRJ0 SRH0   # только указанные коды
#
# Результат в moex_data/:
#   _discovery.txt   — какие коды есть в архиве, S&P/Brent из investing, семантика стакана
#   inv_<code>.csv   — минутки investing.com (time, close, n_ticks): S&P, Brent, ...
#   flow_<code>.csv  — минутки фьючерса + поток агрессоров (taker_buy/sell), касания,
#                      секундная волатильность, стакан (макс за минуту и последний снимок)
set -e
cd "$(dirname "$0")"
mkdir -p moex_data
CH="docker compose exec -T clickhouse clickhouse-client"
SINCE="2020-02-22"
# Si/RI фронты, цепочка Brent помесячно, Сбер, Газпром, индекс МосБиржи
DEFAULT_CODES="SiH0 SiM0 SiU0 RIH0 RIM0 RIU0 BRH0 BRJ0 BRK0 BRM0 BRN0 BRQ0 BRU0 BRV0 SRH0 SRM0 SRU0 GZH0 GZM0 GZU0 MXH0 MXM0 MXU0"

if ! $CH -q "SELECT 1" </dev/null >/dev/null 2>&1; then
  echo "ClickHouse не запущен (или ещё не healthy). Сначала:"
  echo "  cd ~/PycharmProjects/stooq_detect && docker compose up -d clickhouse"
  echo "затем дождитесь healthy:  docker compose ps   — и повторите запуск."
  exit 1
fi

FLOW_ONLY=0
if [ "${1:-}" = "--flow-only" ]; then FLOW_ONLY=1; shift; fi

if [ $FLOW_ONLY -eq 0 ]; then
echo "== 1/3 Разведка архива -> moex_data/_discovery.txt"
{
echo "### Фьючерсы в quik.anonymous_transactions с $SINCE (топ-60 по числу сделок)"
$CH -q "SELECT code, any(parent_code) AS parent, count() AS n,
               min(event_date) AS d0, max(event_date) AS d1
        FROM quik.anonymous_transactions
        WHERE type = 'Futures' AND event_date >= '$SINCE'
        GROUP BY code ORDER BY n DESC LIMIT 60 FORMAT PrettyCompactNoEscapes" </dev/null
echo
echo "### Котировки investing.com (quik.investing_websocket_quotes): код, число тиков, период, часы (UTC)"
$CH -q "SELECT parent, code, count() AS n, min(datetime) AS t0, max(datetime) AS t1,
               uniqExact(toDate(datetime)) AS days,
               arraySort(groupUniqArray(toHour(datetime))) AS hours
        FROM quik.investing_websocket_quotes
        GROUP BY parent, code ORDER BY n DESC FORMAT PrettyCompactNoEscapes" </dev/null
echo
echo "### Для сверки часов: RIH0 сделки по часам 2020-03-10 (UTC: сессия 07..20)"
$CH -q "SELECT toHour(datetime) AS h, count() AS n FROM quik.anonymous_transactions
        WHERE code = 'RIH0' AND event_date = '2020-03-10' GROUP BY h ORDER BY h FORMAT PrettyCompactNoEscapes" </dev/null
echo
echo "### Семантика quik.glass (RIH0, 2020-03-10): init=YES/NO, строк на секунду, глубина"
$CH -q "SELECT init, count() AS rows, uniqExact(datetime) AS seconds,
               round(count() / uniqExact(datetime), 1) AS rows_per_second,
               uniqExact(price) AS distinct_prices
        FROM quik.glass WHERE code = 'RIH0' AND event_date = '2020-03-10'
        GROUP BY init FORMAT PrettyCompactNoEscapes" </dev/null
$CH -q "SELECT datetime, countIf(type = 'BID') AS bids, countIf(type = 'OFFER') AS offers,
               minIf(price, type = 'OFFER') - maxIf(price, type = 'BID') AS spread,
               maxIf(price, type = 'OFFER') - minIf(price, type = 'BID') AS span
        FROM quik.glass WHERE code = 'RIH0' AND event_date = '2020-03-10'
        GROUP BY datetime ORDER BY datetime LIMIT 12 FORMAT PrettyCompactNoEscapes" </dev/null
echo
echo "### Календарь analysis.fxstreet_events"
$CH -q "SELECT count() AS n, min(datetime) AS t0, max(datetime) AS t1,
               uniqExact(Currency) AS currencies
        FROM analysis.fxstreet_events FORMAT PrettyCompactNoEscapes" </dev/null || echo "(нет таблицы)"
} 2>&1 | tee moex_data/_discovery.txt

echo
echo "== 2/3 Котировки investing.com -> moex_data/inv_*.csv"
INV_CODES=$($CH -q "SELECT DISTINCT code FROM quik.investing_websocket_quotes FORMAT TSVRaw" </dev/null)
while IFS= read -r CODE; do
  [ -z "$CODE" ] && continue
  SAFE=$(printf '%s' "$CODE" | tr -c 'A-Za-z0-9_' '_')
  $CH -q "SELECT toStartOfMinute(datetime) AS time, argMax(price, datetime) AS close,
                 count() AS n_ticks
          FROM quik.investing_websocket_quotes WHERE code = '$CODE'
          GROUP BY time ORDER BY time FORMAT CSVWithNames" </dev/null > "moex_data/inv_${SAFE}.csv"
  echo "  $CODE -> inv_${SAFE}.csv ($(wc -l < "moex_data/inv_${SAFE}.csv") строк)"
done <<< "$INV_CODES"
fi

echo
echo "== 3/3 Минутки + поток агрессоров -> moex_data/flow_*.csv"
if [ $# -gt 0 ]; then CODES="$*"; else CODES="$DEFAULT_CODES"; fi
for CODE in $CODES; do
  printf "  %-6s " "$CODE"
  $CH -q "
SELECT * FROM (
SELECT * FROM (
SELECT * FROM (
SELECT * FROM (
  SELECT toStartOfMinute(datetime) AS time,
         argMin(price, (datetime, trancId)) AS open,
         max(price) AS high, min(price) AS low,
         argMax(price, (datetime, trancId)) AS close,
         sum(qty) AS volume,
         sumIf(qty, op = 'B') AS taker_buy, sumIf(qty, op = 'S') AS taker_sell,
         count() AS n_trades, uniqExact(price) AS n_prices,
         arrayCount(x -> x = max(price), groupArray(price)) AS touch_high,
         arrayCount(x -> x = min(price), groupArray(price)) AS touch_low,
         arraySum((q, p) -> q * (p = max(price)), groupArray(qty), groupArray(price)) AS vol_at_high,
         arraySum((q, p) -> q * (p = min(price)), groupArray(qty), groupArray(price)) AS vol_at_low
  FROM quik.anonymous_transactions
  WHERE code = '$CODE' AND type = 'Futures' AND event_date >= '$SINCE'
  GROUP BY time
) AS t
) AS j_s
LEFT JOIN (
  SELECT toStartOfMinute(datetime) AS time,
         stddevPop(ps) / avg(ps) * 10000 AS sec_std_bp, count() AS n_sec
  FROM (
    SELECT datetime, argMax(price, trancId) AS ps
    FROM quik.anonymous_transactions
    WHERE code = '$CODE' AND type = 'Futures' AND event_date >= '$SINCE'
    GROUP BY datetime
  )
  GROUP BY time
) AS s USING (time)
) AS j_g
LEFT JOIN (
  SELECT toStartOfMinute(datetime) AS time,
         argMaxIf(price, quantity, type = 'BID') AS bid_wall_price,
         maxIf(quantity, type = 'BID') AS bid_wall_qty,
         argMaxIf(price, quantity, type = 'OFFER') AS offer_wall_price,
         maxIf(quantity, type = 'OFFER') AS offer_wall_qty,
         sumIf(quantity, type = 'BID') AS bid_total,
         sumIf(quantity, type = 'OFFER') AS offer_total
  FROM quik.glass
  WHERE code = '$CODE' AND event_date >= '$SINCE'
  GROUP BY time
) AS g USING (time)
) AS j_l
LEFT JOIN (
  SELECT toStartOfMinute(datetime) AS time,
         argMaxIf(price, quantity, type = 'BID') AS last_bid_wall_price,
         maxIf(quantity, type = 'BID') AS last_bid_wall_qty,
         argMaxIf(price, quantity, type = 'OFFER') AS last_offer_wall_price,
         maxIf(quantity, type = 'OFFER') AS last_offer_wall_qty
  FROM quik.glass
  WHERE code = '$CODE' AND event_date >= '$SINCE'
    AND (toStartOfMinute(datetime), datetime) IN (
      SELECT toStartOfMinute(datetime), max(datetime)
      FROM quik.glass
      WHERE code = '$CODE' AND event_date >= '$SINCE'
      GROUP BY toStartOfMinute(datetime)
    )
  GROUP BY time
) AS l USING (time)
ORDER BY time
FORMAT CSVWithNames" </dev/null > "moex_data/flow_$CODE.csv"
  echo "$(wc -l < "moex_data/flow_$CODE.csv") строк"
done
echo "Готово: moex_data/flow_*.csv"
