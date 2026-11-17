from django.contrib.auth import authenticate
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.audit import audit
from apps.core.tenancy import accessible_store_ids, is_hq
from apps.core.throttling import LoginRateThrottle
from apps.core.middleware import client_ip
from apps.core.views import ContextMixin
from apps.organization.models import Device, Store

from .models import Role, User
from .permissions import IsStaffUser
from .serializers import LoginSerializer, ManagerAuthorizeSerializer, POSLoginSerializer, RoleSerializer, \
    UserSerializer
from .services import authorize_manager, user_can_operate_store

STORE_ROLE_LIMITS = {"store_manager": {"shift_supervisor", "cashier", "kitchen", "delivery"},
                     "shift_supervisor": set()}


def profile(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "name": user.get_full_name() or user.username,
        "role": user.role_code,
        "role_level": user.role.level if user.role_id else ("hq" if user.is_superuser else None),
        "franchisee_id": user.franchisee_id,
        "permissions": sorted(user.permission_codes()),
        "store_ids": sorted(accessible_store_ids(user) or []) if not is_hq(user) else None,
        "must_change_password": user.must_change_password,
    }


def issue_tokens(user: User, store: Store | None = None) -> dict:
    refresh = RefreshToken.for_user(user)
    refresh["role"] = user.role_code
    if store is not None:
        refresh["store_id"] = store.id
    access = refresh.access_token
    if store is not None:
        access["store_id"] = store.id
    return {"access": str(access), "refresh": str(refresh)}


class LoginView(ContextMixin, APIView):
    """Portal login (headquarters / franchise portals)."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    @extend_schema(request=LoginSerializer, responses={200: dict})
    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = authenticate(request, username=s.validated_data["username"], password=s.validated_data["password"])
        if user is None or not user.is_active:
            audit("auth.login_failed", entity_type="User", entity_id=s.validated_data["username"], actor_label="anon")
            raise AuthenticationFailed("Invalid credentials")
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        audit("auth.login", user, actor=user)
        return Response({"tokens": issue_tokens(user), "user": profile(user)})


def store_context(store: Store) -> dict:
    from apps.organization.serializers import StoreSerializer

    return StoreSerializer(store).data


class POSLoginView(ContextMixin, APIView):
    """POS activation/login: store number + employee username + password (or PIN on a registered device)."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    @extend_schema(request=POSLoginSerializer, responses={200: dict})
    def post(self, request):
        s = POSLoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        store = Store.objects.select_related("franchisee__headquarters").filter(number=d["store_number"]).first()
        if store is None or not store.is_active:
            raise AuthenticationFailed("Unknown or inactive store number")
        device = None
        raw = request.META.get("HTTP_X_DEVICE_TOKEN")
        from apps.devices.services import DeviceAuthError, authenticate_device, policy

        if raw:
            try:
                device, _ = authenticate_device(raw, client_ip(request))
            except DeviceAuthError as e:
                raise AuthenticationFailed(str(e), code=e.code)
            if device.store_id != store.id:
                audit("auth.pos_login_wrong_store_device", device, entity_type="Device",
                      actor_label=f"anon:{d['username']}", data={"store": store.number})
                raise AuthenticationFailed("This device is not registered to this store")
        elif policy("require_registered_device_for_pos"):
            raise AuthenticationFailed("Sign-in is only permitted from a registered, approved device",
                                       code="device_required")
        user = None
        if d.get("password"):
            user = authenticate(request, username=d["username"], password=d["password"])
        elif d.get("pin"):
            if device is None:
                raise AuthenticationFailed("PIN login requires a registered device")
            cand = User.objects.filter(username=d["username"], is_active=True).first()
            user = cand if cand and cand.check_pin(d["pin"]) else None
        if user is None or not user.is_active:
            audit("auth.pos_login_failed", store, actor_label=f"anon:{d['username']}")
            raise AuthenticationFailed("Invalid employee credentials")
        if not user_can_operate_store(user, store):
            audit("auth.pos_login_denied", store, actor=user)
            raise AuthenticationFailed("Employee is not assigned to this store")
        if not (user.has_permission("pos.access") or user.has_permission("kitchen.access")):
            raise AuthenticationFailed("Employee does not have POS or kitchen access")
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        if device is not None:
            Device.objects.filter(pk=device.pk).update(current_user=user, current_user_since=timezone.now())
        audit("auth.pos_login", store, actor=user, data={"device": device.name if device else None,
                                                         "device_uid": str(device.uid) if device else None})
        return Response({
            "tokens": issue_tokens(user, store),
            "user": profile(user),
            "store": store_context(store),
            "device": {"id": device.id, "name": device.name, "type": device.device_type} if device else None,
        })


class MeView(APIView):
    permission_classes = [IsStaffUser]

    def get(self, request):
        return Response(profile(request.user))


class ManagerAuthorizeView(ContextMixin, APIView):
    """Validates a manager code for a restricted operation (UI pre-check)."""

    permission_classes = [IsStaffUser]

    @extend_schema(request=ManagerAuthorizeSerializer, responses={200: dict})
    def post(self, request):
        s = ManagerAuthorizeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        store = Store.objects.filter(pk=s.validated_data["store_id"]).first()
        if store is None or not user_can_operate_store(request.user, store):
            raise PermissionDenied("Store not accessible")
        mgr = authorize_manager(store, s.validated_data["manager_code"], s.validated_data["permission"])
        audit("auth.manager_authorized", store, actor=request.user, authorized_by=mgr,
              data={"permission": s.validated_data["permission"]})
        return Response({"authorized": True, "manager": {"id": mgr.id, "name": mgr.get_full_name() or mgr.username}})


class RoleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Role.objects.prefetch_related("permissions")
    serializer_class = RoleSerializer
    permission_classes = [IsStaffUser]
    pagination_class = None


class UserViewSet(ContextMixin, viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        u = self.request.user
        qs = User.objects.select_related("role", "franchisee").prefetch_related("store_assignments__store")
        if is_hq(u):
            pass
        elif u.is_franchise_level:
            qs = qs.filter(Q(franchisee_id=u.franchisee_id) |
                           Q(store_assignments__store__franchisee_id=u.franchisee_id)).distinct()
        else:
            store_ids = accessible_store_ids(u) or set()
            qs = qs.filter(store_assignments__store_id__in=store_ids, role__level=Role.LEVEL_STORE).distinct()
        if self.request.query_params.get("store"):
            qs = qs.filter(store_assignments__store_id=self.request.query_params["store"],
                           store_assignments__active=True)
        return qs

    def _check_can_manage(self, data, instance=None):
        actor = self.request.user
        role: Role = data.get("role") or (instance.role if instance else None)
        if role is None:
            raise ValidationError({"role": "Role is required"})
        stores = data.get("store_ids")
        if stores is None and instance is not None:
            stores = [a.store for a in instance.store_assignments.filter(active=True)]
        stores = stores or []
        franchisee = data.get("franchisee", instance.franchisee if instance else None)
        if is_hq(actor):
            if role.level == Role.LEVEL_HQ and not actor.has_permission("people.manage_hq_staff"):
                raise PermissionDenied("Cannot manage headquarters staff")
            if not (actor.has_permission("people.manage_hq_staff") or actor.has_permission(
                    "people.manage_franchise_staff")):
                raise PermissionDenied("Not permitted to manage users")
            return
        if role.level == Role.LEVEL_HQ:
            raise PermissionDenied("Cannot assign headquarters roles")
        if actor.is_franchise_level:
            if role.level == Role.LEVEL_FRANCHISE and not actor.has_permission("people.manage_franchise_staff"):
                raise PermissionDenied("Cannot manage franchise-level users")
            if not actor.has_permission("people.manage_store_staff"):
                raise PermissionDenied("Not permitted to manage users")
            if role.level == Role.LEVEL_FRANCHISE and (franchisee is None or franchisee.id != actor.franchisee_id):
                raise PermissionDenied("Franchise users must belong to your franchisee")
            if any(s.franchisee_id != actor.franchisee_id for s in stores):
                raise PermissionDenied("Store belongs to another franchisee")
            return
        # store-level manager
        allowed = STORE_ROLE_LIMITS.get(actor.role_code, set())
        if not actor.has_permission("people.manage_store_staff") or role.code not in allowed:
            raise PermissionDenied("Not permitted to assign this role")
        my_stores = accessible_store_ids(actor) or set()
        if not stores or any(s.id not in my_stores for s in stores):
            raise PermissionDenied("Employees can only be assigned to your stores")
        if franchisee is not None:
            raise PermissionDenied("Store managers cannot assign franchise membership")

    def perform_create(self, serializer):
        self._check_can_manage(serializer.validated_data)
        data = serializer.validated_data
        role = data["role"]
        if role.level == Role.LEVEL_FRANCHISE and not data.get("franchisee"):
            raise ValidationError({"franchisee": "Required for franchise-level roles"})
        if role.level == Role.LEVEL_STORE and not data.get("store_ids"):
            raise ValidationError({"store_ids": "Store employees must be assigned to at least one store"})
        user = serializer.save()
        audit("user.create", user, data={"role": role.code})

    def perform_update(self, serializer):
        self._check_can_manage(serializer.validated_data, serializer.instance)
        changed = sorted(k for k in self.request.data.keys())
        user = serializer.save()
        audit("user.update", user, data={"fields": changed})

    def perform_destroy(self, instance):
        self._check_can_manage({}, instance)
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        audit("user.deactivate", instance)
