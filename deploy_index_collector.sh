#!/bin/bash
# Деплой index_collector (котировки US-индексов из Pyth) на сервер рядом с
# коллектором Binance. Не трогает работающий collector: отдельный образ.
# Запуск:  bash deploy_index_collector.sh
set -e
cd "$(dirname "$0")"
SRV=${COLLECTOR_HOST:?"set COLLECTOR_HOST, e.g. ubuntu@1.2.3.4"}
KEY=${COLLECTOR_KEY:-~/.ssh/id_ed25519}

echo "== 1/3 Копирую файлы..."
scp -i "$KEY" collector/index_collector.py collector/Dockerfile.index \
    collector/docker-compose.yml collector/check.sh "$SRV":~/collector/

echo "== 2/3 Собираю образ и запускаю разведку источников с сервера (без записи в базу)..."
ssh -i "$KEY" "$SRV" 'cd ~/collector && sudo docker compose build index_collector >/dev/null \
  && sudo docker compose run --rm --no-deps index_collector python -u /app/index_collector.py --probe'

echo "== 3/3 Запускаю index_collector..."
ssh -i "$KEY" "$SRV" 'cd ~/collector && sudo docker compose up -d index_collector && sleep 25 \
  && sudo docker compose ps && sudo docker compose logs index_collector --tail 20'
echo "Готово. Через час:  bash collector/check.sh на сервере (раздел «индексы»)."
