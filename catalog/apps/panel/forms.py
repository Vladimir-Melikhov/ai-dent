import re
from decimal import Decimal, InvalidOperation

from django import forms

from .models import Category, Deal, Entry, Server


class MoneyField(forms.DecimalField):
    """Сумма как её пишут: «1 500,50», «1500.5»."""

    def to_python(self, value):
        if isinstance(value, str):
            value = re.sub(r"[\s ₽]", "", value).replace(",", ".")
        return super().to_python(value)


def money(**kwargs):
    return MoneyField(max_digits=12, decimal_places=2, widget=forms.TextInput(attrs={"inputmode": "decimal"}), **kwargs)


class DealForm(forms.ModelForm):
    amount = money(min_value=Decimal("0"), label="Сумма, ₽", required=False)
    cost = money(min_value=Decimal("0"), label="Закупка, ₽", required=False)

    class Meta:
        model = Deal
        fields = ["title", "client", "contact", "amount", "cost", "source", "expected_date", "note"]
        widgets = {
            "expected_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    # У карточек с сайта и из чата этап, сумму и клиента задаёт заказ; менеджер правит только заметку и срок
    SYSTEM_LOCKED = ("title", "client", "contact", "amount", "source")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.is_system:
            for name in self.SYSTEM_LOCKED:
                self.fields[name].disabled = True

    def clean_amount(self):
        return self.cleaned_data.get("amount") or Decimal("0")

    def clean_cost(self):
        return self.cleaned_data.get("cost")  # пусто остаётся пустым: закупка не указана


class EntryForm(forms.ModelForm):
    amount = money(min_value=Decimal("0.01"), label="Сумма, ₽")

    class Meta:
        model = Entry
        fields = ["kind", "category", "amount", "date", "title", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def clean(self):
        cleaned = super().clean()
        category, kind = cleaned.get("category"), cleaned.get("kind")
        if category and kind and category.kind != kind:
            self.add_error("category", "Категория другого вида — выберите из списка")
        return cleaned


class CostForm(forms.Form):
    """Закупка по заказу целиком: спрашивается, когда заказ или сделка становятся оплаченными."""

    cost = money(min_value=Decimal("0"), label="Закупка, ₽")


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["kind", "name", "color"]

    def clean(self):
        cleaned = super().clean()
        kind, name = cleaned.get("kind"), (cleaned.get("name") or "").strip()
        if kind and name:
            same = Category.objects.filter(kind=kind, name__iexact=name).exclude(pk=self.instance.pk)
            if same.exists():
                self.add_error("name", "Такая категория уже есть")
        return cleaned


class ServerForm(forms.ModelForm):
    monthly_cost = money(min_value=Decimal("0"), label="Стоимость в месяц, ₽", required=False)

    class Meta:
        model = Server
        fields = ["name", "kind", "url", "purpose", "provider", "address", "login_url", "login", "monthly_cost", "note"]
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}

    def clean_monthly_cost(self):
        return self.cleaned_data.get("monthly_cost") or Decimal("0")
