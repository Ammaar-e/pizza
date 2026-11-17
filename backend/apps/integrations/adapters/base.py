from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from apps.core.logging import redact

logger = logging.getLogger("integrations")


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class ProviderNotConfigured(ProviderError):
    def __init__(self, message: str):
        super().__init__(message, retryable=False)


class ProviderUnsupported(ProviderError):
    def __init__(self, message: str):
        super().__init__(message, retryable=False)


class WebhookAuthError(Exception):
    pass


@dataclass
class ParsedEvent:
    event_id: str
    event_type: str
    kind: str                    # order_new | order_cancelled | menu_status | ignored
    external_order_id: str = ""
    external_store_id: str = ""
    data: dict | None = None


class ProviderHttp:
    """requests wrapper that writes every call to IntegrationLog (secrets redacted)."""

    def __init__(self, provider, store_integration=None, order=None):
        self.provider = provider
        self.si = store_integration
        self.order = order

    def request(self, method: str, url: str, *, operation: str, headers=None, json_body=None, data=None,
                auth=None, expected=(200, 201, 202, 204)) -> requests.Response:
        from apps.integrations.models import IntegrationLog

        started = time.monotonic()
        try:
            resp = requests.request(method, url, headers=headers, json=json_body, data=data, auth=auth,
                                    timeout=settings.PROVIDER_HTTP_TIMEOUT)
        except requests.RequestException as exc:
            IntegrationLog.objects.create(
                provider=self.provider, store_integration=self.si, order=self.order, level="error",
                direction="outbound", operation=operation, message=f"Network error: {exc.__class__.__name__}",
                http_method=method, url=url[:500], duration_ms=int((time.monotonic() - started) * 1000))
            raise ProviderError(f"Network error calling {self.provider.name}: {exc.__class__.__name__}") from exc
        ok = resp.status_code in expected
        IntegrationLog.objects.create(
            provider=self.provider, store_integration=self.si, order=self.order, level="info" if ok else "error",
            direction="outbound", operation=operation,
            message=f"{method} {operation} -> {resp.status_code}", http_method=method, url=url[:500],
            http_status=resp.status_code, duration_ms=int((time.monotonic() - started) * 1000),
            response_excerpt=redact(resp.text[:2000]) if resp.text else "")
        if not ok:
            retryable = resp.status_code >= 500 or resp.status_code == 429
            raise ProviderError(f"{self.provider.name} {operation} failed: HTTP {resp.status_code} "
                                f"{redact(resp.text[:300])}", status=resp.status_code, retryable=retryable)
        return resp


class ProviderAdapter:
    """Translate between a provider's API and the platform's normalized order format.

    Adapters contain no order business logic: validation, pricing, acceptance
    decisions and persistence all happen in the central intake engine.
    """

    code: str = ""
    name: str = ""
    available: bool = True
    account_secret_fields: tuple[str, ...] = ()
    account_fields: tuple[str, ...] = ()
    store_fields: tuple[str, ...] = ("external_store_id",)
    ack_deadline_seconds: int = 180
    webhook_success_status: int = 200
    supports_ready_signal: bool = False
    supports_merchant_cancel: bool = False
    supports_menu_push: bool = False

    def __init__(self, provider):
        self.provider = provider

    # ---- webhooks -------------------------------------------------------------
    def verify_webhook(self, request, raw_body: bytes, account) -> None:
        raise NotImplementedError

    def parse_events(self, payload: dict) -> list[ParsedEvent]:
        raise NotImplementedError

    # ---- orders ---------------------------------------------------------------
    def fetch_order(self, si, event) -> dict:
        return event.payload

    def normalize(self, si, raw: dict):
        raise NotImplementedError

    def accept(self, si, order) -> str:
        raise NotImplementedError

    def reject(self, si, order, reason_code: str, message: str) -> str:
        raise NotImplementedError

    def cancel(self, si, order, reason_code: str, message: str) -> str:
        raise ProviderUnsupported(f"{self.name} does not support merchant-initiated cancellation via API")

    def mark_ready(self, si, order) -> str:
        raise ProviderUnsupported(f"{self.name} does not support an order-ready signal via API")

    # ---- menus / connection ----------------------------------------------------
    def push_menu(self, si) -> str:
        raise ProviderUnsupported(f"Menu push is not supported for {self.name}")

    def test_connection(self, si) -> str:
        raise NotImplementedError

    # ---- helpers ---------------------------------------------------------------
    def http(self, si=None, order=None) -> ProviderHttp:
        return ProviderHttp(self.provider, si, order)

    def account_credentials(self, si) -> dict:
        if si.account is None or not si.account.is_active:
            raise ProviderNotConfigured(f"No active {self.name} provider account linked to store {si.store.number}")
        creds = dict(si.account.credentials or {})
        creds.update({k: v for k, v in (si.credentials or {}).items() if v})
        missing = [f for f in self.account_secret_fields + self.account_fields if not creds.get(f)]
        if missing:
            raise ProviderNotConfigured(f"{self.name} credentials incomplete: missing {', '.join(missing)}")
        return creds

    def mappings(self, si) -> dict[str, str]:
        from apps.integrations.models import ProductMapping

        rows = ProductMapping.objects.filter(provider=self.provider).filter(
            store__isnull=True) | ProductMapping.objects.filter(provider=self.provider, store=si.store)
        return {r.external_id: r.target for r in rows}

    def known_skus(self) -> set[str]:
        from apps.catalog.models import Product

        return set(Product.objects.values_list("sku", flat=True))

    def deadline(self):
        return timezone.now() + timedelta(seconds=self.ack_deadline_seconds)


def safe_json(resp) -> dict:
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError):
        return {}
