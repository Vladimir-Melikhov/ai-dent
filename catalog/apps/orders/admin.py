"""Админка заказов (ТЗ §10.5, §12): статус только действиями, правка состава до подтверждения."""

from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html

from .models import MAX_ITEM_QUANTITY, CartItem, Invoice, Order, OrderChange, OrderItem, OrderStatusHistory, Payment
from .services import (
    InvoiceError, OrderEditError, TransitionError, allowed_transitions, change_status, describe_change,
    edit_order_items, invoice_available, invoice_url, rub_text,
)
from .notifications import send_invoice_emails
from .views import MAX_REQUISITES_LENGTH

S = Order.Status
STATUS_BUTTONS = [  # кнопка на странице заказа: (статус, надпись)
    (S.CONFIRMED, "Подтвердить"),
    (S.PAID, "Оплачен"),
    (S.CANCELLED, "Отменить"),
]


def editable(order) -> bool:
    return order is not None and order.status == S.PENDING_CONFIRMATION


# --- позиции: количество, цена, удаление — только в pending_confirmation

class ItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if not editable(self.instance):
            return
        kept = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if self.forms and not kept:
            raise forms.ValidationError("Нельзя удалить последнюю товарную позицию — такой заказ отменяют")
        for form in kept:
            quantity = form.cleaned_data.get("quantity")
            price = form.cleaned_data.get("unit_price")
            if quantity is not None and not 1 <= quantity <= MAX_ITEM_QUANTITY:
                form.add_error("quantity", f"От 1 до {MAX_ITEM_QUANTITY}")
            if price is not None and price <= 0:
                form.add_error("unit_price", "Цена должна быть больше нуля")

    def changes(self) -> list[dict]:
        result = []
        for form in self.forms:
            if not form.instance.pk:
                continue
            if form in self.deleted_forms:
                result.append({"action": "remove", "item_id": form.instance.pk})
                continue
            if "quantity" in form.changed_data:
                result.append({"action": "quantity", "item_id": form.instance.pk,
                               "value": form.cleaned_data["quantity"]})
            if "unit_price" in form.changed_data:
                result.append({"action": "price", "item_id": form.instance.pk,
                               "value": form.cleaned_data["unit_price"]})
        return result


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    formset = ItemFormSet
    extra = 0
    verbose_name_plural = "Позиции (до подтверждения можно менять количество и цену за единицу)"
    fields = ["product_name", "source", "quantity", "unit_price", "line_total", "price_overridden"]
    readonly_fields = ["product_name", "source", "line_total", "price_overridden"]

    def get_queryset(self, request):
        # Строка доставки правится отдельным полем заказа
        return super().get_queryset(request).filter(kind=OrderItem.Kind.PRODUCT).order_by("pk")

    def get_readonly_fields(self, request, obj=None):
        if editable(obj):
            return self.readonly_fields
        return self.fields

    def has_add_permission(self, request, obj=None):
        return False  # новые товары в заказ не добавляются (ТЗ §10.5)

    def has_delete_permission(self, request, obj=None):
        return editable(obj)

    @admin.display(description="В каталоге")
    def source(self, item):
        # Цена каталога на момент оформления — рядом с ценой менеджера видно, какая дана скидка
        if item.source_price is None:
            return "—"
        if item.source_currency and item.source_currency != "RUB":
            return f"{item.source_price} {item.source_currency} · курс {item.exchange_rate}"
        return rub_text(item.source_price)


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class OrderStatusHistoryInline(ReadOnlyInline):
    model = OrderStatusHistory
    fields = ["created_at", "from_status", "to_status", "actor", "actor_user"]
    readonly_fields = fields


class OrderChangeInline(ReadOnlyInline):
    model = OrderChange
    fields = ["created_at", "actor_user", "action", "text"]
    readonly_fields = fields

    @admin.display(description="Изменение")
    def text(self, change):
        return describe_change(change)


# --- заказ

class OrderAdminForm(forms.ModelForm):
    delivery_price = forms.DecimalField(
        label="Доставка, ₽", required=False, min_value=Decimal(0), max_digits=12, decimal_places=2,
        help_text="Сумма вручную, отдельной строкой в счёте. Пусто — без доставки.",
    )
    # В базе — JSON-строка из формы /checkout/; менеджер видит и правит обычный текст, без кавычек и «null»
    requisites = forms.CharField(
        label="Реквизиты", required=False, max_length=MAX_REQUISITES_LENGTH,
        widget=forms.Textarea(attrs={"rows": 3, "cols": 60}), help_text="Текст из формы оформления — печатается в счёте.",
    )

    class Meta:
        model = Order
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            delivery = self.instance.items.filter(kind=OrderItem.Kind.DELIVERY).first()
            self.fields["delivery_price"].initial = delivery.unit_price if delivery else None

    def clean_requisites(self):
        return self.cleaned_data["requisites"].strip() or None


BUYER_FIELDS = ["buyer_type", "company_name", "inn", "buyer_name", "contact_name", "contact_phone",
                "contact_email", "requisites"]


def buyer_fields(order) -> list[str]:
    """Реквизиты свободным текстом — только у заказов с /checkout/: у заказа из чата поля покупателя раздельные."""
    fields = [name for name in BUYER_FIELDS if name != "requisites"]
    if order is None or not order.buyer_type:
        fields.append("requisites" if editable(order) else "requisites_text")
    return fields


DELIVERY_FIELDS = ["delivery_city", "delivery_address", "comment"]
ALWAYS_READONLY = ["number", "status", "created_by", "conversation_link", "created_at", "confirmed_at", "paid_at",
                   "payment_method", "payment_status", "total_amount", "total_at_creation", "pd_consent_at",
                   "delivery_line", "invoice_info", "requisites_text"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    form = OrderAdminForm
    change_form_template = "admin/orders/order/change_form.html"
    list_display = ["number", "created_at", "status", "total_amount", "created_by", "buyer_type", "contact_phone",
                    "payment_status"]
    list_filter = ["status", "created_by", "buyer_type", "payment_status"]
    search_fields = ["number", "contact_phone", "contact_name", "company_name", "buyer_name", "inn"]
    inlines = [OrderItemInline, OrderChangeInline, OrderStatusHistoryInline]
    actions = ["confirm_orders", "cancel_orders", "mark_paid", "resend_invoices"]

    def get_fieldsets(self, request, obj=None):
        delivery = ["delivery_price"] if editable(obj) else ["delivery_line"]
        return [
            ("Заказ", {"fields": ["number", "status", "created_by", "conversation_link", "created_at",
                                  "confirmed_at", "paid_at", "payment_method", "payment_status",
                                  "total_amount", "total_at_creation"]}),
            ("Покупатель", {"fields": [*buyer_fields(obj), "pd_consent_at"]}),
            ("Доставка", {"fields": [*DELIVERY_FIELDS, *delivery]}),
            ("Счёт", {"fields": ["invoice_info"]}),
        ]

    def get_readonly_fields(self, request, obj=None):
        if editable(obj):
            return ALWAYS_READONLY
        # После подтверждения заказ зафиксирован: данные покупателя попали в счёт
        return [*ALWAYS_READONLY, *BUYER_FIELDS, *DELIVERY_FIELDS]

    def has_add_permission(self, request):
        return False  # заказы создаются оформлением на сайте или в чате

    @admin.display(description="Диалог")
    def conversation_link(self, order):
        if not order.conversation_id:
            return "—"
        url = reverse("admin:chat_conversation_change", args=[order.conversation_id])
        return format_html('<a href="{}">Диалог #{}</a>', url, order.conversation_id)

    @admin.display(description="Реквизиты")
    def requisites_text(self, order):
        return str(order.requisites) if order.requisites else "—"

    @admin.display(description="Доставка")
    def delivery_line(self, order):
        delivery = order.items.filter(kind=OrderItem.Kind.DELIVERY).first()
        return rub_text(delivery.line_total) if delivery else "—"

    @admin.display(description="Счёт")
    def invoice_info(self, order):
        invoice = getattr(order, "invoice", None)
        if invoice is None:
            return "Будет выставлен при подтверждении"
        emailed = (f"письма отправлены {invoice.emailed_at:%d.%m.%Y %H:%M}" if invoice.emailed_at
                   else "письма не отправлены")
        legacy = " · выставлен до этапа 3" if invoice.legacy else ""
        if not invoice_available(order):
            return f"{invoice.number} — не выдаётся{legacy}"
        return format_html('<a href="{}">{}</a> от {} · {}{}', invoice_url(order), invoice.number,
                           invoice.issued_at.strftime("%d.%m.%Y"), emailed, legacy)

    # --- сохранение: правки позиций и доставки — через сервис с историей

    def save_formset(self, request, form, formset, change):
        if isinstance(formset, ItemFormSet):
            request._item_changes = formset.changes() if editable(form.instance) else []
            # Позиции сохраняет сервис с историей правок; журналу админки — пустые списки
            formset.new_objects, formset.changed_objects, formset.deleted_objects = [], [], []
            return
        formset.save()

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        order = form.instance
        if not editable(order):
            return
        changes = list(getattr(request, "_item_changes", []))
        if "delivery_price" in form.changed_data:
            changes.append({"action": "delivery", "value": form.cleaned_data.get("delivery_price")})
        if changes:
            try:
                edit_order_items(order, changes, request.user)
            except OrderEditError as error:
                messages.error(request, str(error))

    # --- смена статуса кнопками на странице заказа

    def change_view(self, request, object_id, form_url="", extra_context=None):
        order = self.get_object(request, object_id)
        buttons = []
        if order is not None:
            allowed = allowed_transitions(order)
            buttons = [(f"_status_{status}", label) for status, label in STATUS_BUTTONS if status in allowed]
            if getattr(order, "invoice", None) is not None and invoice_available(order):
                buttons.append(("_resend_invoice", "Отправить счёт повторно"))
        extra_context = {**(extra_context or {}), "status_buttons": buttons}
        return super().change_view(request, object_id, form_url, extra_context)

    def response_change(self, request, obj):
        for status, _ in STATUS_BUTTONS:
            if f"_status_{status}" in request.POST:
                self._apply(request, [obj], status)
                return HttpResponseRedirect(request.path)
        if "_resend_invoice" in request.POST:
            self._resend(request, [obj])
            return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)

    # --- действия в списке: каждый заказ — в своей транзакции, итог — списком

    def _apply(self, request, orders, status):
        done, failed = [], []
        for order in orders:
            try:
                change_status(order, status, actor_user=request.user)
                done.append(order.number)
            except (TransitionError, InvoiceError) as error:
                failed.append(str(error))
        word = {S.CONFIRMED: "Подтверждены", S.PAID: "Отмечены оплаченными", S.CANCELLED: "Отменены"}[status]
        if done:
            messages.success(request, f"{word}: {', '.join(done)}")
            if status == S.CONFIRMED:
                # Письма уходят после коммита — проверка результата встаёт в очередь за ними
                numbers = list(done)
                transaction.on_commit(lambda: self._report_emails(request, numbers))
        for problem in failed:
            messages.error(request, problem)

    def _report_emails(self, request, numbers):
        missed = list(Invoice.objects.filter(order__number__in=numbers, emailed_at__isnull=True)
                      .values_list("order__number", flat=True))
        if missed:
            messages.warning(request, f"Письма со счётом не отправлены: {', '.join(missed)}. "
                                      "Проверьте почту и нажмите «Отправить счёт повторно».")

    def _resend(self, request, orders):
        for order in orders:
            if not invoice_available(order):
                messages.error(request, f"Заказ № {order.number}: счёт не выдаётся")
            elif send_invoice_emails(order):
                messages.success(request, f"Заказ № {order.number}: счёт отправлен повторно")
            else:
                messages.warning(request, f"Заказ № {order.number}: письма не отправлены — проверьте почту и адреса")

    @admin.action(description="Подтвердить")
    def confirm_orders(self, request, queryset):
        self._apply(request, queryset, S.CONFIRMED)

    @admin.action(description="Отменить")
    def cancel_orders(self, request, queryset):
        self._apply(request, queryset, S.CANCELLED)

    @admin.action(description="Оплачен")
    def mark_paid(self, request, queryset):
        self._apply(request, queryset, S.PAID)

    @admin.action(description="Отправить счёт повторно")
    def resend_invoices(self, request, queryset):
        self._resend(request, queryset)


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ["product", "quantity", "user", "session", "created_at"]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["number", "order", "issued_at", "legacy", "emailed_at"]
    readonly_fields = ["order", "number", "pdf_path", "issued_at", "due_date", "legacy", "emailed_at"]

    def has_add_permission(self, request):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["order", "provider", "amount", "status"]
    list_filter = ["provider", "status"]
