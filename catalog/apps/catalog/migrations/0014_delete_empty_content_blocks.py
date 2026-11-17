"""Удаляет пустые блоки контента, которые админка создавала при каждом сохранении товара."""

from django.db import migrations
from django.db.models import Q


def empty_blocks(apps):
    Block = apps.get_model("catalog", "ProductContentBlock")
    return Block.objects.filter(title="", subtitle="", body="", video_url="").filter(
        Q(image="") | Q(image__isnull=True)
    ).filter(Q(data=[]) | Q(data={}) | Q(data__isnull=True))


def delete_empty_blocks(apps, schema_editor):
    empty_blocks(apps).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0013_loupe_demo_help_text"),
    ]

    operations = [
        migrations.RunPython(delete_empty_blocks, migrations.RunPython.noop),
    ]
