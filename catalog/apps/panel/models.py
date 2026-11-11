from django.db import models
from django.utils import timezone


class Category(models.Model):
    """Тип дохода или расхода."""

    class Kind(models.TextChoices):
        INCOME = "income", "Доход"
        EXPENSE = "expense", "Расход"

    kind = models.CharField(max_length=10, choices=Kind.choices, verbose_name="Вид")
    name = models.CharField(max_length=80, verbose_name="Название")
    # Номер цвета в палитре панели (1–8), см. static/css/panel.css
    color = models.PositiveSmallIntegerField(default=1, verbose_name="Цвет")

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "name"], name="panel_category_kind_name"),
            models.CheckConstraint(condition=models.Q(color__gte=1, color__lte=8), name="panel_category_color"),
        ]

    def __str__(self):
        return self.name


class Deal(models.Model):
    class Stage(models.TextChoices):
        NEW = "new", "Новая"
        NEGOTIATION = "negotiation", "Переговоры"
        OFFER = "offer", "КП отправлено"
        PAYMENT = "payment", "Ждём оплату"
        WON = "won", "Выиграна"
        LOST = "lost", "Проиграна"

    class Origin(models.TextChoices):
        MANUAL = "manual", "Вручную"
        ORDER = "order", "С сайта"
        QUOTE = "quote", "Из чата"

    OPEN_STAGES = (Stage.NEW, Stage.NEGOTIATION, Stage.OFFER, Stage.PAYMENT)

    title = models.CharField(max_length=160, verbose_name="Сделка")
    client = models.CharField(max_length=160, blank=True, default="", verbose_name="Клиент")
    contact = models.CharField(max_length=160, blank=True, default="", verbose_name="Контакт")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма, ₽")
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.NEW, verbose_name="Этап")
    source = models.CharField(max_length=80, blank=True, default="", verbose_name="Источник")
    expected_date = models.DateField(null=True, blank=True, verbose_name="Ожидаем закрыть")
    note = models.TextField(blank=True, default="", verbose_name="Заметка")
    # Карточки с сайта и из чата ведёт система: этап следует за статусом заказа, а деньги уже учтены заказом
    origin = models.CharField(max_length=10, choices=Origin.choices, default=Origin.MANUAL, verbose_name="Откуда")
    order = models.OneToOneField(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="panel_deal", verbose_name="Заказ"
    )
    quote = models.OneToOneField(
        "chat.Quote", null=True, blank=True, on_delete=models.SET_NULL, related_name="panel_deal", verbose_name="КП"
    )
    conversation = models.ForeignKey(
        "chat.Conversation", null=True, blank=True, on_delete=models.SET_NULL, related_name="panel_deals",
        verbose_name="Диалог",
    )
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Закрыта")
    # В архиве: не показывается на доске и не входит в цифры; сам заказ в магазине не меняется
    archived_at = models.DateTimeField(null=True, blank=True, verbose_name="В архиве с")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлена")

    class Meta:
        verbose_name = "Сделка"
        verbose_name_plural = "Сделки"
        ordering = ["-updated_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gte=0), name="panel_deal_amount")]

    def __str__(self):
        return self.title

    @property
    def is_system(self):
        return self.origin != self.Origin.MANUAL


class Entry(models.Model):
    """Доход или расход. Продажи магазина (оплаченные заказы) сюда не копируются — их панель берёт из заказов."""

    kind = models.CharField(max_length=10, choices=Category.Kind.choices, verbose_name="Вид")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="entries", verbose_name="Категория")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Сумма, ₽")
    date = models.DateField(default=timezone.localdate, db_index=True, verbose_name="Дата")
    title = models.CharField(max_length=160, blank=True, default="", verbose_name="Описание")
    note = models.TextField(blank=True, default="", verbose_name="Заметка")
    # Доход, созданный автоматически при выигрыше сделки
    deal = models.OneToOneField(
        Deal, null=True, blank=True, on_delete=models.CASCADE, related_name="entry", verbose_name="Сделка"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Операция"
        verbose_name_plural = "Операции"
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="panel_entry_amount")]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount}"


class Server(models.Model):
    class Kind(models.TextChoices):
        SITE = "site", "Сайт"
        API = "api", "API"
        VPS = "vps", "Сервер"
        DB = "db", "База данных"
        MAIL = "mail", "Почта"
        DNS = "dns", "Домены и DNS"
        MONITORING = "monitoring", "Мониторинг"
        OTHER = "other", "Другое"

    name = models.CharField(max_length=120, verbose_name="Название")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.VPS, verbose_name="Тип")
    url = models.URLField(max_length=300, blank=True, default="", verbose_name="Ссылка")
    purpose = models.CharField(max_length=300, blank=True, default="", verbose_name="Для чего")
    provider = models.CharField(max_length=120, blank=True, default="", verbose_name="Провайдер")
    address = models.CharField(max_length=200, blank=True, default="", verbose_name="IP или хост")
    login_url = models.URLField(max_length=300, blank=True, default="", verbose_name="Ссылка для входа")
    login = models.CharField(max_length=160, blank=True, default="", verbose_name="Логин")
    monthly_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Стоимость в месяц, ₽")
    note = models.TextField(blank=True, default="", verbose_name="Заметка")
    position = models.PositiveIntegerField(default=0, verbose_name="Порядок")

    class Meta:
        verbose_name = "Сервер"
        verbose_name_plural = "Серверы"
        ordering = ["position", "id"]

    def __str__(self):
        return self.name
