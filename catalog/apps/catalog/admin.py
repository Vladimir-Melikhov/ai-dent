from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from unfold.admin import ModelAdmin, TabularInline, StackedInline

from .admin_fields import CharacteristicsField, NumberField, RangeField, numbers, with_current
from .currency import update_rates
from .models import (
    Brand,
    Category,
    CurrencyRate,
    LoupeDistance,
    LoupeFrame,
    LoupeIpd,
    LoupeOption,
    Product,
    ProductImage,
    ProductOption,
    ProductContentBlock,
)


class ProductImageInline(TabularInline):
    model = ProductImage
    extra = 1


class ProductOptionInline(TabularInline):
    model = ProductOption
    extra = 1


LOUPE_REQUIRED = ["loupe_kind", "loupe_depth", "loupe_weight", "loupe_tilt", "loupe_vertical", "loupe_optics",
                  "loupe_mount"]
OPTICS = ["Галилеевская", "Кеплеровская"]
MOUNTS = ["TTL", "Flip-up"]
FRAMES = ["Классическая", "Спортивная"]


class ProductForm(forms.ModelForm):
    loupe_depth = NumberField("мм", label="Глубина фокуса, мм", placeholder="200")
    loupe_weight = NumberField("г", label="Вес, г", placeholder="44")
    loupe_tilt = RangeField("°", single=lambda n, u: f"0–{n}°", allow_fixed=True, label="Угол наклона, °",
                            placeholder="0-40 или фикс", help_text="Диапазон, например 0-40. Не регулируется — «фикс».")
    loupe_vertical = RangeField("мм", single=lambda n, u: f"до {n} {u}", allow_fixed=True,
                                label="Вертикальная регулировка, мм", placeholder="6 или фикс",
                                help_text="Сколько мм хода, например 6. Нет регулировки — «фикс».")
    loupe_optics = forms.ChoiceField(required=False, label="Оптическая система")
    loupe_mount = forms.ChoiceField(required=False, label="Система крепления")
    characteristics = CharacteristicsField(
        required=False, label="Характеристики",
        help_text="Строки «Название: значение». Группа — строка в скобках: [Оптика], дальше её строки.",
    )

    class Meta:
        model = Product
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, options in (("loupe_optics", OPTICS), ("loupe_mount", MOUNTS)):
            self.fields[name].choices = with_current(options, getattr(self.instance, name, ""))

    def clean(self):
        data = super().clean()
        if data.get("is_loupe"):
            for name in LOUPE_REQUIRED:
                if not str(data.get(name) or "").strip():
                    self.add_error(name, "Обязательно для бинокулярной лупы")
        return data


class LoupeOptionForm(forms.ModelForm):
    zoom = NumberField("×", glue="", label="Увеличение, ×", placeholder="2.8", required=True)
    field_of_view = NumberField("мм", label="Поле обзора, мм", placeholder="120", required=True)

    class Meta:
        model = LoupeOption
        fields = ["zoom", "field_of_view"]

    def save(self, commit=True):
        number = numbers(self.instance.zoom)
        self.instance.sort_order = int(number[0] * 10) if number else 0  # по возрастанию увеличения
        return super().save(commit)


class LoupeDistanceForm(forms.ModelForm):
    distance = NumberField("мм", label="Рабочее расстояние, мм", placeholder="340", required=True)

    class Meta:
        model = LoupeDistance
        fields = ["distance"]

    def save(self, commit=True):
        number = numbers(self.instance.distance)
        self.instance.sort_order = int(number[0]) if number else 0
        return super().save(commit)


class LoupeIpdForm(forms.ModelForm):
    value = RangeField("мм", single=lambda n, u: f"{n} {u}", label="Межзрачковое расстояние, мм",
                       placeholder="58-66 или 62", required=True)

    class Meta:
        model = LoupeIpd
        fields = ["value"]

    def save(self, commit=True):
        number = numbers(self.instance.value)
        self.instance.sort_order = int(number[0]) if number else 0
        return super().save(commit)


class LoupeFrameForm(forms.ModelForm):
    name = forms.ChoiceField(label="Оправа")

    class Meta:
        model = LoupeFrame
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].choices = with_current(FRAMES, getattr(self.instance, "name", ""))


class LoupeOptionInline(TabularInline):
    """Увеличения лупы: «увеличение → поле обзора». Блок показывается, когда стоит галочка «Бинокулярная лупа»."""

    model = LoupeOption
    form = LoupeOptionForm
    extra = 1
    verbose_name = "Увеличение"
    verbose_name_plural = "Бинокулярная лупа: увеличения и поле обзора при каждом"


class LoupeDistanceInline(TabularInline):
    model = LoupeDistance
    form = LoupeDistanceForm
    extra = 1
    verbose_name = "Рабочее расстояние"
    verbose_name_plural = "Бинокулярная лупа: рабочие расстояния (от увеличения не зависят)"


class LoupeIpdInline(TabularInline):
    model = LoupeIpd
    form = LoupeIpdForm
    extra = 1
    verbose_name = "Межзрачковое расстояние"
    verbose_name_plural = "Индивидуальные лупы: варианты межзрачкового расстояния (клиент выберет один)"


class LoupeFrameInline(TabularInline):
    model = LoupeFrame
    form = LoupeFrameForm
    extra = 1
    verbose_name = "Оправа"
    verbose_name_plural = "Бинокулярная лупа: оправы (если у лупы они бывают разные; нет — оставьте пустым)"


class ProductContentBlockForm(forms.ModelForm):
    CONTENT_FIELDS = ("title", "subtitle", "body", "image", "video_url", "data")

    def has_changed(self):
        # У «Типа блока» в Unfold нет пустого варианта: браузер всегда присылает «Hero», и Django
        # считал нетронутый блок заполненным — каждое сохранение товара добавляло пустой блок.
        # Новый блок без содержимого не сохраняем.
        if self.instance.pk is None and not set(self.CONTENT_FIELDS) & set(self.changed_data):
            return False
        return super().has_changed()


class ProductContentBlockInline(StackedInline):
    model = ProductContentBlock
    form = ProductContentBlockForm
    # Блоки добавляются кнопкой «Добавить ещё», заготовка по умолчанию не нужна
    extra = 0
    fields = [
        "block_type",
        "sort_order",
        "title",
        "subtitle",
        "body",
        "image",
        "video_url",
        "data",
    ]


@admin.register(Brand)
class BrandAdmin(ModelAdmin):
    list_display = ["name", "country", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ["name", "parent", "sort_order"]
    list_editable = ["sort_order"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = [
        "name",
        "brand",
        "category",
        "price",
        "stock_qty",
        "priority",
        "badge",
        "is_active",
    ]
    list_editable = ["price", "stock_qty", "priority", "badge", "is_active"]
    search_fields = ["name", "sku"]
    form = ProductForm
    list_filter = ["is_loupe", "brand", "category", "is_active"]
    fieldsets = [
        (None, {"fields": ["name", "brand", "category", "sku", "description"]}),
        ("Цена и наличие", {"fields": ["price", "currency", "stock_qty"]}),
        ("Показ в каталоге", {"fields": ["is_active", "priority", "badge"]}),
        ("Бинокулярная лупа", {"fields": [
            "is_loupe", "loupe_kind", "loupe_optics", "loupe_mount", "loupe_depth", "loupe_weight",
            "loupe_tilt", "loupe_vertical", "loupe_demo",
        ]}),
        ("Характеристики", {"fields": ["characteristics"], "classes": ["collapse"]}),
        ("Адрес страницы", {"fields": ["slug"], "classes": ["collapse"]}),
    ]
    inlines = [
        ProductImageInline,
        ProductOptionInline,
        LoupeOptionInline,
        LoupeDistanceInline,
        LoupeIpdInline,
        LoupeFrameInline,
        ProductContentBlockInline,
    ]

    class Media:
        # Галочка «Бинокулярная лупа» раскрывает поля лупы; тип «индивидуальные» — межзрачковое расстояние
        js = ["admin/loupe.js"]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        product = form.instance
        if product.is_loupe and not product.loupe_options.exists():
            messages.warning(request, "У лупы нет ни одного увеличения: в чате она будет обычной карточкой.")
        if product.is_loupe and not product.loupe_kind:
            messages.warning(request, "У лупы не выбран тип (универсальные или индивидуальные).")
        if product.loupe_kind != Product.LoupeKind.INDIVIDUAL and product.loupe_ipds.exists():
            # Универсальным межзрачковое расстояние не нужно
            product.loupe_ipds.all().delete()


@admin.register(CurrencyRate)
class CurrencyRateAdmin(ModelAdmin):
    """Курсы ЦБ: только чтение и кнопка обновления."""

    list_display = ["date", "currency", "rate", "fetched_at"]
    list_filter = ["currency"]
    change_list_template = "admin/catalog/currencyrate/change_list.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        custom_urls = [
            path(
                "update/",
                self.admin_site.admin_view(self.update_view),
                name="catalog_currencyrate_update",
            ),
        ]
        return custom_urls + super().get_urls()

    def update_view(self, request):
        if (
            request.method != "POST"
            or not request.user.has_perm("catalog.view_currencyrate")
        ):
            return redirect("admin:catalog_currencyrate_changelist")

        result = update_rates()

        if result["loaded"]:
            self.message_user(
                request,
                f"Курсы загружены, пересчитано цен: {result['recalculated']}",
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "Курсы ЦБ не загрузились — попробуйте позже",
                messages.ERROR,
            )

        if result["stale"]:
            self.message_user(
                request,
                f"Нет свежего курса для: {', '.join(result['stale'])}",
                messages.WARNING,
            )

        return redirect("admin:catalog_currencyrate_changelist")
