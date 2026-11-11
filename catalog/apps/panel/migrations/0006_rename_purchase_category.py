from django.db import migrations


def rename(apps, schema_editor):
    # Закупка под продажу указывается при оплате; категория — только для закупки вне заказов (склад)
    Category = apps.get_model("panel", "Category")
    Category.objects.filter(kind="expense", name="Закупка товара").update(name="Закупка вне заказов")


class Migration(migrations.Migration):
    dependencies = [("panel", "0005_deal_cost")]
    operations = [migrations.RunPython(rename, migrations.RunPython.noop)]
