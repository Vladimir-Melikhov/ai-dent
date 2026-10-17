from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("request-code/", views.request_code, name="request_code"),
    path("verify-code/", views.verify_code, name="verify_code"),
    path("logout/", views.logout, name="logout"),
    path("delete/", views.account_delete, name="account_delete"),
    path("shortlist/", views.shortlist_list, name="shortlist_list"),
    path("shortlist/ids/", views.shortlist_ids, name="shortlist_ids"),
    path("shortlist/toggle/", views.toggle_shortlist, name="shortlist_toggle"),
]
