#!/usr/bin/env bash
# Тесты без обращений к платной модели.
#
# Нужны тестовые Postgres и Redis:
#   docker compose -f docker-compose.test.yml up -d
# Адреса по умолчанию совпадают с docker-compose.test.yml; переопределяются переменными TEST_PG_* и TEST_REDIS_URL.
# PYTHON — интерпретатор с зависимостями catalog/requirements-dev.txt и api/requirements-dev.txt.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY=${PYTHON:-python3}

PG_HOST=${TEST_PG_HOST:-127.0.0.1}
PG_PORT=${TEST_PG_PORT:-55432}
PG_USER=${TEST_PG_USER:-postgres}
PG_PASSWORD=${TEST_PG_PASSWORD:-postgres}
REDIS=${TEST_REDIS_URL:-redis://127.0.0.1:56379/0}
DB=dentai_test
API_ROLE=dentai_api_test
API_PASSWORD=dentai_api_test

export DENTAI_TESTS=1
export DJANGO_SECRET_KEY=test DEBUG=True DJANGO_ADMIN_URL=admin/ DJANGO_ALLOWED_HOSTS=localhost,testserver
export POSTGRES_DB=$DB POSTGRES_USER=$PG_USER POSTGRES_PASSWORD=$PG_PASSWORD POSTGRES_HOST=$PG_HOST POSTGRES_PORT=$PG_PORT
export REDIS_URL=$REDIS SITE_ORIGIN=http://testserver
export API_DB_USER=$API_ROLE API_DB_PASSWORD=$API_PASSWORD
# Из тестов — никаких настоящих писем и сообщений в Telegram
export TELEGRAM_BOT_TOKEN= TELEGRAM_CHAT_ID= MANAGER_EMAIL= NOTIFY_EMAILS=
export EMAIL_BACKEND=django.core.mail.backends.locmem.EmailBackend

echo "== База для тестов api: схема из миграций Django, права роли api"
"$PY" - <<PYEOF
import psycopg2
conn = psycopg2.connect(dbname="postgres", user="$PG_USER", password="$PG_PASSWORD", host="$PG_HOST", port=$PG_PORT)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("DROP DATABASE IF EXISTS $DB")
    cur.execute("CREATE DATABASE $DB")
PYEOF
(cd "$ROOT/catalog" && "$PY" manage.py migrate -v0 && "$PY" manage.py grant_api_role)

echo "== Django"
(cd "$ROOT/catalog" && "$PY" -m pytest -q -p no:cacheprovider "$@")

echo "== api"
(cd "$ROOT/api" && \
  DATABASE_URL="postgresql+asyncpg://$API_ROLE:$API_PASSWORD@$PG_HOST:$PG_PORT/$DB" \
  TEST_ADMIN_DATABASE_URL="postgresql://$PG_USER:$PG_PASSWORD@$PG_HOST:$PG_PORT/$DB" \
  DATABASE_NULL_POOL=true CHAT_STUB_DELAY=0 \
  "$PY" -m pytest -q -p no:cacheprovider "$@")

echo "== Фронтенд (node)"
# Нужен Node.js 20+; без него — предупреждение, не ошибка
if command -v node >/dev/null 2>&1; then
  node --test "$ROOT"/catalog/tests_js/*.test.mjs
else
  echo "Node.js не найден — пропущены тесты catalog/tests_js"
fi
