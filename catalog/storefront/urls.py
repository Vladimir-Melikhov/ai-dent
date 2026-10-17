from django.urls import path
from django.urls import path

from . import views

app_name = "storefront"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("catalog/", views.catalog, name="catalog"),
    path("product/<slug:slug>/", views.product, name="product"),
    path("checkout/", views.checkout, name="checkout"),
    path("delivery/", views.delivery, name="delivery"),
    path("about/", views.about, name="about"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
]
