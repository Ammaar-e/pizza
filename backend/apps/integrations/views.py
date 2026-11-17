from __future__ import annotations

import json
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsStaffUser
from apps.core.audit import audit
from apps.core.encryption import mask_secrets
from apps.core.tenancy import accessible_franchisee_ids, can_access_store, is_hq
from apps.core.throttling import WebhookRateThrottle
from apps.core.views import ContextMixin
from apps.organization.models import Store

from .adapters import WebhookAuthError, get_adapter
from .adapters.base import ProviderError
from .adapters.registry import ADAPTERS
from .models import (
    ConnectionStatus,
    IntegrationLog,
    IntegrationProvider,
    ProductMapping,
    ProviderAccount,
    StoreIntegration,
    SyncRecord,
    WebhookEvent,
    default_integration_settings,
)
from .services import push_menu, test_connection
from .tasks import process_webhook_event_task

logger = logging.getLogger("integrations")

SAFE_HEADER_PREFIXES = ("content-type", "user-agent", "x-uber-signature", "x-request-id", "x-forwarded-for")


def webhook_url(provider_code: str, account: ProviderAccount | None) -> str:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    suffix = f"{account.webhook_key}/" if account else ""
    return f"{base}/api/v1/integrations/{provider_code}/webhooks/{suffix}"


# ---------------------------------------------------------------------------
# Inbound webhooks (public, authenticated by provider signature/token)
# ---------------------------------------------------------------------------
class WebhookReceiverView(APIView):
    """Receives provider webhooks. Authenticates, persists, acknowledges immediately and processes
    asynchronously — provider acknowledgement deadlines never wait on kitchen staff."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WebhookRateThrottle]

    @extend_schema(request=dict, responses={200: None, 202: None, 401: dict})
    def post(self, request, provider, account_key=None):
        raw = request.body
        prov = IntegrationProvider.objects.filter(code=provider).first()
        if prov is None or provider not in ADAPTERS:
            raise NotFound("Unknown provider")
        adapter = get_adapter(prov)
        if not adapter.available:
            IntegrationLog.objects.create(provider=prov, level="error", direction="inbound", operation="webhook",
                                          message="Webhook received for provider without implemented API access")
            return Response({"error": {"code": "provider_not_available",
                                       "message": "Integration is not available for this provider"}}, status=501)
        accounts = ProviderAccount.objects.filter(provider=prov, is_active=True)
        accounts = accounts.filter(webhook_key=account_key) if account_key else accounts.filter(franchisee__isnull=True)
        account = None
        for acc in accounts:
            try:
                adapter.verify_webhook(request, raw, acc)
                account = acc
                break
            except WebhookAuthError:
                continue
        if account is None:
            IntegrationLog.objects.create(provider=prov, level="warning", direction="inbound", operation="webhook",
                                          message="Webhook authentication failed")
            return Response({"error": {"code": "unauthorized", "message": "Webhook authentication failed"}},
                            status=401)
        try:
            payload = json.loads(raw or b"{}")
            events = adapter.parse_events(payload)
        except (ValueError, ProviderError) as exc:
            IntegrationLog.objects.create(provider=prov, level="error", direction="inbound", operation="webhook",
                                          message=f"Unparseable webhook: {exc}")
            return Response({"error": {"code": "invalid_payload", "message": str(exc)}}, status=400)
        headers = {k: v for k, v in request.headers.items() if k.lower().startswith(SAFE_HEADER_PREFIXES)}
        for pe in events:
            try:
                with transaction.atomic():
                    ev = WebhookEvent.objects.create(provider=prov, account=account, event_id=pe.event_id[:200],
                                                     event_type=pe.event_type, external_order_id=pe.external_order_id,
                                                     headers=headers, payload=payload)
                created = True
            except IntegrityError:
                # Provider retry / duplicate delivery: never create a second order.
                ev = WebhookEvent.objects.get(provider=prov, event_id=pe.event_id[:200])
                WebhookEvent.objects.filter(pk=ev.pk).update(delivery_count=F("delivery_count") + 1)
                created = ev.status == WebhookEvent.STATUS_FAILED  # re-process failed events on redelivery
            if created:
                transaction.on_commit(lambda pk=ev.pk: process_webhook_event_task.delay(pk))
        return HttpResponse(status=adapter.webhook_success_status)


# ---------------------------------------------------------------------------
# Provider management (HQ)
# ---------------------------------------------------------------------------
class IntegrationProviderSerializer(serializers.ModelSerializer):
    supports = serializers.SerializerMethodField()

    class Meta:
        model = IntegrationProvider
        fields = ["id", "code", "name", "network_enabled", "api_available", "access_requirements",
                  "documentation_url", "ack_deadline_seconds", "capabilities", "supports"]
        read_only_fields = ["code", "name", "api_available", "access_requirements", "documentation_url",
                            "ack_deadline_seconds", "capabilities"]

    def get_supports(self, obj):
        cls = ADAPTERS.get(obj.code)
        if not cls:
            return {}
        return {"orders": cls.available, "ready_signal": cls.supports_ready_signal,
                "merchant_cancel": cls.supports_merchant_cancel, "menu_push": cls.supports_menu_push}


class IntegrationProviderViewSet(ContextMixin, viewsets.ModelViewSet):
    queryset = IntegrationProvider.objects.all().order_by("name")
    serializer_class = IntegrationProviderSerializer
    permission_classes = [IsStaffUser]
    http_method_names = ["get", "patch"]
    pagination_class = None
    lookup_field = "code"

    def perform_update(self, serializer):
        if not self.request.user.has_permission("integrations.manage_providers"):
            raise PermissionDenied("Only headquarters can change network-wide provider permissions")
        p = serializer.save()
        audit("integration_provider.update", p, data={"network_enabled": p.network_enabled})


class ProviderAccountSerializer(serializers.ModelSerializer):
    provider = serializers.SlugRelatedField(slug_field="code", queryset=IntegrationProvider.objects.all())
    credentials = serializers.DictField(write_only=True, required=False)
    credentials_status = serializers.SerializerMethodField()
    webhook_url = serializers.SerializerMethodField()

    class Meta:
        model = ProviderAccount
        fields = ["id", "provider", "franchisee", "name", "environment", "is_active", "credentials",
                  "credentials_status", "webhook_url", "created_at"]

    def get_credentials_status(self, obj):
        return mask_secrets(obj.credentials or {})

    def get_webhook_url(self, obj):
        return webhook_url(obj.provider.code, obj)

    def update(self, instance, validated):
        creds = validated.pop("credentials", None)
        if creds is not None:
            merged = dict(instance.credentials or {})
            for k, v in creds.items():
                if v in (None, ""):
                    merged.pop(k, None)
                else:
                    merged[k] = str(v)
            instance.credentials = merged
            instance.token_cache = {}
        return super().update(instance, validated)


class ProviderAccountViewSet(ContextMixin, viewsets.ModelViewSet):
    serializer_class = ProviderAccountSerializer
    permission_classes = [IsStaffUser]
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        qs = ProviderAccount.objects.select_related("provider").order_by("id")
        ids = accessible_franchisee_ids(self.request.user)
        if ids is None:
            return qs
        # Franchisees see their own accounts and the names of HQ-owned shared accounts (never secrets).
        return qs.filter(franchisee_id__in=ids) | qs.filter(franchisee__isnull=True)

    def _check(self, franchisee):
        u = self.request.user
        if franchisee is None:
            if not u.has_permission("integrations.manage_providers"):
                raise PermissionDenied("Only headquarters can manage shared provider accounts")
        elif not (is_hq(u) or (u.franchisee_id == franchisee.id and u.has_permission("integrations.configure_store"))):
            raise PermissionDenied()

    def perform_create(self, serializer):
        self._check(serializer.validated_data.get("franchisee"))
        acc = serializer.save(credentials={k: str(v) for k, v in
                                           (serializer.validated_data.pop("credentials", None) or {}).items() if v})
        audit("provider_account.create", acc, franchisee=acc.franchisee)

    def perform_update(self, serializer):
        self._check(serializer.instance.franchisee)
        acc = serializer.save()
        audit("provider_account.update", acc, franchisee=acc.franchisee,
              data={"credential_keys": sorted((self.request.data.get("credentials") or {}).keys())})

    def perform_destroy(self, instance):
        self._check(instance.franchisee)
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        audit("provider_account.deactivate", instance, franchisee=instance.franchisee)


# ---------------------------------------------------------------------------
# Store-level integrations (franchisee)
# ---------------------------------------------------------------------------
class StoreIntegrationSerializer(serializers.ModelSerializer):
    provider = serializers.CharField(source="provider.code", read_only=True)
    provider_name = serializers.CharField(source="provider.name", read_only=True)
    credentials_status = serializers.SerializerMethodField()
    webhook_url = serializers.SerializerMethodField()
    account_name = serializers.CharField(source="account.name", read_only=True, default=None)
    recent_errors = serializers.SerializerMethodField()

    class Meta:
        model = StoreIntegration
        fields = ["id", "store", "provider", "provider_name", "account", "account_name", "enabled",
                  "external_store_id", "settings", "connection_status", "last_success_at", "last_error_at",
                  "last_error", "menu_sync_status", "menu_synced_at", "external_menu_id", "credentials_status",
                  "webhook_url", "recent_errors"]

    def get_credentials_status(self, obj):
        return mask_secrets(obj.credentials or {})

    def get_webhook_url(self, obj):
        return webhook_url(obj.provider.code, obj.account) if obj.account_id else None

    def get_recent_errors(self, obj) -> list:
        return [{"at": log.created_at, "operation": log.operation, "message": log.message}
                for log in obj.logs.filter(level="error")[:5]] if obj.pk else []


class StoreIntegrationConfigSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    account = serializers.PrimaryKeyRelatedField(queryset=ProviderAccount.objects.all(), required=False,
                                                 allow_null=True)
    external_store_id = serializers.CharField(required=False, allow_blank=True, max_length=100)
    settings = serializers.DictField(required=False)
    credentials = serializers.DictField(required=False, help_text="Write-only store-level secrets")


def _store(request, store_id, perm="integrations.view") -> Store:
    store = Store.objects.select_related("franchisee").filter(pk=store_id).first()
    if store is None or not can_access_store(request.user, store):
        raise NotFound("Store not found")
    if not request.user.has_permission(perm):
        raise PermissionDenied(f"Missing permission: {perm}")
    return store


def _integration(store, provider_code, create=False) -> StoreIntegration:
    prov = IntegrationProvider.objects.filter(code=provider_code).first()
    if prov is None:
        raise NotFound("Unknown provider")
    si = StoreIntegration.objects.select_related("provider", "account").filter(store=store, provider=prov).first()
    if si is None:
        si = StoreIntegration(store=store, provider=prov, settings=default_integration_settings())
        if create:
            si.save()
    return si


class StoreIntegrationsView(ContextMixin, APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(responses=StoreIntegrationSerializer(many=True))
    def get(self, request, store_id):
        store = _store(request, store_id)
        out = []
        for prov in IntegrationProvider.objects.order_by("name"):
            si = _integration(store, prov.code)
            data = StoreIntegrationSerializer(si).data
            data["network_enabled"] = prov.network_enabled
            data["api_available"] = prov.api_available
            data["access_requirements"] = prov.access_requirements
            data["supports"] = IntegrationProviderSerializer(prov).data["supports"]
            out.append(data)
        return Response(out)


class StoreIntegrationDetailView(ContextMixin, APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(request=StoreIntegrationConfigSerializer, responses=StoreIntegrationSerializer)
    def put(self, request, store_id, provider):
        store = _store(request, store_id, "integrations.configure_store")
        s = StoreIntegrationConfigSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        si = _integration(store, provider, create=True)
        if "account" in d:
            acc = d["account"]
            if acc is not None and (acc.provider_id != si.provider_id or
                                    (acc.franchisee_id and acc.franchisee_id != store.franchisee_id)):
                raise PermissionDenied("Provider account not available to this store")
            si.account = acc
        if "external_store_id" in d:
            si.external_store_id = d["external_store_id"].strip()
        if "settings" in d:
            merged = default_integration_settings()
            merged.update(si.settings or {})
            for k, v in d["settings"].items():
                if k == "acceptance_policy" and isinstance(v, dict):
                    merged["acceptance_policy"] = {**merged["acceptance_policy"], **v}
                elif k in merged:
                    merged[k] = v
            si.settings = merged
        if "credentials" in d:
            creds = dict(si.credentials or {})
            for k, v in d["credentials"].items():
                if v in (None, ""):
                    creds.pop(k, None)
                else:
                    creds[k] = str(v)
            si.credentials = creds
        if "enabled" in d:
            if d["enabled"] and not si.provider.network_enabled:
                raise PermissionDenied(f"{si.provider.name} is not permitted by headquarters")
            si.enabled = d["enabled"]
        if not si.enabled:
            si.connection_status = ConnectionStatus.DISABLED
        elif not si.provider.api_available:
            si.connection_status = ConnectionStatus.PENDING_ACCESS
        elif si.connection_status in (ConnectionStatus.DISABLED, ConnectionStatus.NOT_CONFIGURED):
            si.connection_status = ConnectionStatus.NOT_CONFIGURED if not (si.account_id and (
                si.external_store_id or provider == "doordash")) else ConnectionStatus.PENDING_ACCESS
        si.save()
        audit("store_integration.update", si, store=store,
              data={"enabled": si.enabled, "external_store_id": si.external_store_id,
                    "credential_keys": sorted((d.get("credentials") or {}).keys())})
        return Response(StoreIntegrationSerializer(si).data)

    @extend_schema(request=None, responses=dict)
    def post(self, request, store_id, provider, op):
        store = _store(request, store_id, "integrations.configure_store")
        si = _integration(store, provider)
        if si.pk is None:
            raise ValidationError("Configure the integration first")
        if op == "test":
            ok, msg = test_connection(si, request.user)
            si.refresh_from_db()
            return Response({"ok": ok, "message": msg, "connection_status": si.connection_status})
        if op == "menu-sync":
            if not si.enabled:
                raise ValidationError("Integration is disabled")
            rec = push_menu(si, request.user)
            return Response({"status": rec.status, "reference": rec.reference, "detail": rec.detail})
        raise NotFound()

    @extend_schema(responses=dict)
    def get(self, request, store_id, provider, op):
        store = _store(request, store_id)
        si = _integration(store, provider)
        if si.pk is None:
            return Response({"logs": [], "events": [], "sync_records": []})
        if op != "activity":
            raise NotFound()
        logs = [{"id": l.id, "at": l.created_at, "level": l.level, "direction": l.direction,
                 "operation": l.operation, "message": l.message, "http_status": l.http_status,
                 "order_id": str(l.order_id) if l.order_id else None} for l in si.logs.all()[:100]]
        events = [{"id": e.id, "event_id": e.event_id, "type": e.event_type, "status": e.status,
                   "attempts": e.attempts, "delivery_count": e.delivery_count, "error": e.last_error,
                   "order_id": str(e.order_id) if e.order_id else None, "received_at": e.received_at}
                  for e in si.webhook_events.all()[:100]]
        syncs = [{"id": r.id, "kind": r.kind, "status": r.status, "reference": r.reference, "detail": r.detail,
                  "order_id": str(r.order_id) if r.order_id else None, "at": r.created_at}
                 for r in SyncRecord.objects.filter(store_integration=si)[:100]]
        return Response({"logs": logs, "events": events, "sync_records": syncs})


class WebhookEventViewSet(ContextMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsStaffUser]

    class Ser(serializers.ModelSerializer):
        provider = serializers.CharField(source="provider.code")

        class Meta:
            model = WebhookEvent
            fields = ["id", "provider", "event_id", "event_type", "external_order_id", "store_integration", "order",
                      "status", "attempts", "delivery_count", "last_error", "next_retry_at", "received_at",
                      "processed_at"]

    serializer_class = Ser

    def get_queryset(self):
        qs = WebhookEvent.objects.select_related("provider")
        u = self.request.user
        if not is_hq(u):
            ids = accessible_franchisee_ids(u) or set()
            qs = qs.filter(store_integration__store__franchisee_id__in=ids)
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        if not request.user.has_permission("integrations.configure_store"):
            raise PermissionDenied()
        ev = self.get_object()
        if ev.status not in (WebhookEvent.STATUS_FAILED,):
            raise ValidationError("Only failed events can be retried")
        from .services import process_webhook_event

        process_webhook_event(ev)
        ev.refresh_from_db()
        audit("webhook_event.retry", ev, entity_type="WebhookEvent")
        return Response({"status": ev.status, "error": ev.last_error})


class ProductMappingViewSet(ContextMixin, viewsets.ModelViewSet):
    permission_classes = [IsStaffUser]

    class Ser(serializers.ModelSerializer):
        provider = serializers.SlugRelatedField(slug_field="code", queryset=IntegrationProvider.objects.all())

        class Meta:
            model = ProductMapping
            fields = ["id", "provider", "store", "external_id", "target"]

    serializer_class = Ser

    def get_queryset(self):
        u = self.request.user
        qs = ProductMapping.objects.select_related("provider")
        if is_hq(u):
            return qs
        ids = accessible_franchisee_ids(u) or set()
        return qs.filter(store__franchisee_id__in=ids)

    def perform_create(self, serializer):
        store = serializer.validated_data.get("store")
        u = self.request.user
        if store is None and not is_hq(u):
            raise PermissionDenied("Only headquarters can create network-wide mappings")
        if store is not None and not (can_access_store(u, store) and u.has_permission("integrations.configure_store")):
            raise PermissionDenied()
        audit("product_mapping.create", serializer.save(), store=store)
