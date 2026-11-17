"""Типы блоков контента: вместо шести (на сайте выглядели одинаково) — «Описание» и «Подбор оптики»."""

from django.db import migrations, models


def merge_types(apps, schema_editor):
    Block = apps.get_model("catalog", "ProductContentBlock")
    Block.objects.filter(title__icontains="подбор").update(block_type="fitting")
    Block.objects.exclude(block_type__in=["text", "fitting"]).update(block_type="text")


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0015_loupe_demo_field_of_view"),
    ]

    operations = [
        migrations.RunPython(merge_types, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="productcontentblock",
            name="block_type",
            field=models.CharField(
                choices=[("text", "Описание"), ("fitting", "Подбор оптики")],
                default="text",
                help_text="Только подпись блока в админке, на сайте все блоки выглядят одинаково.",
                max_length=20,
                verbose_name="Тип блока",
            ),
        ),
    ]
