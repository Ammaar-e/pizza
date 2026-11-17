"""SkipTheDishes adapter — NOT AVAILABLE (no public order-ingestion API specification).

Research performed 2026-11-17:
* SkipTheDishes' only public OpenAPI specification (``Skip the Dishes API 1.0.0`` hosted at
  uk.api.just-eat.io/docs/skip/) covers **Delivery as a Service** courier endpoints only
  (``POST /v1/delivery``, ``/v1/delivery/estimate``, notification-config, cancellation-request),
  authenticated by a partner-issued API key / DaaS JWT. It does not expose restaurant
  marketplace order ingestion, acceptance, menu or store-status endpoints.
* Skip's partner site states restaurants can build a POS integration to Skip's APIs, with
  integrations arranged through a Skip partnership manager. That specification is not public.
* Skip orders are also available through Skip-approved middleware partners.

Accordingly this adapter refuses every operation with an actionable "access required" error
instead of fabricating endpoints or payloads. Store-level configuration is still captured so the
connection can be completed once Skip issues the partner specification and credentials.
"""
from __future__ import annotations

from .base import ProviderAdapter, ProviderNotConfigured, WebhookAuthError

ACCESS_MESSAGE = ("SkipTheDishes restaurant order-ingestion API access has not been granted: the partner "
                  "specification and credentials must be obtained through a SkipTheDishes partnership manager. "
                  "No orders can be received or acknowledged through this connection yet.")


class SkipAdapter(ProviderAdapter):
    code = "skip"
    name = "SkipTheDishes"
    available = False

    def verify_webhook(self, request, raw_body, account):
        raise WebhookAuthError(ACCESS_MESSAGE)

    def parse_events(self, payload):
        raise ProviderNotConfigured(ACCESS_MESSAGE)

    def normalize(self, si, raw):
        raise ProviderNotConfigured(ACCESS_MESSAGE)

    def accept(self, si, order):
        raise ProviderNotConfigured(ACCESS_MESSAGE)

    def reject(self, si, order, reason_code, message):
        raise ProviderNotConfigured(ACCESS_MESSAGE)

    def test_connection(self, si):
        raise ProviderNotConfigured(ACCESS_MESSAGE)
