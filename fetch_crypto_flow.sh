#!/bin/bash
# Экспорт flow-минуток на сервере -> скачать в crypto_data/flow_*.csv (одной командой).
# Запуск:  bash fetch_crypto_flow.sh
set -e
cd "$(dirname "$0")"

SRV=${COLLECTOR_HOST:?"set COLLECTOR_HOST, e.g. ubuntu@1.2.3.4"}
KEY=${COLLECTOR_KEY:-~/.ssh/id_ed25519}

echo "== 1/2 Экспорт на сервере..."
scp -i "$KEY" collector/export_crypto_flow.sh "$SRV":~/collector/
ssh -i "$KEY" "$SRV" 'bash collector/export_crypto_flow.sh'

echo "== 2/2 Скачиваю CSV..."
mkdir -p crypto_data
scp -i "$KEY" "$SRV":'~/collector/crypto_data/flow_*.csv' crypto_data/
scp -i "$KEY" "$SRV":'~/collector/crypto_data/idx_*.csv' crypto_data/ 2>/dev/null || echo "(idx_*.csv ещё нет)"
ls -la crypto_data/flow_*.csv crypto_data/idx_*.csv 2>/dev/null
echo "Готово. Дальше: python event_study.py --source crypto --symbols flow_BTCUSDT,flow_ATOMUSDT --flow"
