import os

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from apps.catalog.images import optimize
from apps.catalog.models import ProductContentBlock, ProductImage


class Command(BaseCommand):
    help = "Переводит уже загруженные фото товаров и блоков в WebP (до 1600 px). Старые файлы остаются: --delete-old удаляет."

    def add_arguments(self, parser):
        parser.add_argument("--delete-old", action="store_true", help="удалить прежние файлы после замены")
        parser.add_argument("--dry-run", action="store_true", help="только показать, что будет заменено")

    def handle(self, *args, **options):
        saved = 0
        for model in (ProductImage, ProductContentBlock):
            for obj in model.objects.exclude(image="").exclude(image__isnull=True):
                name = obj.image.name
                if name.lower().endswith(".webp") and obj.image.size < 400_000:
                    continue
                try:
                    old_size = obj.image.size
                    with obj.image.open("rb") as handle:
                        result = optimize(handle)
                except FileNotFoundError:
                    self.stderr.write(f"нет файла: {name}")
                    continue
                if result is None:
                    self.stderr.write(f"не удалось: {name}")
                    continue
                saved += max(old_size - result.size, 0)
                self.stdout.write(f"{name}: {old_size // 1024} КБ → {result.size // 1024} КБ")
                if options["dry_run"]:
                    continue
                old_path = obj.image.path
                obj.image.save(result.name, ContentFile(result.read()), save=False)
                obj.save(update_fields=["image"])
                if options["delete_old"] and old_path != obj.image.path and os.path.exists(old_path):
                    os.remove(old_path)
        self.stdout.write(self.style.SUCCESS(f"Экономия: {saved // 1024} КБ"))
