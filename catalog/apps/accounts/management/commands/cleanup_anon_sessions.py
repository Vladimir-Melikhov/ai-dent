from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import AnonSession


class Command(BaseCommand):
    help = (
        "Удаляет гостевые сессии, неактивные дольше N дней (вместе с корзиной, избранным и диалогами; "
        "заказы остаются). Сессии, чьи диалоги связаны с заказом или заявкой, не трогает."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90, help="Порог неактивности в днях (по умолчанию 90)")
        parser.add_argument("--dry-run", action="store_true", help="Только посчитать, ничего не удалять")

    def handle(self, *args, days, dry_run, **options):
        cutoff = timezone.now() - timedelta(days=days)
        stale = (
            AnonSession.objects.filter(last_seen_at__lt=cutoff)
            # Иначе пропадёт ссылка «диалог» у заказа или заявки (ТЗ §5.5)
            .exclude(conversations__orders__isnull=False)
            .exclude(conversations__leads__isnull=False)
        )
        count = stale.count()
        if dry_run:
            self.stdout.write(f"Будет удалено сессий: {count}")
            return
        stale.delete()
        self.stdout.write(self.style.SUCCESS(f"Удалено сессий: {count}"))
