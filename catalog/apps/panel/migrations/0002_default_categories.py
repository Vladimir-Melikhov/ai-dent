from django.db import migrations

INCOME = [("Сделки", 3), ("Услуги", 1), ("Прочие доходы", 7)]
EXPENSE = [
    ("Закупка товара", 1), ("Доставка", 2), ("Реклама", 5), ("Серверы и сервисы", 7),
    ("ИИ-консультант", 3), ("Налоги", 8), ("Зарплата", 4), ("Бухгалтерия и банк", 6), ("Прочие расходы", 2),
]


def add_defaults(apps, schema_editor):
    Category = apps.get_model("panel", "Category")
    for kind, rows in (("income", INCOME), ("expense", EXPENSE)):
        for name, color in rows:
            Category.objects.get_or_create(kind=kind, name=name, defaults={"color": color})


class Migration(migrations.Migration):
    dependencies = [("panel", "0001_initial")]
    operations = [migrations.RunPython(add_defaults, migrations.RunPython.noop)]
