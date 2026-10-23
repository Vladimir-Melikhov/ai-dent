from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from .currency import update_rates
from .models import Brand, Category, CurrencyRate, Product, ProductImage, ProductOption, ProductContentBlock


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class ProductOptionInline(admin.TabularInline):
    model = ProductOption
    extra = 1


class ProductContentBlockInline(admin.StackedInline):
    model = ProductContentBlock
    extra = 1
    fields = ["block_type", "sort_order", "title", "subtitle", "body", "image", "video_url", "data"]


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ["name", "country", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "parent", "sort_order"]
    list_editable = ["sort_order"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "brand", "category", "price", "stock_qty", "priority", "is_active"]
    list_filter = ["brand", "category", "is_active"]
    list_editable = ["price", "stock_qty", "priority", "is_active"]
    search_fields = ["name", "sku"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductImageInline, ProductOptionInline, ProductContentBlockInline]



@admin.register(CurrencyRate)
class CurrencyRateAdmin(admin.ModelAdmin):
    """Курсы ЦБ: только чтение и кнопка «Обновить курсы» над списком."""

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
        return [path("update/", self.admin_site.admin_view(self.update_view), name="catalog_currencyrate_update"),
                *super().get_urls()]

    def update_view(self, request):
        if request.method != "POST" or not request.user.has_perm("catalog.view_currencyrate"):
            return redirect("admin:catalog_currencyrate_changelist")
        result = update_rates()
        if result["loaded"]:
            self.message_user(request, f"Курсы загружены, пересчитано цен: {result['recalculated']}", messages.SUCCESS)
        else:
            self.message_user(request, "Курсы ЦБ не загрузились — попробуйте позже", messages.ERROR)
        if result["stale"]:
            self.message_user(request, f"Нет свежего курса для: {', '.join(result['stale'])}", messages.WARNING)
        return redirect("admin:catalog_currencyrate_changelist")
