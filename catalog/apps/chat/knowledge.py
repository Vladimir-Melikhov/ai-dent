"""База знаний: версии, активация, предупреждение о ценах (ТЗ §9)."""

import re

from django.db import transaction

from .models import KnowledgeBaseVersion

# Суммы в рублях: «48 500 ₽», «1 710 534 руб.», «2 млн ₽»
RUB_AMOUNT = re.compile(r"\d[\d\s ,.]*\s?(?:тыс\.?|млн)?\s?(?:₽|руб\b|руб\.|рубл)", re.IGNORECASE)


def price_mentions(content: str, limit: int = 3) -> list[str]:
    """Цен в базе знаний быть не должно: клиенту их называет только каталог. Это предупреждение, не запрет."""
    return [m.group(0).strip() for m in RUB_AMOUNT.finditer(content)][:limit]


@transaction.atomic
def activate(version: KnowledgeBaseVersion) -> None:
    """Активна ровно одна версия: сначала снять отметку со всех, потом поставить (частичный уникальный индекс)."""
    KnowledgeBaseVersion.objects.filter(is_active=True).exclude(pk=version.pk).update(is_active=False)
    KnowledgeBaseVersion.objects.filter(pk=version.pk).update(is_active=True)
    version.is_active = True


@transaction.atomic
def create_version(content: str, *, source: str, comment: str = "", created_by=None) -> KnowledgeBaseVersion:
    """Каждое сохранение — новая версия, старые не меняются; новая сразу становится активной."""
    version = KnowledgeBaseVersion.objects.create(
        content=content, comment=comment, source=source, created_by=created_by, char_count=len(content),
    )
    activate(version)
    return version
