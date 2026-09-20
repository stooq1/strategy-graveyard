#!/bin/bash
# Установка на чистом Ubuntu: docker + запуск коллектора.
set -e
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "== Ставлю docker..."
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

echo "== Запускаю коллектор..."
sudo docker compose up -d --build
sleep 15
sudo docker compose ps
sudo docker compose logs collector --tail 5
echo "== Готово. Проверка объёмов:  bash check.sh"
