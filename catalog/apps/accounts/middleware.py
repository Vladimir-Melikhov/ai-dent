from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import AnonSession

SESSION_COOKIE = "session_id"
LAST_SEEN_RESOLUTION = timedelta(days=1)


class AnonymousSessionMiddleware:
    """Находит гостевую сессию по cookie. Новую запись не создаёт —
    это делает get_owner_filter(create=True) при первом добавлении в корзину/избранное."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw = request.COOKIES.get(SESSION_COOKIE)
        session = None
        if raw:
            try:
                session = AnonSession.objects.filter(session_id=raw).first()
            except (ValueError, ValidationError):
                session = None
        if session is not None:
            now = timezone.now()
            if session.last_seen_at < now - LAST_SEEN_RESOLUTION:
                AnonSession.objects.filter(pk=session.pk).update(last_seen_at=now)
        request.anon_session = session
        request.anon_session_created = False

        response = self.get_response(request)

        if request.anon_session_created:
            response.set_cookie(
                SESSION_COOKIE,
                str(request.anon_session.session_id),
                max_age=60 * 60 * 24 * 365,
                httponly=True,
                samesite="Lax",
            )
        return response
