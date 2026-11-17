import uuid

from .context import RequestContext, reset_request_context, set_request_context


def client_ip(request) -> str | None:
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class RequestContextMiddleware:
    """Populates the request context. DRF authenticates lazily, so views/auth
    classes update ``ctx.user`` after authentication (see core.views mixin)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.META.get("HTTP_X_REQUEST_ID") or uuid.uuid4().hex
        ctx = RequestContext(user=None, ip=client_ip(request), request_id=rid)
        token = set_request_context(ctx)
        request.request_id = rid
        try:
            response = self.get_response(request)
        finally:
            reset_request_context(token)
        response["X-Request-ID"] = rid
        return response
