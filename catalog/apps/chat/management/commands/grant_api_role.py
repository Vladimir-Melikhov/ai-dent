import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

# Права роли сервиса api — строго по таблице ТЗ §4. Всё, чего здесь нет, недоступно:
# пользователи, заказы, счета, лиды, сессии Django.
TABLE_PRIVILEGES = {
    "orders_cartitem": "SELECT, INSERT, UPDATE, DELETE",
    "chat_conversation": "SELECT",
    "chat_message": "SELECT, INSERT",
    "chat_knowledgebaseversion": "SELECT",
    "chat_quote": "SELECT, INSERT",
    "chat_llmusage": "SELECT, INSERT",
}
# Обновление — только служебных колонок
COLUMN_UPDATES = {
    "chat_conversation": ["last_message_at", "prompt_epoch", "pending_lead_topic"],
    "chat_knowledgebaseversion": ["token_count"],
}
READ_ONLY_PREFIX = "catalog_"
ROLE_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class Command(BaseCommand):
    help = "Создаёт роль БД для сервиса api и выдаёт ей права по ТЗ §4. Запускается после migrate."

    def handle(self, *args, **options):
        role, password = settings.API_DB_USER, settings.API_DB_PASSWORD
        if not role or not password:
            self.stderr.write(self.style.WARNING(
                "API_DB_USER / API_DB_PASSWORD не заданы — роль для api не создана, сервис api не подключится к БД"
            ))
            return
        if not ROLE_NAME_RE.match(role):
            raise CommandError("API_DB_USER: только строчные латинские буквы, цифры и подчёркивание")

        quote = connection.ops.quote_name
        role_q = quote(role)
        tables = set(connection.introspection.table_names())
        missing = (set(TABLE_PRIVILEGES) | set(COLUMN_UPDATES)) - tables
        if missing:
            raise CommandError(f"Нет таблиц {sorted(missing)} — сначала выполните migrate")

        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
            if cursor.fetchone() is None:
                cursor.execute(f"CREATE ROLE {role_q} LOGIN PASSWORD %s", [password])
            else:
                cursor.execute(f"ALTER ROLE {role_q} WITH LOGIN PASSWORD %s", [password])

            cursor.execute(f"GRANT CONNECT ON DATABASE {quote(connection.settings_dict['NAME'])} TO {role_q}")
            cursor.execute(f"GRANT USAGE ON SCHEMA public TO {role_q}")
            # Сначала отзываем всё, потом выдаём ровно нужное: лишние права не накапливаются
            cursor.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role_q}")
            cursor.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role_q}")

            for table in sorted(t for t in tables if t.startswith(READ_ONLY_PREFIX)):
                cursor.execute(f"GRANT SELECT ON {quote(table)} TO {role_q}")
            for table, privileges in TABLE_PRIVILEGES.items():
                cursor.execute(f"GRANT {privileges} ON {quote(table)} TO {role_q}")
            for table, columns in COLUMN_UPDATES.items():
                cols = ", ".join(quote(c) for c in columns)
                cursor.execute(f"GRANT UPDATE ({cols}) ON {quote(table)} TO {role_q}")

        self.stdout.write(self.style.SUCCESS(f"Права роли {role} обновлены"))
