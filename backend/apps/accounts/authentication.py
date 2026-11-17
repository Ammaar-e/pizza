from django.utils import timezone
from rest_framework import exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.organization.models import Device


class DeviceAwareJWTAuthentication(JWTAuthentication):
    """JWT auth that also resolves a registered POS/KDS device from X-Device-Token.

    The JWT may carry a ``store_id`` claim (POS session). When present, the
    request is pinned to that store (request.pos_store_id).
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        request.pos_store_id = token.get("store_id")
        request.device = None
        raw = request.META.get("HTTP_X_DEVICE_TOKEN")
        if raw:
            device = Device.objects.filter(token_hash=Device.hash_token(raw), is_active=True).first()
            if device is None:
                raise exceptions.AuthenticationFailed("Unknown or revoked device", code="invalid_device")
            if request.pos_store_id and device.store_id != request.pos_store_id:
                raise exceptions.AuthenticationFailed("Device is registered to another store", code="device_store_mismatch")
            Device.objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
            request.device = device
        return user, token
