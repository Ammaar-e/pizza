from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed, NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsStaffUser
from apps.accounts.services import user_can_operate_store
from apps.core.audit import audit
from apps.core.middleware import client_ip
from apps.core.models import AuditLog
from apps.core.tenancy import accessible_store_ids, can_access_store, is_hq
from apps.core.throttling import LoginRateThrottle
from apps.core.views import ContextMixin
from apps.organization.models import Device, Store

from . import services as svc
from .models import DeviceAlert, DeviceConfiguration, DeviceCredential, DeviceRegistration

VIEW_PERMS = ("devices.view", "org.manage_devices")


def can_view(user) -> bool:
    return any(user.has_permission(p) for p in VIEW_PERMS)


class DeviceSerializer(serializers.ModelSerializer):
    store_number = serializers.CharField(source="store.number", read_only=True)
    franchisee_id = serializers.IntegerField(source="store.franchisee_id", read_only=True)
    franchisee_code = serializers.CharField(source="store.franchisee.code", read_only=True)
    current_user_name = serializers.CharField(source="current_user.username", read_only=True, default=None)
    profile_name = serializers.CharField(source="config_profile.name", read_only=True, default=None)
    is_active = serializers.BooleanField(read_only=True)
    open_alerts = serializers.SerializerMethodField()
    active_register_session = serializers.SerializerMethodField()
    version_status = serializers.SerializerMethodField()
    config_stale = serializers.SerializerMethodField()
    credential = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = ["id", "uid", "store", "store_number", "franchisee_id", "franchisee_code", "device_type", "name",
                  "status", "is_active", "parent_device", "kitchen_station", "registered_by", "approved_by",
                  "approved_at", "created_at", "deactivated_at", "deactivation_reason", "last_seen_at",
                  "last_heartbeat_at", "is_online", "last_ip", "software_version", "os_info", "app_status",
                  "sync_status", "pending_offline_count", "current_user_name", "current_user_since",
                  "config_profile", "profile_name", "local_settings", "config_version", "applied_config_version",
                  "config_stale", "version_status", "open_alerts", "active_register_session", "credential"]
        read_only_fields = [f for f in fields if f not in ("store", "device_type", "name", "parent_device",
                                                           "kitchen_station", "config_profile", "local_settings")]

    def get_open_alerts(self, obj):
        return [{"id": a.id, "kind": a.kind, "severity": a.severity, "message": a.message, "at": a.created_at}
                for a in obj.alerts.all() if a.resolved_at is None]

    def get_active_register_session(self, obj):
        from apps.payments.models import CashSession

        s = CashSession.objects.filter(register__device=obj, status=CashSession.STATUS_OPEN).select_related(
            "employee").first()
        return {"id": str(s.id), "employee": s.employee.username if s.employee else None,
                "opened_at": s.opened_at} if s else None

    def get_version_status(self, obj):
        return svc.version_status(obj)

    def get_config_stale(self, obj):
        return obj.applied_config_version < obj.config_version

    def get_credential(self, obj):
        c = obj.credentials.filter(revoked_at__isnull=True).order_by("-created_at").first()
        return {"key_id": c.key_id, "issued_at": c.created_at, "expires_at": c.expires_at,
                "last_used_at": c.last_used_at} if c else None


class DeviceViewSet(ContextMixin, viewsets.ModelViewSet):
    """Network device registry. Devices authenticate with their own revocable credential, which is
    returned exactly once (at registration, enrollment or rotation) and stored only as a hash."""

    serializer_class = DeviceSerializer
    permission_classes = [IsStaffUser]
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        if not can_view(self.request.user):
            raise PermissionDenied("Missing permission: devices.view")
        ids = accessible_store_ids(self.request.user)
        qs = Device.objects.select_related("store__franchisee", "current_user", "config_profile").prefetch_related(
            "alerts")
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        p = self.request.query_params
        for key, field in (("store", "store_id"), ("franchisee", "store__franchisee_id"),
                           ("device_type", "device_type"), ("status", "status")):
            if p.get(key):
                qs = qs.filter(**{f"{field}__in": p[key].split(",")})
        if p.get("online") in ("1", "0"):
            qs = qs.filter(is_online=p["online"] == "1")
        if p.get("q"):
            qs = qs.filter(Q(name__icontains=p["q"]) | Q(uid__icontains=p["q"]) | Q(store__number=p["q"]))
        return qs.order_by("store__number", "name")

    def _device(self, perm: str) -> Device:
        d = self.get_object()
        if not self.request.user.has_permission(perm):
            raise PermissionDenied(f"Missing permission: {perm}")
        return d

    @extend_schema(request=DeviceSerializer, responses={201: dict})
    def create(self, request, *args, **kwargs):
        """Register a device from a signed-in employee session. Unless the actor may approve devices
        (or HQ policy disables approval), the device starts as ``pending`` and cannot be used until
        approved. The response contains ``device_token`` once."""
        if not request.user.has_permission("org.manage_devices"):
            raise PermissionDenied("Missing permission: org.manage_devices")
        s = DeviceSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        store = s.validated_data["store"]
        if not user_can_operate_store(request.user, store):
            raise PermissionDenied("Store not accessible")
        device, token = svc.register_by_staff(
            store=store, device_type=s.validated_data["device_type"], name=s.validated_data["name"],
            user=request.user, ip=client_ip(request), user_agent=request.META.get("HTTP_USER_AGENT", ""),
            parent=s.validated_data.get("parent_device"), kitchen_station=s.validated_data.get("kitchen_station"))
        out = DeviceSerializer(device).data
        out["device_token"] = token
        return Response(out, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        u = self.request.user
        d = serializer.instance
        data = serializer.validated_data
        if "store" in data and data["store"] != d.store:
            raise ValidationError({"store": "Use the reassign action"})
        if "config_profile" in data and not u.has_permission("devices.configure_profiles"):
            raise PermissionDenied("Only headquarters can assign configuration profiles")
        if "local_settings" in data:
            if not u.has_permission("devices.local_settings"):
                raise PermissionDenied("Missing permission: devices.local_settings")
            allowed = set((d.config_profile.local_overridable if d.config_profile else []))
            bad = set(data["local_settings"]) - allowed
            if bad:
                raise ValidationError({"local_settings": f"Not locally configurable: {', '.join(sorted(bad))}"})
        if {"name", "kitchen_station", "parent_device"} & set(data) and not (
                u.has_permission("devices.administer") or u.has_permission("org.manage_devices")):
            raise PermissionDenied()
        changed = sorted(data)
        dev = serializer.save()
        if {"local_settings", "config_profile", "kitchen_station"} & set(changed):
            Device.objects.filter(pk=dev.pk).update(config_version=dev.config_version + 1)
        audit("device.update", dev, entity_type="Device", data={"fields": changed})
        svc.broadcast_device(dev, "device.updated")

    def perform_destroy(self, instance):
        if not self.request.user.has_permission("devices.administer"):
            raise PermissionDenied("Missing permission: devices.administer")
        svc.deactivate(instance, self.request.user, "Deactivated by administrator")

    @extend_schema(request=None, responses=DeviceSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        d = self.get_object()
        if not svc.can_approve(request.user, d.store):
            raise PermissionDenied("Not permitted to approve devices")
        return Response(DeviceSerializer(svc.decide(d, request.user, True, request.data.get("note", ""))).data)

    @extend_schema(request=None, responses=DeviceSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        d = self.get_object()
        if not svc.can_approve(request.user, d.store):
            raise PermissionDenied("Not permitted to approve devices")
        return Response(DeviceSerializer(svc.decide(d, request.user, False, request.data.get("note", ""))).data)

    @extend_schema(request=None, responses=DeviceSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        d = self._device("devices.administer")
        reason = request.data.get("reason") or ""
        if not reason:
            raise ValidationError({"reason": "Required"})
        return Response(DeviceSerializer(svc.deactivate(d, request.user, reason)).data)

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["post"], url_path="revoke-credentials")
    def revoke_credentials(self, request, pk=None):
        """Revoke every credential of the device immediately. The device must be re-enrolled with a
        new one-time code (returned here) before it can connect again."""
        d = self._device("devices.administer")
        n = svc.revoke_credentials(d, request.user, request.data.get("reason") or "revoked by administrator")
        reg, code = svc.reactivate(d, request.user)
        return Response({"revoked": n, "enrollment_code": code, "expires_at": reg.expires_at})

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        d = self._device("devices.administer")
        if d.status != Device.STATUS_DEACTIVATED:
            raise ValidationError("Device is not deactivated")
        reg, code = svc.reactivate(d, request.user)
        return Response({"enrollment_code": code, "expires_at": reg.expires_at})

    @extend_schema(request=None, responses=DeviceSerializer)
    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        d = self._device("devices.administer")
        store = Store.objects.filter(pk=request.data.get("store")).first()
        if store is None or not can_access_store(request.user, store):
            raise PermissionDenied("Target store not accessible")
        return Response(DeviceSerializer(svc.reassign(d, store, request.user)).data)

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        d = self.get_object()
        audit_rows = AuditLog.objects.filter(entity_type="Device", entity_id=str(d.pk))[:200]
        return Response({
            "audit": [{"at": a.created_at, "action": a.action, "actor": a.actor_label, "data": a.data}
                      for a in audit_rows],
            "heartbeats": [{"at": h.received_at, "ip": h.ip, "version": h.software_version, "app_status": h.app_status,
                            "sync_status": h.sync_status, "pending": h.pending_offline_count,
                            "config_version": h.config_version} for h in d.heartbeats.all()[:100]],
            "alerts": [{"id": a.id, "kind": a.kind, "severity": a.severity, "message": a.message, "at": a.created_at,
                        "resolved_at": a.resolved_at} for a in d.alerts.all()[:100]],
            "credentials": [{"key_id": c.key_id, "issued_at": c.created_at, "expires_at": c.expires_at,
                             "revoked_at": c.revoked_at, "revoked_reason": c.revoked_reason,
                             "last_used_at": c.last_used_at} for c in d.credentials.order_by("-created_at")],
            "effective_config": svc.effective_config(d),
        })

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"])
    def summary(self, request):
        qs = self.get_queryset()
        agg = qs.aggregate(total=Count("id"), active=Count("id", filter=Q(status="active")),
                           pending=Count("id", filter=Q(status="pending")),
                           deactivated=Count("id", filter=Q(status="deactivated")),
                           online=Count("id", filter=Q(status="active", is_online=True)))
        agg["offline"] = agg["active"] - agg["online"]
        agg["open_alerts"] = DeviceAlert.objects.filter(device__in=qs, resolved_at__isnull=True).count()
        return Response(agg)


class EnrollmentSerializer(serializers.Serializer):
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    device_type = serializers.ChoiceField(choices=Device.TYPES)
    name = serializers.CharField(max_length=100)
    valid_hours = serializers.IntegerField(min_value=1, max_value=168, default=24)


class DeviceRegistrationViewSet(ContextMixin, viewsets.GenericViewSet):
    """Pending registrations and one-time enrollment codes."""

    permission_classes = [IsStaffUser]

    def get_queryset(self):
        ids = accessible_store_ids(self.request.user)
        qs = DeviceRegistration.objects.select_related("store", "device", "requested_by")
        return qs if ids is None else qs.filter(store_id__in=ids)

    @extend_schema(responses=dict)
    def list(self, request):
        if not can_view(request.user):
            raise PermissionDenied()
        qs = self.get_queryset()
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response([{"id": r.id, "kind": r.kind, "status": r.status, "store": r.store.number,
                          "device_id": r.device_id, "device_type": r.device_type, "device_name": r.device_name,
                          "requested_by": r.requested_by.username if r.requested_by else None,
                          "expires_at": r.expires_at, "created_at": r.created_at} for r in qs[:200]])

    @extend_schema(request=EnrollmentSerializer, responses={201: dict})
    def create(self, request):
        """Issue a single-use enrollment code (valid ``valid_hours``) for a new device."""
        s = EnrollmentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if not request.user.has_permission("org.manage_devices") or not can_access_store(request.user, d["store"]):
            raise PermissionDenied()
        if not svc.can_approve(request.user, d["store"]):
            raise PermissionDenied("Only users who may approve devices can issue enrollment codes")
        reg, code = svc.create_enrollment(store=d["store"], device_type=d["device_type"], name=d["name"],
                                          user=request.user, hours=d["valid_hours"])
        return Response({"id": reg.id, "enrollment_code": code, "store_number": d["store"].number,
                         "expires_at": reg.expires_at}, status=201)


class AlertViewSet(ContextMixin, viewsets.GenericViewSet):
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        if not can_view(self.request.user):
            raise PermissionDenied()
        ids = accessible_store_ids(self.request.user)
        qs = DeviceAlert.objects.select_related("device__store")
        return qs if ids is None else qs.filter(device__store_id__in=ids)

    @extend_schema(parameters=[OpenApiParameter("open", str)], responses=dict)
    def list(self, request):
        qs = self.get_queryset()
        if request.query_params.get("open", "1") == "1":
            qs = qs.filter(resolved_at__isnull=True)
        return Response([{"id": a.id, "device_id": a.device_id, "device_name": a.device.name,
                          "store_number": a.device.store.number, "kind": a.kind, "severity": a.severity,
                          "message": a.message, "created_at": a.created_at, "resolved_at": a.resolved_at,
                          "acknowledged_at": a.acknowledged_at} for a in qs[:300]])

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        a = self.get_queryset().filter(pk=pk).first()
        if a is None:
            raise NotFound()
        a.acknowledged_by, a.acknowledged_at = request.user, timezone.now()
        if a.kind in (DeviceAlert.KIND_UNAUTHORIZED, DeviceAlert.KIND_AUTH_FAILURES):
            a.resolved_at = timezone.now()
        a.save()
        audit("device.alert_acknowledged", a.device, entity_type="Device", data={"alert": a.id, "kind": a.kind})
        return Response({"id": a.id, "acknowledged_at": a.acknowledged_at, "resolved_at": a.resolved_at})


class DeviceProfileSerializer(serializers.ModelSerializer):
    device_count = serializers.SerializerMethodField()

    class Meta:
        model = DeviceConfiguration
        fields = ["id", "name", "description", "device_types", "settings", "local_overridable", "approved_versions",
                  "minimum_version", "update_policy", "is_default", "version", "device_count", "updated_at"]
        read_only_fields = ["version", "updated_at"]

    def get_device_count(self, obj):
        return obj.devices.count()


class DeviceProfileViewSet(ContextMixin, viewsets.ModelViewSet):
    """HQ-managed configuration profiles. Every save creates a new version and bumps the
    ``config_version`` of all assigned devices so they re-synchronize."""

    serializer_class = DeviceProfileSerializer
    permission_classes = [IsStaffUser]
    queryset = DeviceConfiguration.objects.all().order_by("name")
    pagination_class = None
    http_method_names = ["get", "post", "patch"]

    def _check(self):
        if not self.request.user.has_permission("devices.configure_profiles"):
            raise PermissionDenied("Only headquarters can manage configuration profiles")

    def perform_create(self, serializer):
        self._check()
        svc.save_profile(serializer.save(), self.request.user, is_new=True)

    def perform_update(self, serializer):
        self._check()
        svc.save_profile(serializer.save(), self.request.user, is_new=False)

    @action(detail=True, methods=["get"])
    def versions(self, request, pk=None):
        p = self.get_object()
        return Response([{"version": v.version, "snapshot": v.snapshot, "created_at": v.created_at,
                          "created_by": v.created_by_id} for v in p.versions.all()[:50]])


# ---------------------------------------------------------------------------
# Device-authenticated endpoints (no employee session required)
# ---------------------------------------------------------------------------
def _device_from_request(request, allow_pending=False):
    raw = request.META.get("HTTP_X_DEVICE_TOKEN") or ""
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not raw and auth.startswith("Device "):
        raw = auth[len("Device "):].strip()
    if not raw:
        raise AuthenticationFailed("Device credential required (X-Device-Token)")
    try:
        return svc.authenticate_device(raw, client_ip(request), allow_pending=allow_pending)
    except svc.DeviceAuthError as e:
        raise AuthenticationFailed(str(e), code=e.code)


class DeviceEndpoint(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]


class EnrollView(DeviceEndpoint):
    throttle_classes = [LoginRateThrottle]

    @extend_schema(request=dict, responses={201: dict})
    def post(self, request):
        """Claim a one-time enrollment code: ``{store_number, enrollment_code, software_version}``.
        Returns the device's own credential (shown once)."""
        number = str(request.data.get("store_number") or "")
        code = str(request.data.get("enrollment_code") or "")
        if not number or not code:
            raise ValidationError("store_number and enrollment_code are required")
        device, token = svc.enroll(store_number=number, code=code, ip=client_ip(request),
                                   user_agent=request.META.get("HTTP_USER_AGENT", ""),
                                   software_version=str(request.data.get("software_version") or ""))
        return Response({"device": DeviceSerializer(device).data, "device_token": token}, status=201)


class HeartbeatView(DeviceEndpoint):
    @extend_schema(request=dict, responses=dict)
    def post(self, request):
        """Report device health: ``software_version, os_info, app_status, sync_status,
        pending_offline_count, config_version, signed_in_username, peripherals[]``. The server
        records its own receipt time; online/offline status is derived from it, not from the payload."""
        device, _ = _device_from_request(request, allow_pending=True)
        if device.status == Device.STATUS_PENDING:
            return Response({"status": device.status, "message": "Awaiting approval"}, status=202)
        return Response(svc.record_heartbeat(device, request.data or {}, client_ip(request)))


class DeviceConfigView(DeviceEndpoint):
    @extend_schema(responses=dict)
    def get(self, request):
        device, _ = _device_from_request(request)
        return Response(svc.effective_config(device))

    @extend_schema(request=dict, responses=dict)
    def post(self, request):
        """Acknowledge that ``config_version`` has been applied."""
        device, _ = _device_from_request(request)
        v = int(request.data.get("config_version") or 0)
        if v > device.config_version:
            raise ValidationError("Unknown configuration version")
        Device.objects.filter(pk=device.pk).update(applied_config_version=v)
        audit("device.config_applied", device, entity_type="Device", actor_label=f"device:{device.uid}",
              data={"config_version": v})
        return Response({"applied_config_version": v, "config_version": device.config_version})


class RotateCredentialView(DeviceEndpoint):
    @extend_schema(request=None, responses=dict)
    def post(self, request):
        """Rotate this device's credential. The credential used for this call is revoked."""
        device, cred = _device_from_request(request)
        return Response({"device_token": svc.rotate_credential(device, cred)})


class DeviceSelfView(DeviceEndpoint):
    @extend_schema(responses=dict)
    def get(self, request):
        device, _ = _device_from_request(request, allow_pending=True)
        return Response({"id": device.id, "uid": str(device.uid), "name": device.name, "status": device.status,
                         "store_id": device.store_id, "store_number": device.store.number,
                         "device_type": device.device_type})
