from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from unfold.admin import ModelAdmin, TabularInline, StackedInline

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


class LoupeOptionInline(TabularInline):
    """Увеличения лупы: «увеличение → поле обзора». Блок показывается, когда стоит галочка «Бинокулярная лупа»."""

    model = LoupeOption
    extra = 2
    fields = ["zoom", "field_of_view", "sort_order"]
    verbose_name = "Увеличение"
    verbose_name_plural = "Бинокулярная лупа: увеличения и поле обзора"


class LoupeDistanceInline(TabularInline):
    model = LoupeDistance
    extra = 2
    fields = ["distance", "sort_order"]
    verbose_name = "Рабочее расстояние"
    verbose_name_plural = "Бинокулярная лупа: рабочие расстояния (от увеличения не зависят)"


class LoupeIpdInline(TabularInline):
    model = LoupeIpd
    extra = 2
    fields = ["value", "sort_order"]
    verbose_name = "Межзрачковое расстояние"
    verbose_name_plural = "Индивидуальные лупы: варианты межзрачкового расстояния (клиент выберет один)"


class LoupeFrameInline(TabularInline):
    model = LoupeFrame
    extra = 1
    fields = ["name", "sort_order"]
    verbose_name = "Оправа"
    verbose_name_plural = "Бинокулярная лупа: оправы (если у лупы они бывают разные; нет — оставьте пустым)"


class ProductContentBlockInline(StackedInline):
    model = ProductContentBlock
    extra = 1
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
    list_filter = ["brand", "category", "is_active"]
    list_editable = ["price", "stock_qty", "priority", "badge", "is_active"]
    search_fields = ["name", "sku"]
    prepopulated_fields = {"slug": ("name",)}
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
