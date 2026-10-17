from django.urls import path
from . import views

app_name = "orders"

urlpatterns = [
    path("add/", views.cart_add, name="cart_add"),
    path("", views.cart_list, name="cart_list"),
    path("update/", views.cart_update, name="cart_update"),
    path("remove/", views.cart_remove, name="cart_remove"),
    path("count/", views.cart_count, name="cart_count"),
    path("checkout/", views.checkout, name="checkout"),
    path("orders/", views.orders_list, name="orders_list"),
    path("invoice/<str:number>/", views.invoice_download, name="invoice_download"),
]
