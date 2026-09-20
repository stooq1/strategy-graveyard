#!/bin/bash
# Место на сервере: диск, docker, размеры таблиц ClickHouse, темп роста и
# сколько суток осталось до заполнения. Запускать НА СЕРВЕРЕ.
cd "$(dirname "$0")"

echo "=== диск ==="
df -h / /var/lib/docker 2>/dev/null | awk 'NR==1 || !seen[$NF]++'

echo
echo "=== крупнейшие каталоги в / ==="
sudo du -xh --max-depth=1 / 2>/dev/null | sort -rh | head -8

echo
echo "=== docker ==="
sudo docker system df

CH="sudo docker compose exec -T clickhouse clickhouse-client"
echo
echo "=== ClickHouse: по базам (системные логи бывают больше данных) ==="
$CH -q "
SELECT database, formatReadableSize(sum(bytes_on_disk)) AS on_disk,
       formatReadableQuantity(sum(rows)) AS rows
FROM system.parts WHERE active GROUP BY database
ORDER BY sum(bytes_on_disk) DESC FORMAT PrettyCompact" </dev/null

echo
echo "=== ClickHouse: таблицы crypto ==="
$CH -q "
SELECT table,
       formatReadableQuantity(sum(rows))          AS rows,
       formatReadableSize(sum(bytes_on_disk))     AS on_disk,
       round(sum(data_uncompressed_bytes) / nullIf(sum(data_compressed_bytes), 0), 1) AS compress_x
FROM system.parts WHERE active AND database = 'crypto'
GROUP BY table ORDER BY sum(bytes_on_disk) DESC FORMAT PrettyCompact" </dev/null

echo
echo "=== темп роста и запас ==="
BYTES=$($CH -q "SELECT sum(bytes_on_disk) FROM system.parts WHERE active AND database='crypto'" </dev/null)
DAYS=$($CH -q "SELECT greatest(dateDiff('day', min(ts), now()), 1) FROM crypto.glass" </dev/null)
FREE=$(df --output=avail -B1 /var/lib/docker 2>/dev/null | tail -1 || df --output=avail -B1 / | tail -1)
awk -v b="$BYTES" -v d="$DAYS" -v f="$FREE" 'BEGIN {
  gb = b/1073741824; per = gb/d; freegb = f/1073741824;
  printf "  сейчас занято базой : %.1f ГБ за %d суток сбора\n", gb, d;
  printf "  темп               : %.2f ГБ/сутки (%.1f ГБ/месяц)\n", per, per*30;
  printf "  свободно           : %.1f ГБ\n", freegb;
  if (per > 0) printf "  хватит ещё на      : %.0f суток (~%.1f месяца)\n", freegb/per, freegb/per/30;
  if (per > 0 && freegb/per < 45) print "  !! меньше 45 суток — чистить или сокращать глубину хранения";
}'

echo
echo "=== если места мало: глубина хранения по таблицам ==="
$CH -q "
SELECT 'glass' AS tbl, min(ts) AS first, max(ts) AS last FROM crypto.glass
UNION ALL SELECT 'trades', min(ts), max(ts) FROM crypto.trades
UNION ALL SELECT 'mark', min(ts), max(ts) FROM crypto.mark
UNION ALL SELECT 'liquidations', min(ts), max(ts) FROM crypto.liquidations
UNION ALL SELECT 'index_quotes', min(ts), max(ts) FROM crypto.index_quotes
FORMAT PrettyCompact" </dev/null
