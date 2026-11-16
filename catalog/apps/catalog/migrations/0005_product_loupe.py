import apps.catalog.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_product_price_rub_currencyrate"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="loupe",
            field=models.JSONField(
                blank=True,
                null=True,
                validators=[apps.catalog.models.validate_loupe],
                verbose_name="Данные лупы",
                help_text=(
                    'Только для бинокулярных луп. Пример: {"kind": "individual", "options": ['
                    '{"zoom": "2.5×", "distance": "340–420 мм", "field": "110 мм", "depth": "200 мм"}]}. '
                    'kind — "universal" или "individual"; в options нужен только zoom, остальное по желанию.'
                ),
            ),
        ),
    ]
