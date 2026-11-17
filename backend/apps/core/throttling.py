from rest_framework.throttling import SimpleRateThrottle


class UserOrClientRateThrottle(SimpleRateThrottle):
    """Rate limit per authenticated user or per external API client."""

    scope = "user_or_client"

    def get_cache_key(self, request, view):
        client = getattr(request, "api_client", None)
        if client is not None:
            ident = f"client:{client.pk}"
            if client.rate_limit_per_minute:
                self.rate = f"{client.rate_limit_per_minute}/min"
                self.num_requests, self.duration = self.parse_rate(self.rate)
        elif request.user and request.user.is_authenticated:
            ident = f"user:{request.user.pk}"
        else:
            return None
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LoginRateThrottle(SimpleRateThrottle):
    scope = "login"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class WebhookRateThrottle(SimpleRateThrottle):
    scope = "webhook"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
