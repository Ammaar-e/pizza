from rest_framework import exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.core.middleware import client_ip


class DeviceAwareJWTAuthentication(JWTAuthentication):
    """Employee JWT authentication that additionally resolves the device the request comes from.

    Device identity is separate from employee identity: ``X-Device-Token`` carries the device's own
    credential (see ``apps.devices``). Revoked, expired, pending or deactivated devices are refused
    even when the employee token is valid. A POS JWT carries a ``store_id`` claim pinning the session
    to one store; the device must belong to that store.
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
            from apps.devices.services import DeviceAuthError, authenticate_device

            try:
                device, _ = authenticate_device(raw, client_ip(request))
            except DeviceAuthError as e:
                raise exceptions.AuthenticationFailed(str(e), code=e.code)
            if request.pos_store_id and device.store_id != request.pos_store_id:
                raise exceptions.AuthenticationFailed("Device is registered to another store",
                                                      code="device_store_mismatch")
            request.device = device
        return user, token
