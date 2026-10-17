#!/usr/bin/env bash
# Полный прогон технических проверок ТЗ §17.2 без платной модели (M5).
#
# Что нужно:
#   - тестовые Postgres и Redis: docker compose -f docker-compose.test.yml up -d
#   - запущенный стенд (для T17 и T18): docker compose up -d  (или с docker-compose.dev.yml)
#   - PYTHON — интерпретатор с catalog/requirements-dev.txt и api/requirements-dev.txt
#   - Node.js 20+ для T11 (без него проверка пропускается с предупреждением)
#
# Запуск:  PYTHON=.venv/bin/python scripts/check_all.sh [адрес сайта, по умолчанию http://localhost]
# T2 и T13 обращаются к модели — они в списке «запустить с ключом» (docs/выкатка.md).
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SITE=${1:-http://localhost}
cd "$ROOT"
declare -a RESULTS=()

run() {  # run "<проверки>" команда...
  local label=$1; shift
  echo
  echo "=================== $label"
  if "$@"; then RESULTS+=("OK    $label"); else RESULTS+=("FAIL  $label"); fi
}

caddy_closed() {
  # T17: счета и КП не раздаются напрямую из media — только через Django с проверкой владельца
  # Берём настоящие файлы со стенда: 404 на несуществующий файл ничего не доказывает
  local status=0 code path invoice quote
  invoice=$(docker compose exec -T catalog sh -c 'ls media/invoices 2>/dev/null | head -1' | tr -d '\r')
  quote=$(docker compose exec -T catalog sh -c 'ls media/quotes 2>/dev/null | head -1' | tr -d '\r')
  [ -n "$invoice" ] || echo "Счетов на стенде нет — проверяется вымышленный путь"
  [ -n "$quote" ] || echo "КП на стенде нет — проверяется вымышленный путь"
  for path in "/media/invoices/${invoice:-invoice-AD-1.pdf}" "/media/quotes/${quote:-kp-1.pdf}"; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "$SITE$path")
    if [ "$code" = "404" ]; then echo "OK   $path → 404"; else echo "FAIL $path → $code"; status=1; fi
  done
  return $status
}

masking_on_catalog() {
  # T18 на настоящем каталоге стенда (в автотестах — на тестовых товарах)
  docker compose exec -T api python scripts/check_masking.py
}

run "T1, T3–T12, T14–T16, T18, T20 — автотесты Django, api и фронтенда" scripts/run_tests.sh
run "T17 — Caddy не отдаёт /media/invoices и /media/quotes" caddy_closed
run "T18 — маскирование на каталоге стенда" masking_on_catalog
run "T19 — в образах нет .env" scripts/check_images.sh

echo
echo "=================== Итог"
printf '%s\n' "${RESULTS[@]}"
echo "НУЖЕН КЛЮЧ  T2 (кэш), T13 (prefix_mismatch_behavior=error) — см. docs/выкатка.md, «Запустить с ключом»"
! printf '%s\n' "${RESULTS[@]}" | grep -q '^FAIL'
