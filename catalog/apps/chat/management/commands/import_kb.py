from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.chat.knowledge import create_version, price_mentions
from apps.chat.models import KnowledgeBaseVersion


class Command(BaseCommand):
    help = "Загружает базу знаний из файла (.md / .txt) новой активной версией."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Путь к файлу базы знаний")
        parser.add_argument("--if-empty", action="store_true",
                            help="Загрузить, только если версий ещё нет — для первого запуска контейнера")
        parser.add_argument("--comment", default="", help="Комментарий к версии")

    def handle(self, *args, path, if_empty, comment, **options):
        if if_empty and KnowledgeBaseVersion.objects.exists():
            self.stdout.write("База знаний уже загружена — пропускаю")
            return
        file = Path(path)
        if not file.is_file():
            raise CommandError(f"Файл не найден: {path}")
        content = file.read_text(encoding="utf-8").strip()
        if not content:
            raise CommandError("Файл пустой")
        version = create_version(content, source=KnowledgeBaseVersion.Source.IMPORT,
                                 comment=comment or f"Импорт из {file.name}")
        self.stdout.write(self.style.SUCCESS(f"Версия #{version.pk} загружена и активна, {version.char_count} символов"))
        prices = price_mentions(content)
        if prices:
            # Ожидаемо для текущей базы: импорт не блокируется
            self.stdout.write(self.style.WARNING(f"В тексте есть суммы в рублях, например: {', '.join(prices)}"))
