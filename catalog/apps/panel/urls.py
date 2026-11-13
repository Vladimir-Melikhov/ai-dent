from django.urls import path

from . import views

app_name = "panel"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("robots.txt", views.robots),

    path("deals/", views.deals, name="deals"),
    path("deals/save/", views.deal_save, name="deal_save"),
    path("deals/<int:pk>/stage/", views.deal_stage, name="deal_stage"),
    path("deals/<int:pk>/delete/", views.deal_delete, name="deal_delete"),
    path("deals/<int:pk>/archive/", views.deal_archive, name="deal_archive"),
    path("deals/<int:pk>/delete-order/", views.deal_order_delete, name="deal_order_delete"),

    path("finance/", views.finance, name="finance"),
    path("finance/save/", views.entry_save, name="entry_save"),
    path("finance/export.csv", views.finance_export, name="finance_export"),
    path("finance/<int:pk>/delete/", views.entry_delete, name="entry_delete"),

    path("categories/", views.categories, name="categories"),
    path("categories/save/", views.category_save, name="category_save"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),

    path("sales/", views.sales, name="sales"),
    path("orders/<int:pk>/to/<str:to>/", views.order_status, name="order_status"),
    path("ai/", views.ai, name="ai"),
    path("margin/", views.margin, name="margin"),

    path("docs/", views.documents, name="documents"),
    path("docs/quote/", views.quote_create, name="quote_create"),
    path("docs/quote/<uuid:pk>/revise/", views.quote_revise, name="quote_revise"),
    path("docs/invoice/", views.invoice_create, name="invoice_create"),
    path("docs/quote/<uuid:pk>.pdf", views.quote_file, name="quote_file"),
    path("docs/invoice/<str:number>.pdf", views.invoice_file, name="invoice_file"),

    path("servers/", views.servers, name="servers"),
    path("servers/save/", views.server_save, name="server_save"),
    path("servers/<int:pk>/delete/", views.server_delete, name="server_delete"),
    path("servers/<int:pk>/check/", views.server_check, name="server_check"),
]
