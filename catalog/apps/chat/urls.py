from django.urls import path

from apps.leads.views import create_lead
from apps.orders.views import chat_order
from . import views

app_name = "chat"

urlpatterns = [
    path("ticket/", views.ticket, name="ticket"),
    path("lead/", create_lead, name="lead"),
    path("order/", chat_order, name="order"),
    path("quote/<uuid:quote_id>/", views.quote_pdf, name="quote_pdf"),
]
