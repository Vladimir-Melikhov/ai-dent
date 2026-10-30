FROM python:3.12-slim
# Пакеты Debian — с зеркала Яндекса: deb.debian.org с сервера в России отдаёт их еле-еле
ARG DEBIAN_MIRROR=http://mirror.yandex.ru

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN sed -i "s|http://deb.debian.org|$DEBIAN_MIRROR|" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Все зависимости — готовыми колёсами, компиляторы не нужны
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

RUN useradd --create-home --uid 1000 app

COPY --chown=app:app . .

RUN DJANGO_SECRET_KEY=collectstatic-build-only DJANGO_ADMIN_URL=build-only \
    python manage.py collectstatic --noinput \
    && mkdir -p /app/media && chown -R app:app /app/media /app/staticfiles

USER app

EXPOSE 8000

# После migrate — права роли БД для api. База знаний из файла — только при первом запуске,
# дальше её ведут в админке. Сбой загрузки курсов ЦБ запуск не останавливает
CMD sh -c "python manage.py migrate --noinput && python manage.py grant_api_role && python manage.py import_kb knowledge_base/baza_znaniy.md --if-empty && (python manage.py update_currency_rates || true) && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3"
