from __future__ import annotations

from apps.core.exceptions import DomainError, ManagerAuthorizationRequired

from .models import User


def user_can_operate_store(user: User, store) -> bool:
    if not user.is_active:
        return False
    if user.is_superuser or user.is_hq:
        return True
    if user.is_franchise_level:
        return user.franchisee_id == store.franchisee_id
    return user.store_assignments.filter(store=store, active=True).exists()


def authorize_manager(store, manager_code: str | None, permission: str, requesting_user: User | None = None) -> User:
    """Validate a manager authorization code for a restricted POS operation.

    If the requesting user already holds the permission, they authorise themselves.
    Otherwise a manager code must be supplied that belongs to a user who both holds
    the permission and can operate the store.
    """
    if requesting_user is not None and requesting_user.has_permission(permission) and requesting_user.has_permission(
        "manager.authorize"
    ):
        return requesting_user
    if not manager_code:
        raise ManagerAuthorizationRequired()
    candidates = User.objects.filter(is_active=True).exclude(manager_code_hash="").select_related("role")
    candidates = candidates.filter(store_assignments__store=store, store_assignments__active=True) | candidates.filter(
        franchisee=store.franchisee, role__level="franchise"
    )
    for cand in candidates.distinct():
        if cand.check_manager_code(manager_code):
            if not (cand.has_permission(permission) and cand.has_permission("manager.authorize")):
                raise DomainError("Manager lacks permission for this operation", code="manager_not_permitted",
                                  status_code=403)
            return cand
    raise DomainError("Invalid manager authorization code", code="invalid_manager_code", status_code=403)
