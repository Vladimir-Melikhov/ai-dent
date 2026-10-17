from django.core.management.base import BaseCommand

from apps.catalog.currency import update_rates
from apps.orders.telegram import send_telegram


class Command(BaseCommand):
    help = "Загружает курсы ЦБ РФ и пересчитывает цены валютных товаров в рубли (ТЗ §10.7). Раз в день по cron."

    def handle(self, *args, **options):
        result = update_rates()
        for date, rates in sorted(result["loaded"].items()):
            text = ", ".join(f"{code} {rate}" for code, rate in sorted(rates.items()))
            self.stdout.write(f"Курсы на {date:%d.%m.%Y}: {text}")
        if not result["loaded"]:
            self.stderr.write(self.style.WARNING("Курсы ЦБ не загрузились — цены считаются по последнему курсу"))
        self.stdout.write(f"Пересчитано цен: {result['recalculated']}")
        if result["stale"]:
            message = f"Курс ЦБ для {', '.join(result['stale'])} не обновлялся больше 7 дней или его нет"
            self.stderr.write(self.style.WARNING(message))
            send_telegram(f"Ai-Dent: {message}")
