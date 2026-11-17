from rest_framework.permissions import BasePermission


def RequireScope(scope: str):  # noqa: N802
    class _Scope(BasePermission):
        message = f"API client lacks scope '{scope}'"

        def has_permission(self, request, view):
            client = getattr(request._request, "api_client", None)
            if client is None:
                return False
            return client.has_scope(scope)

    return _Scope


class IsApiClient(BasePermission):
    def has_permission(self, request, view):
        return getattr(request._request, "api_client", None) is not None
