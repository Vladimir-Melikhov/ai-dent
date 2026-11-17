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


# validate_loupe и LOUPE_HELP остались только для старых миграций 0005–0006: поле loupe заменено полями ниже
LOUPE_OPTION_KEYS = {"zoom", "field", "depth"}
LOUPE_HELP = (
    'Только для бинокулярных луп. Рабочее расстояние от увеличения не зависит — оно в distances, '
    'а options — увеличения с полем обзора. Пример: {"kind": "individual", "distances": ["340 мм", "420 мм"], '
    '"options": [{"zoom": "2.5×", "field": "130 мм"}, {"zoom": "3.5×", "field": "95 мм"}]}. '
    'kind — "universal" или "individual"; distances и field по желанию, zoom обязателен.'
)


def validate_loupe(value):
    """Данные лупы: вид, увеличения и рабочие расстояния — иначе чат не сможет их показать."""
    if value in (None, {}):
        return
    if not isinstance(value, dict) or set(value) - {"kind", "options", "distances"}:
        raise ValidationError("Ожидается объект с ключами kind, options и distances.")
    if value.get("kind") not in ("universal", "individual"):
        raise ValidationError('kind: "universal" (универсальные) или "individual" (индивидуальные).')
    options = value.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= 12:
        raise ValidationError("options: список от 1 до 12 вариантов.")
    for option in options:
        if not isinstance(option, dict) or not option.get("zoom") or set(option) - LOUPE_OPTION_KEYS:
            raise ValidationError("Вариант: объект с обязательным zoom и, по желанию, field, depth.")
        if not all(isinstance(v, str) and len(v) <= 40 for v in option.values()):
            raise ValidationError("Значения варианта — короткие строки.")
    distances = value.get("distances", [])
    if not isinstance(distances, list) or len(distances) > 6 or not all(
            isinstance(d, str) and 0 < len(d) <= 40 for d in distances):
        raise ValidationError("distances: до 6 коротких строк, например «340 мм».")


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
    # Бинокулярные лупы: в чате вместо карточки — выбор увеличения и рабочего расстояния, поле обзора рядом
    is_loupe = models.BooleanField(default=False, db_default=False, verbose_name="Бинокулярная лупа")

    class LoupeKind(models.TextChoices):
        UNIVERSAL = "universal", "Универсальные"
        INDIVIDUAL = "individual", "Индивидуальные"

    loupe_kind = models.CharField(
        max_length=12, choices=LoupeKind.choices, blank=True, default="", db_default="", verbose_name="Тип лупы"
    )
    # Параметры лупы для сравнения в чате: при галочке обязательны (у фиксированных пишем «фикс»)
    loupe_depth = models.CharField(max_length=40, blank=True, default="", db_default="", verbose_name="Глубина фокуса",
                                   help_text="Например: 200 мм")
    loupe_weight = models.CharField(max_length=40, blank=True, default="", db_default="", verbose_name="Вес",
                                    help_text="Например: 44 г")
    loupe_tilt = models.CharField(max_length=40, blank=True, default="", db_default="", verbose_name="Угол наклона",
                                  help_text="Например: 0–40° или «фикс»")
    loupe_vertical = models.CharField(max_length=40, blank=True, default="", db_default="",
                                      verbose_name="Вертикальная регулировка", help_text="Например: до 6 мм или «фикс»")
    loupe_optics = models.CharField(max_length=40, blank=True, default="", db_default="",
                                    verbose_name="Оптическая система", help_text="Например: галилеевская")
    loupe_mount = models.CharField(max_length=40, blank=True, default="", db_default="",
                                   verbose_name="Система крепления", help_text="Например: TTL или Flip-up")
    # Метка выбора магазина: показывается в сравнении в чате («Хит», «Лучший выбор»)
    badge = models.CharField(
        max_length=24, blank=True, default="", db_default="", verbose_name="Метка",
        help_text="Например: Хит, Лучший выбор. Показывается в сравнении товаров в чате.",
    )
    priority = models.IntegerField(default=0, db_index=True, verbose_name="Приоритет")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")

    def loupe_data(self):
        """Данные конфигуратора лупы для формы заказа; None — не лупа или нет увеличений."""
        if not self.is_loupe:
            return None
        options = [{"zoom": o.zoom, "field": o.field_of_view} for o in self.loupe_options.all()]
        if not options:
            return None
        return {
            "kind": self.loupe_kind,
            "options": options,
            "distances": [d.distance for d in self.loupe_distances.all()],
            "frames": [f.name for f in self.loupe_frames.all()],
            # Межзрачковое расстояние бывает только у индивидуальных; нет списка — клиент вводит своё значение
            "ask_ipd": self.loupe_kind == self.LoupeKind.INDIVIDUAL,
            "ipds": [i.value for i in self.loupe_ipds.all()] if self.loupe_kind == self.LoupeKind.INDIVIDUAL else [],
        }

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

    def save(self, *args, **kwargs):
        from .images import optimize_field

        optimize_field(self.image)  # новая загрузка уходит в WebP до 1600 px
        super().save(*args, **kwargs)


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


class LoupeOption(models.Model):
    """Увеличение бинокулярной лупы и поле обзора при нём."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="loupe_options", verbose_name="Товар"
    )
    zoom = models.CharField(max_length=20, verbose_name="Увеличение", help_text="Например: 3.5×")
    field_of_view = models.CharField(
        max_length=20, blank=True, verbose_name="Поле обзора",
        help_text="Число и единица: 95 мм",
    )
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="Порядок")

    class Meta:
        verbose_name = "Увеличение"
        verbose_name_plural = "Увеличения и поле обзора"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.zoom


class LoupeDistance(models.Model):
    """Рабочее расстояние лупы: от увеличения не зависит, поэтому отдельный список."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="loupe_distances", verbose_name="Товар"
    )
    distance = models.CharField(max_length=20, verbose_name="Рабочее расстояние", help_text="Например: 340 мм")
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="Порядок")

    class Meta:
        verbose_name = "Рабочее расстояние"
        verbose_name_plural = "Рабочие расстояния"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.distance


class LoupeFrame(models.Model):
    """Вариант оправы лупы (классическая, спортивная…). Нет вариантов — выбора оправы нет."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="loupe_frames", verbose_name="Товар"
    )
    name = models.CharField(max_length=40, verbose_name="Оправа", help_text="Например: Классическая")
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="Порядок")

    class Meta:
        verbose_name = "Оправа"
        verbose_name_plural = "Оправы"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class LoupeIpd(models.Model):
    """Вариант межзрачкового расстояния индивидуальной лупы (у модели их обычно три-четыре)."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="loupe_ipds", verbose_name="Товар"
    )
    value = models.CharField(max_length=40, verbose_name="Межзрачковое расстояние", help_text="Например: 58–66 мм")
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="Порядок")

    class Meta:
        verbose_name = "Межзрачковое расстояние"
        verbose_name_plural = "Межзрачковые расстояния"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.value


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

    def save(self, *args, **kwargs):
        from .images import optimize_field

        optimize_field(self.image)
        super().save(*args, **kwargs)
    

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
