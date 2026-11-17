from __future__ import annotations

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.core.models import TimeStampedModel


class Permission(models.Model):
    code = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=200)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Role(models.Model):
    LEVEL_HQ = "hq"
    LEVEL_FRANCHISE = "franchise"
    LEVEL_STORE = "store"
    LEVELS = [(LEVEL_HQ, "Headquarters"), (LEVEL_FRANCHISE, "Franchisee"), (LEVEL_STORE, "Store")]

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    level = models.CharField(max_length=20, choices=LEVELS)
    permissions = models.ManyToManyField(Permission, blank=True, related_name="roles")

    class Meta:
        ordering = ["level", "code"]

    def __str__(self):
        return self.name


class User(AbstractUser):
    """Every person who logs into any interface (HQ, franchise portal, POS, KDS)."""

    role = models.ForeignKey(Role, null=True, blank=True, on_delete=models.PROTECT, related_name="users")
    franchisee = models.ForeignKey(
        "organization.Franchisee", null=True, blank=True, on_delete=models.PROTECT, related_name="users"
    )
    employee_number = models.CharField(max_length=30, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    pin_hash = models.CharField(max_length=128, blank=True)
    manager_code_hash = models.CharField(max_length=128, blank=True)
    must_change_password = models.BooleanField(default=False)

    class Meta:
        ordering = ["username"]

    # ---- role helpers -------------------------------------------------
    @property
    def role_code(self) -> str | None:
        return self.role.code if self.role_id else None

    @property
    def is_hq(self) -> bool:
        return bool(self.role_id and self.role.level == Role.LEVEL_HQ)

    @property
    def is_franchise_level(self) -> bool:
        return bool(self.role_id and self.role.level == Role.LEVEL_FRANCHISE)

    def permission_codes(self) -> set[str]:
        if self.is_superuser:
            from .permissions_catalog import ALL

            return set(ALL)
        cache = getattr(self, "_perm_cache", None)
        if cache is None:
            cache = set(self.role.permissions.values_list("code", flat=True)) if self.role_id else set()
            self._perm_cache = cache
        return cache

    def has_permission(self, code: str) -> bool:
        return self.is_active and code in self.permission_codes()

    # ---- secrets -----------------------------------------------------
    def set_pin(self, pin: str | None):
        self.pin_hash = make_password(pin) if pin else ""

    def check_pin(self, pin: str) -> bool:
        return bool(self.pin_hash) and check_password(pin, self.pin_hash)

    def set_manager_code(self, code: str | None):
        self.manager_code_hash = make_password(code) if code else ""

    def check_manager_code(self, code: str) -> bool:
        return bool(self.manager_code_hash) and check_password(code, self.manager_code_hash)


class StoreAssignment(TimeStampedModel):
    """Assigns a store-level employee to a store they may operate in."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="store_assignments")
    store = models.ForeignKey("organization.Store", on_delete=models.CASCADE, related_name="assignments")
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("user", "store")]
