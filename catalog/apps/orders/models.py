from django.conf import settings
from django.db import models
from django.utils import timezone

MAX_ITEM_QUANTITY = 999


class CartItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="cart_items"
    )
    session = models.ForeignKey(
        "accounts.AnonSession", null=True, blank=True, on_delete=models.CASCADE, related_name="cart_items"
    )
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="cart_items")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    selected_options = models.JSONField(default=dict, blank=True, verbose_name="Выбранные опции")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Позиция корзины"
        verbose_name_plural = "Корзина"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | models.Q(session__isnull=False),
                name="cart_item_user_or_session",
            ),
            # Одно правило количества для витрины и агента (ТЗ §5.3, D16)
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1, quantity__lte=MAX_ITEM_QUANTITY),
                name="cart_item_quantity_range",
            ),
        ]
        unique_together = [("user", "product"), ("session", "product")]

    def __str__(self):
        return f"{self.product_id} x{self.quantity}"


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PENDING_CONFIRMATION = "pending_confirmation", "Ожидает подтверждения"
        CONFIRMED = "confirmed", "Подтверждён"
        PAID = "paid", "Оплачен"
        CANCELLED = "cancelled", "Отменён"

    class PaymentMethod(models.TextChoices):
        INVOICE = "invoice", "Счёт"
        ONLINE = "online", "Онлайн"

    class PaymentStatus(models.TextChoices):
        NONE = "none", "Не требуется"
        AWAITING = "awaiting", "Ожидает оплаты"
        PAID = "paid", "Оплачено"
        FAILED = "failed", "Ошибка оплаты"

    class CreatedBy(models.TextChoices):
        AI = "ai", "ИИ"
        CATALOG = "catalog", "Каталог"

    class BuyerType(models.TextChoices):
        LEGAL = "legal", "Юрлицо"
        IP = "ip", "ИП"
        INDIVIDUAL = "individual", "Физлицо"

    number = models.CharField(max_length=50, unique=True, verbose_name="Номер заказа")
    session = models.ForeignKey(
        "accounts.AnonSession", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="orders", verbose_name="Сессия анонима",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="orders", verbose_name="Пользователь",
    )
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT, verbose_name="Статус")
    contact_name = models.CharField(max_length=255, null=True, blank=True, verbose_name="Имя / контактное лицо")
    contact_email = models.EmailField(null=True, blank=True, verbose_name="Email")
    contact_phone = models.CharField(max_length=30, null=True, blank=True, verbose_name="Телефон")
    phone_verified = models.BooleanField(default=False, verbose_name="Телефон подтверждён")
    requisites = models.JSONField(null=True, blank=True, verbose_name="Реквизиты")
    buyer_type = models.CharField(
        max_length=20, choices=BuyerType.choices, blank=True, default="", verbose_name="Тип покупателя"
    )
    inn = models.CharField(max_length=12, blank=True, default="", verbose_name="ИНН")
    company_name = models.CharField(max_length=255, blank=True, default="", verbose_name="Название юрлица")
    buyer_name = models.CharField(max_length=255, blank=True, default="", verbose_name="ФИО (ИП или физлицо)")
    delivery_city = models.CharField(max_length=255, blank=True, default="", verbose_name="Город доставки")
    delivery_address = models.TextField(blank=True, default="", verbose_name="Адрес доставки")
    comment = models.TextField(blank=True, default="", verbose_name="Комментарий")
    pd_consent_at = models.DateTimeField(null=True, blank=True, verbose_name="Согласие на обработку ПДн")
    conversation = models.ForeignKey(
        "chat.Conversation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="orders", verbose_name="Диалог",
    )
    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, null=True, blank=True, verbose_name="Метод оплаты"
    )
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.NONE, verbose_name="Статус оплаты"
    )
    total_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Итоговая сумма"
    )
    total_at_creation = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Сумма при оформлении"
    )
    currency = models.CharField(max_length=3, default="RUB", verbose_name="Валюта")
    created_by = models.CharField(
        max_length=20, choices=CreatedBy.choices, default=CreatedBy.CATALOG, verbose_name="Кто создал"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="Подтверждён в")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="Оплачен в")

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Заказ №{self.number}"


class OrderItem(models.Model):
    class Kind(models.TextChoices):
        PRODUCT = "product", "Товар"
        DELIVERY = "delivery", "Доставка"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", verbose_name="Заказ")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PRODUCT, verbose_name="Тип строки")
    # Пусто только у строки доставки
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, null=True, blank=True,
        related_name="order_items", verbose_name="Товар",
    )
    product_name = models.CharField(max_length=255, verbose_name="Снапшот названия")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Цена за единицу, ₽")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    selected_options = models.JSONField(default=dict, blank=True, verbose_name="Выбранные опции")
    line_total = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Сумма позиции, ₽")
    source_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Цена в каталоге"
    )
    source_currency = models.CharField(max_length=3, blank=True, default="", verbose_name="Валюта в каталоге")
    exchange_rate = models.DecimalField(
        max_digits=14, decimal_places=6, null=True, blank=True, verbose_name="Курс ЦБ"
    )
    # Цену задал менеджер (скидка) — при подтверждении по курсу не пересчитывается (ТЗ §10.5)
    price_overridden = models.BooleanField(default=False, verbose_name="Цена менеджера")

    class Meta:
        verbose_name = "Позиция заказа"
        verbose_name_plural = "Позиции заказа"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind="delivery") | models.Q(product__isnull=False),
                name="order_item_product_required",
            ),
            models.UniqueConstraint(
                fields=["order"],
                condition=models.Q(kind="delivery"),
                name="order_item_single_delivery",
            ),
        ]

    def __str__(self):
        return f"{self.product_name} x{self.quantity}"


class OrderStatusHistory(models.Model):
    class Actor(models.TextChoices):
        AI = "ai", "ИИ"
        CUSTOMER = "customer", "Клиент"
        MANAGER = "manager", "Менеджер"
        DIRECTOR = "director", "Директор"
        SYSTEM = "system", "Система"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history", verbose_name="Заказ")
    from_status = models.CharField(max_length=30, choices=Order.Status.choices, verbose_name="Из статуса")
    to_status = models.CharField(max_length=30, choices=Order.Status.choices, verbose_name="В статус")
    actor = models.CharField(max_length=20, choices=Actor.choices, verbose_name="Кто изменил")
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Пользователь"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата и время")

    class Meta:
        verbose_name = "История статуса заказа"
        verbose_name_plural = "История статусов заказов"
        ordering = ["-created_at"]

    def __str__(self):
        # Подпись строки в истории статусов на странице заказа — названиями, а не кодами
        return f"{self.get_from_status_display()} → {self.get_to_status_display()}"


class Invoice(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="invoice", verbose_name="Заказ")
    number = models.CharField(max_length=50, unique=True, verbose_name="Номер счёта")
    pdf_path = models.CharField(max_length=255, null=True, blank=True, verbose_name="Путь к PDF")
    # Момент подтверждения заказа (ТЗ §10.5); задаётся явно при выпуске и перевыпуске
    issued_at = models.DateTimeField(default=timezone.now, verbose_name="Выставлен")
    due_date = models.DateField(null=True, blank=True, verbose_name="Оплатить до")
    # Выставлен при оформлении, до этапа 3 — продолжает скачиваться (ТЗ §10.5)
    legacy = models.BooleanField(default=False, verbose_name="Выставлен до этапа 3")
    emailed_at = models.DateTimeField(null=True, blank=True, verbose_name="Отправлен письмом")

    class Meta:
        verbose_name = "Счёт"
        verbose_name_plural = "Счета"

    def __str__(self):
        return f"Счёт {self.number}"


class OrderChange(models.Model):
    """История правок состава заказа менеджером до подтверждения (ТЗ §10.5)."""

    class Action(models.TextChoices):
        QUANTITY_CHANGED = "quantity_changed", "Изменено количество"
        PRICE_CHANGED = "price_changed", "Изменена цена"
        ITEM_REMOVED = "item_removed", "Удалена позиция"
        DELIVERY_ADDED = "delivery_added", "Добавлена доставка"
        DELIVERY_CHANGED = "delivery_changed", "Изменена доставка"
        DELIVERY_REMOVED = "delivery_removed", "Удалена доставка"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="changes", verbose_name="Заказ")
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Кто изменил"
    )
    action = models.CharField(max_length=30, choices=Action.choices, verbose_name="Что изменено")
    # Позиция, было, стало, разница, итог до и после
    details = models.JSONField(default=dict, blank=True, verbose_name="Подробности")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата и время")

    class Meta:
        verbose_name = "Правка состава заказа"
        verbose_name_plural = "История правок состава"
        ordering = ["-created_at"]

    def __str__(self):
        return self.get_action_display()


class Payment(models.Model):
    class Provider(models.TextChoices):
        TINKOFF = "tinkoff", "Тинькофф"
        YOOKASSA = "yookassa", "ЮKassa"

    class Status(models.TextChoices):
        PENDING = "pending", "В обработке"
        SUCCEEDED = "succeeded", "Успешно"
        CANCELED = "canceled", "Отменён"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments", verbose_name="Заказ")
    provider = models.CharField(max_length=30, choices=Provider.choices, verbose_name="Провайдер")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Сумма")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name="Статус")
    external_id = models.CharField(max_length=255, null=True, blank=True, verbose_name="Внешний ID")

    class Meta:
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"

    def __str__(self):
        return f"Платёж #{self.pk} по заказу #{self.order_id}"
