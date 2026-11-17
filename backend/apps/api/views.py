import secrets

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.accounts.permissions import IsStaffUser
from apps.core.audit import audit
from apps.core.tenancy import accessible_franchisee_ids, is_hq
from apps.core.views import ContextMixin
from apps.organization.models import Store

from .models import SCOPES, ApiClient, OutboundWebhook


class ApiClientSerializer(serializers.ModelSerializer):
    stores = serializers.PrimaryKeyRelatedField(many=True, queryset=Store.objects.all(), required=False)
    scopes = serializers.ListField(child=serializers.ChoiceField(choices=list(SCOPES)))
    webhook_secret_configured = serializers.SerializerMethodField()

    class Meta:
        model = ApiClient
        fields = ["id", "name", "kind", "franchisee", "stores", "scopes", "key_prefix", "is_active",
                  "rate_limit_per_minute", "webhook_url", "webhook_secret_configured", "last_used_at", "created_at"]
        read_only_fields = ["key_prefix", "last_used_at", "created_at"]

    def get_webhook_secret_configured(self, obj):
        return bool((obj.webhook_secret or {}).get("secret"))


class ApiClientViewSet(ContextMixin, viewsets.ModelViewSet):
    """External application credentials. The API key and webhook signing secret are shown once."""

    serializer_class = ApiClientSerializer
    permission_classes = [IsStaffUser]
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        ids = accessible_franchisee_ids(self.request.user)
        qs = ApiClient.objects.prefetch_related("stores")
        return qs if ids is None else qs.filter(franchisee_id__in=ids)

    def _check(self, franchisee, stores):
        u = self.request.user
        if not u.has_permission("api.manage_clients"):
            raise PermissionDenied()
        if is_hq(u):
            return
        if franchisee is None or franchisee.id != u.franchisee_id:
            raise PermissionDenied("API clients must belong to your franchisee")
        if any(s.franchisee_id != u.franchisee_id for s in stores or []):
            raise PermissionDenied("Store belongs to another franchisee")

    def create(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        self._check(s.validated_data.get("franchisee"), s.validated_data.get("stores"))
        key, prefix, key_hash = ApiClient.issue_key()
        wh_secret = secrets.token_urlsafe(32)
        client = s.save(key_prefix=prefix, key_hash=key_hash, created_by=request.user,
                        webhook_secret={"secret": wh_secret})
        audit("api_client.create", client, franchisee=client.franchisee, data={"scopes": client.scopes})
        out = ApiClientSerializer(client).data
        out["api_key"] = key
        out["webhook_signing_secret"] = wh_secret
        return Response(out, status=201)

    def perform_update(self, serializer):
        self._check(serializer.validated_data.get("franchisee", serializer.instance.franchisee),
                    serializer.validated_data.get("stores"))
        c = serializer.save()
        audit("api_client.update", c, franchisee=c.franchisee)

    def perform_destroy(self, instance):
        self._check(instance.franchisee, [])
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        audit("api_client.revoke", instance, franchisee=instance.franchisee)

    @action(detail=True, methods=["post"], url_path="rotate-key")
    def rotate_key(self, request, pk=None):
        client = self.get_object()
        self._check(client.franchisee, [])
        key, prefix, key_hash = ApiClient.issue_key()
        client.key_prefix, client.key_hash = prefix, key_hash
        client.save(update_fields=["key_prefix", "key_hash", "updated_at"])
        audit("api_client.rotate_key", client, franchisee=client.franchisee)
        return Response({"api_key": key, "key_prefix": prefix})

    @action(detail=True, methods=["get"])
    def deliveries(self, request, pk=None):
        client = self.get_object()
        return Response([{"event_id": d.event_id, "type": d.event_type, "status": d.status, "attempts": d.attempts,
                          "last_status_code": d.last_status_code, "last_error": d.last_error,
                          "delivered_at": d.delivered_at, "created_at": d.created_at}
                         for d in OutboundWebhook.objects.filter(client=client).order_by("-created_at")[:100]])
