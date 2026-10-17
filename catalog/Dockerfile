# --- сборка колёс: компиляторы нужны только здесь ---
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt



FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/* && rm -rf /wheels

RUN useradd --create-home --uid 1000 app

COPY --chown=app:app . .

RUN DJANGO_SECRET_KEY=collectstatic-build-only DJANGO_ADMIN_URL=build-only \
    python manage.py collectstatic --noinput \
    && mkdir -p /app/media && chown -R app:app /app/media /app/staticfiles

USER app

EXPOSE 8000

# После migrate — права роли БД для api (ТЗ §5.4).
# База знаний загружается из файла только при первом запуске — дальше её ведут в админке (ТЗ §9).
# Курсы ЦБ — при каждом старте; сбой загрузки запуск не останавливает (ТЗ §10.7)
CMD sh -c "python manage.py migrate --noinput && python manage.py grant_api_role && python manage.py import_kb knowledge_base/baza_znaniy.md --if-empty && (python manage.py update_currency_rates || true) && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3"
