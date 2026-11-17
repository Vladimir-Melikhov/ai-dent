from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0014_delete_empty_content_blocks"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="loupe_demo",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="На странице товара появится блок: бренды с лупами и поле обзора при каждом увеличении на снимке челюсти.",
                verbose_name="Показывать блок «Поле обзора»",
            ),
        ),
    ]
