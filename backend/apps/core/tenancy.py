"""Tenant isolation: every queryset touching franchise data must be filtered here.

The rules:
* HQ users see everything.
* Franchisee owners/managers see only their franchisee's stores.
* Store-level employees see only the stores they are assigned to.
* External API clients see only the stores/franchisee they are bound to.
"""
from __future__ import annotations

from django.db.models import QuerySet


def is_hq(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or getattr(user, "is_hq", False)))


def accessible_store_ids(request_or_user) -> set[int] | None:
    """Return the set of store ids the actor may access, or None for 'all' (HQ)."""
    from apps.organization.models import Store

    client = getattr(request_or_user, "api_client", None)
    user = getattr(request_or_user, "user", request_or_user)
    if client is not None:
        return client.accessible_store_ids()
    if user is None or not user.is_authenticated:
        return set()
    if is_hq(user):
        return None
    if user.franchisee_id and user.is_franchise_level:
        return set(Store.objects.filter(franchisee_id=user.franchisee_id).values_list("id", flat=True))
    return set(user.store_assignments.filter(active=True).values_list("store_id", flat=True))


def accessible_franchisee_ids(request_or_user) -> set[int] | None:
    from apps.organization.models import Store

    store_ids = accessible_store_ids(request_or_user)
    if store_ids is None:
        return None
    user = getattr(request_or_user, "user", request_or_user)
    ids = set(Store.objects.filter(id__in=store_ids).values_list("franchisee_id", flat=True))
    if getattr(user, "franchisee_id", None) and getattr(user, "is_franchise_level", False):
        ids.add(user.franchisee_id)
    client = getattr(request_or_user, "api_client", None)
    if client is not None and client.franchisee_id:
        ids.add(client.franchisee_id)
    return ids


def scope_queryset(qs: QuerySet, request_or_user, store_field: str = "store") -> QuerySet:
    ids = accessible_store_ids(request_or_user)
    if ids is None:
        return qs
    return qs.filter(**{f"{store_field}__in": ids})


def scope_franchisee_queryset(qs: QuerySet, request_or_user, field: str = "franchisee") -> QuerySet:
    ids = accessible_franchisee_ids(request_or_user)
    if ids is None:
        return qs
    return qs.filter(**{f"{field}__in": ids})


def can_access_store(request_or_user, store) -> bool:
    ids = accessible_store_ids(request_or_user)
    return ids is None or store.id in ids
