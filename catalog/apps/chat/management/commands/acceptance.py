"""Приёмочные диалоги одной командой.

    docker compose exec -T catalog python manage.py acceptance > acceptance-report.md
    docker compose exec -T catalog python manage.py acceptance --only 3,7,12
    docker compose exec -T catalog python manage.py acceptance --cleanup

Отчёт в Markdown — в стандартный вывод, ход прогона — в поток ошибок. Обращается к модели,
которая подключена к api: с ключом — к Claude API (платно, ~$0,5–1 за диалог), на стенде
с docker-compose.dev.yml — к подменной модели.
"""

import os
import sys

from django.core.management.base import BaseCommand, CommandError

from apps.chat import acceptance
from apps.chat.models import LLMUsage


class Command(BaseCommand):
    help = "Прогнать приёмочные диалоги 1–14 и вывести отчёт; --cleanup — удалить созданное приёмкой"

    def add_arguments(self, parser):
        parser.add_argument("--only", help="номера диалогов через запятую, по умолчанию все")
        parser.add_argument("--ws", default=os.environ.get("ACCEPTANCE_WS_URL", "ws://api:8000/ws/chat"),
                            help="адрес WebSocket чата в api (по умолчанию ws://api:8000/ws/chat)")
        parser.add_argument("--kb-product", default=acceptance.KB_ONLY_PRODUCT,
                            help="товар, который есть в базе знаний, но не в каталоге (диалог 4)")
        parser.add_argument("--notify", action="store_true",
                            help="отправлять настоящие письма и Telegram (по умолчанию — только в память)")
        parser.add_argument("--cleanup", action="store_true", help="удалить заказы, заявки и диалоги приёмки")

    def handle(self, *args, **options):
        if options["cleanup"]:
            counts = acceptance.cleanup()
            self.stderr.write("Удалено: " + ", ".join(f"{k} — {v}" for k, v in counts.items()))
            return
        numbers = sorted(acceptance.DIALOGS)
        if options["only"]:
            try:
                numbers = [int(n) for n in options["only"].split(",")]
            except ValueError:
                raise CommandError("--only: номера через запятую, например 3,7,12") from None
            unknown = set(numbers) - set(acceptance.DIALOGS)
            if unknown:
                raise CommandError(f"нет диалогов {sorted(unknown)}")

        run = acceptance.Run(options["ws"], notify=options["notify"], kb_product=options["kb_product"],
                             log=lambda text: print(text, file=sys.stderr, flush=True))
        results = run.run(numbers)
        conversations = [r.conversation_id for r in results if r.conversation_id]
        fake = LLMUsage.objects.filter(conversation_id__in=conversations, request_id__startswith="req_fake").exists()
        note = ("Модель: подменная (стенд разработки) — проверяется сам сценарий; "
                "диалоги, где важно понимание и база знаний, на ней не проходят." if fake
                else "Модель: Claude API, подключённая к api.")
        self.stdout.write(acceptance.report(results, note))
        failed = [r.number for r in results if r.status in ("НЕ ПРОЙДЕН", "ОШИБКА")]
        self.stderr.write(f"Готово: {len(results)} диалогов, не пройдено: {failed or 'нет'}. "
                          "Созданное приёмкой удаляется командой: manage.py acceptance --cleanup")
