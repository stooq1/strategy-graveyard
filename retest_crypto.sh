#!/bin/bash
# Повторный тест на накопленных данных одной командой:
# экспорт на сервере -> скачать CSV -> прогон с мейкер/тейкер.
#
# Запуск:  bash retest_crypto.sh
set -e
cd "$(dirname "$0")"

SRV=${COLLECTOR_HOST:?"set COLLECTOR_HOST, e.g. ubuntu@1.2.3.4"}
KEY=${COLLECTOR_KEY:-~/.ssh/id_ed25519}

echo "== 1/3 Экспорт минуток на сервере..."
scp -i "$KEY" collector/export_crypto.sh "$SRV":~/collector/
ssh -i "$KEY" "$SRV" 'bash collector/export_crypto.sh'

echo "== 2/3 Скачиваю CSV..."
mkdir -p crypto_data
scp -i "$KEY" "$SRV":'~/collector/crypto_data/*.csv' crypto_data/

echo "== 3/3 Прогон (taker vs maker, режимная специализация)..."
.venv/bin/python run_walls_crypto.py
