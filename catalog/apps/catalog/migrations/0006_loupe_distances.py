import apps.catalog.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0005_product_loupe"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="loupe",
            field=models.JSONField(
                blank=True,
                null=True,
                validators=[apps.catalog.models.validate_loupe],
                verbose_name="Данные лупы",
                help_text=apps.catalog.models.LOUPE_HELP,
            ),
        ),
    ]
