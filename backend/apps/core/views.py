from .context import get_request_context


class ContextMixin:
    """Mixes into DRF views to publish the authenticated actor to the request context."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        ctx = get_request_context()
        client = getattr(request, "api_client", None)
        if client is not None:
            ctx.api_client_label = f"api_client:{client.name}"
        elif request.user and request.user.is_authenticated:
            ctx.user = request.user
