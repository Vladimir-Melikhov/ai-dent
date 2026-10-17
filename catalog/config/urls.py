from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

admin.site.site_header = "Ai-Dent — управление"
admin.site.site_title = "Ai-Dent"
admin.site.index_title = "Магазин и ИИ-консультант"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("cart/", include("apps.orders.urls")),
    path("chat/", include("apps.chat.urls")),
    path("", include("storefront.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
