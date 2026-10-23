#!/usr/bin/env bash
# В собранных образах нет файлов .env. Нужен запущенный Docker.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose build catalog api
status=0
for service in catalog api; do
  found=$(docker compose run --rm --no-deps --entrypoint "" "$service" sh -c 'find /app -name ".env" -o -name ".env.*" | grep -v ".env.example" || true')
  if [ -n "$found" ]; then
    echo "FAIL $service: $found"
    status=1
  else
    echo "OK   $service: .env в образе нет"
  fi
done
exit $status
