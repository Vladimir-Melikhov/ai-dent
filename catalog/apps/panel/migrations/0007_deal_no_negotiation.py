from django.db import migrations, models


def negotiation_to_new(apps, schema_editor):
    apps.get_model("panel", "Deal").objects.filter(stage="negotiation").update(stage="new")


class Migration(migrations.Migration):
    dependencies = [("panel", "0006_rename_purchase_category")]

    operations = [
        migrations.RunPython(negotiation_to_new, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="deal", name="stage",
            field=models.CharField(
                choices=[("new", "Новая"), ("offer", "КП отправлено"), ("payment", "Ждём оплату"),
                         ("won", "Оплачено"), ("lost", "Отказ")],
                default="new", max_length=20, verbose_name="Этап"),
        ),
    ]
