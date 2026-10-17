from django.conf import settings


def chat(request):
    return {"chat_widget_enabled": settings.CHAT_WIDGET_ENABLED}
