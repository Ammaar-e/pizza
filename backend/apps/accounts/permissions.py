from rest_framework.permissions import BasePermission


def HasPermission(code: str):  # noqa: N802 - factory that reads like a class
    class _Has(BasePermission):
        message = f"Missing permission: {code}"

        def has_permission(self, request, view):
            user = request.user
            return bool(user and user.is_authenticated and user.has_permission(code))

    _Has.__name__ = f"HasPermission_{code.replace('.', '_')}"
    return _Has


def HasAnyPermission(*codes: str):  # noqa: N802
    class _HasAny(BasePermission):
        message = f"Missing one of permissions: {', '.join(codes)}"

        def has_permission(self, request, view):
            user = request.user
            return bool(user and user.is_authenticated and any(user.has_permission(c) for c in codes))

    return _HasAny


class IsStaffUser(BasePermission):
    """Authenticated human user (not an external API client)."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and getattr(request, "api_client", None) is None)
