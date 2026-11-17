"""WebSocket authentication: JWT passed as ?token=... (browsers cannot set headers)."""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def _user_from_token(raw: str):
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import AccessToken

    from apps.accounts.models import User

    try:
        tok = AccessToken(raw)
    except TokenError:
        return AnonymousUser(), None
    user = User.objects.select_related("role").filter(pk=tok.get("user_id"), is_active=True).first()
    return (user or AnonymousUser()), tok.get("store_id")


class JWTQueryAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        qs = parse_qs(scope.get("query_string", b"").decode())
        token = (qs.get("token") or [None])[0]
        scope["user"], scope["pos_store_id"] = (await _user_from_token(token)) if token else (AnonymousUser(), None)
        return await self.app(scope, receive, send)
