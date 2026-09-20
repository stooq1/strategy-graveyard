#!/bin/bash
# Сколько собрано по каждому символу + хвост лога коллектора.
# ВАЖНО: скрипт работает только НА СЕРВЕРЕ (там ClickHouse в docker compose).
cd "$(dirname "$0")"

if ! sudo docker compose exec -T clickhouse clickhouse-client -q "SELECT 1" >/dev/null 2>&1; then
  echo "ClickHouse недоступен из этой машины — похоже, скрипт запущен локально." >&2
  echo "Запускать надо на сервере:" >&2
  echo "  scp -i $COLLECTOR_KEY collector/check.sh $COLLECTOR_HOST:~/collector/" >&2
  echo "  ssh -i $COLLECTOR_KEY $COLLECTOR_HOST 'bash collector/check.sh'" >&2
  exit 1
fi
sudo docker compose exec -T clickhouse clickhouse-client -q "
SELECT symbol,
       countIf(tbl='glass')  AS glass,
       countIf(tbl='trades') AS trades,
       countIf(tbl='mark')   AS mark,
       countIf(tbl='liq')    AS liq,
       countIf(tbl='oi')     AS oi,
       max(ts) AS last
FROM (
  SELECT symbol, ts, 'glass' AS tbl FROM crypto.glass
  UNION ALL SELECT symbol, ts, 'trades' FROM crypto.trades
  UNION ALL SELECT symbol, ts, 'mark' FROM crypto.mark
  UNION ALL SELECT symbol, ts, 'liq' FROM crypto.liquidations
  UNION ALL SELECT symbol, ts, 'oi' FROM crypto.open_interest
)
GROUP BY symbol ORDER BY symbol FORMAT PrettyCompact"
echo "--- индексы (Pyth): строк, первая/последняя, обновлений за последний час, медианная задержка ленты, мс"
sudo docker compose exec -T clickhouse clickhouse-client -q "
SELECT source, symbol, count() AS rows, min(ts) AS first, max(ts) AS last,
       countIf(ts > now() - INTERVAL 1 HOUR) AS last_hour,
       round(median(dateDiff('millisecond', ts, recv_ts))) AS lag_ms,
       round(avg(conf / price) * 1e4, 1) AS spread_bp
FROM crypto.index_quotes GROUP BY source, symbol ORDER BY source, symbol FORMAT PrettyCompact" \
  2>/tmp/idx_err.$$ || {
    if grep -qi "UNKNOWN_TABLE\|doesn't exist\|does not exist" /tmp/idx_err.$$; then
      echo "(таблицы crypto.index_quotes нет — index_collector ни разу не писал: bash deploy_index_collector.sh)"
    else
      echo "(запрос к crypto.index_quotes не прошёл — это НЕ значит, что коллектор стоит:)"
      sed 's/^/    /' /tmp/idx_err.$$ | head -5
    fi
  }
rm -f /tmp/idx_err.$$
echo "--- лог коллектора:"
sudo docker compose logs collector --tail 8
echo "--- лог index_collector:"
sudo docker compose logs index_collector --tail 8 2>/dev/null || true
