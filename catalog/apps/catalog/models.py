from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.text import slugify

ALLOWED_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024

_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate_slug(text: str) -> str:
    lowered = text.lower()
    transliterated = "".join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in lowered)
    return slugify(transliterated)


def validate_image_size(image):
    if image.size > MAX_IMAGE_SIZE_BYTES:
        raise ValidationError("Файл больше 5 МБ")


class Brand(models.Model):
    name = models.CharField(max_length=120, unique=True, verbose_name="Название")
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True, verbose_name="Страна")
    description = models.TextField(null=True, blank=True, verbose_name="Описание")

    class Meta:
        verbose_name = "Бренд"
        verbose_name_plural = "Бренды"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = transliterate_slug(self.name)
        super().save(*args, **kwargs)


class Category(models.Model):
    name = models.CharField(max_length=120, verbose_name="Название")
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True,
        related_name="children", verbose_name="Родительская категория",
    )
    icon = models.CharField(max_length=255, null=True, blank=True, verbose_name="Иконка")
    sort_order = models.IntegerField(default=0, verbose_name="Порядок сортировки")

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = transliterate_slug(self.name)
        super().save(*args, **kwargs)


class Product(models.Model):
    class Currency(models.TextChoices):
        RUB = "RUB", "Рубли"
        USD = "USD", "Доллары"
        EUR = "EUR", "Евро"

    name = models.CharField(max_length=255, verbose_name="Название")
    slug = models.SlugField(max_length=280, unique=True, blank=True)
    sku = models.CharField(max_length=50, null=True, blank=True, unique=True, verbose_name="Артикул")
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="products", verbose_name="Бренд")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products", verbose_name="Категория")
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Цена")
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.RUB, verbose_name="Валюта")
    # Цена в рублях по текущему курсу ЦБ; пусто — курса для валюты нет
    price_rub = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, editable=False, verbose_name="Цена, ₽"
    )
    stock_qty = models.PositiveIntegerField(default=0, verbose_name="Остаток")
    description = models.TextField(blank=True, verbose_name="Описание")
    characteristics = models.JSONField(default=dict, blank=True, verbose_name="Характеристики")
    priority = models.IntegerField(default=0, db_index=True, verbose_name="Приоритет")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["-priority", "name"]
        indexes = [
            models.Index(fields=["category", "is_active"]),
            models.Index(fields=["brand", "is_active"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = transliterate_slug(self.name)
        # Цена в рублях по текущему курсу ЦБ; без курса — пусто, товар не продаётся
        from .currency import price_rub

        self.price_rub = price_rub(self)
        super().save(*args, **kwargs)

    @property
    def in_stock(self) -> bool:
        return self.stock_qty > 0


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images", verbose_name="Товар")
    image = models.ImageField(
        upload_to="products/",
        validators=[FileExtensionValidator(ALLOWED_IMAGE_EXTENSIONS), validate_image_size],
        verbose_name="Изображение",
    )
    alt_text = models.CharField(max_length=255, blank=True, verbose_name="Alt-текст")
    is_primary = models.BooleanField(default=False, verbose_name="Основное")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Порядок")

    class Meta:
        verbose_name = "Изображение товара"
        verbose_name_plural = "Изображения товаров"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.product.name} — изображение #{self.pk}"


class ProductOption(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="options", verbose_name="Товар")
    name = models.CharField(max_length=120, verbose_name="Название")
    value = models.CharField(max_length=120, verbose_name="Значение")
    price_delta = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Доплата")

    class Meta:
        verbose_name = "Опция товара"
        verbose_name_plural = "Опции товаров"

    def __str__(self):
        return f"{self.name}: {self.value}"


class ProductContentBlock(models.Model):
    class BlockType(models.TextChoices):
        HERO = "hero", "Hero (шапка)"
        FEATURES = "features", "Ряд фич"
        TECH = "tech", "Технологии"
        SPECS = "specs", "Характеристики"
        BRAND = "brand", "Блок бренда"
        TEXT = "text", "Текст"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="content_blocks", verbose_name="Товар")
    block_type = models.CharField(max_length=20, choices=BlockType.choices, verbose_name="Тип блока")
    title = models.CharField(max_length=255, blank=True, verbose_name="Заголовок")
    subtitle = models.CharField(max_length=255, blank=True, verbose_name="Подзаголовок")
    body = models.TextField(blank=True, verbose_name="Текст")
    image = models.ImageField(upload_to="content/", null=True, blank=True, verbose_name="Картинка")
    video_url = models.URLField(blank=True, verbose_name="Видео (ссылка)")
    data = models.JSONField(default=list, blank=True, verbose_name="Список (спеки/фичи)")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Порядок")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Блок контента"
        verbose_name_plural = "Блоки контента"

    def __str__(self):
        return f"{self.product.name} — {self.get_block_type_display()}"
    

class CurrencyRate(models.Model):
    """Официальный курс ЦБ РФ: рублей за одну единицу валюты."""

    currency = models.CharField(max_length=3, verbose_name="Валюта")
    date = models.DateField(verbose_name="Дата")
    rate = models.DecimalField(max_digits=14, decimal_places=6, verbose_name="Курс, ₽")
    fetched_at = models.DateTimeField(auto_now=True, verbose_name="Загружен")

    class Meta:
        verbose_name = "Курс ЦБ"
        verbose_name_plural = "Курсы ЦБ"
        ordering = ["-date", "currency"]
        constraints = [
            models.UniqueConstraint(fields=["currency", "date"], name="currency_rate_unique_day"),
        ]

    def __str__(self):
        return f"{self.currency} {self.date:%d.%m.%Y}: {self.rate}"
