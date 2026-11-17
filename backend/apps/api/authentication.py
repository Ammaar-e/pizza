from django.utils import timezone
from rest_framework import authentication, exceptions

from .models import ApiClient


class ApiClientUser:
    """Represents an authenticated API client as request.user (no staff permissions)."""

    is_authenticated = True
    is_active = True
    is_superuser = False
    is_hq = False
    is_franchise_level = False
    franchisee_id = None
    pk = None
    username = "api-client"

    def __init__(self, client: ApiClient):
        self.client = client
        self.pk = f"client-{client.pk}"
        self.username = f"api:{client.name}"

    def has_permission(self, code):
        return False


class ApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Api-Key"

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith(self.keyword + " "):
            return None
        raw = header[len(self.keyword) + 1:].strip()
        prefix, _, secret = raw.partition(".")
        if not prefix or not secret:
            raise exceptions.AuthenticationFailed("Malformed API key")
        client = ApiClient.objects.filter(key_prefix=prefix, is_active=True).select_related("franchisee").first()
        if client is None or client.key_hash != ApiClient.hash_secret(secret):
            raise exceptions.AuthenticationFailed("Invalid API key")
        ApiClient.objects.filter(pk=client.pk).update(last_used_at=timezone.now())
        request._request.api_client = client
        return ApiClientUser(client), client

    def authenticate_header(self, request):
        return self.keyword
