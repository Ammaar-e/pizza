import os

os.environ.setdefault("DJANGO_TESTING", "1")

import json  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def seeded(db):
    from django.core.management import call_command

    call_command("seed_demo", password="Test!Pass2026", verbosity=0)
    from apps.organization.models import Store

    return {s.number: s for s in Store.objects.all()}


@pytest.fixture
def api():
    from rest_framework.test import APIClient

    return APIClient()


def login(client, username, password="Test!Pass2026", store_number=None, device_token=None):
    if store_number:
        headers = {"HTTP_X_DEVICE_TOKEN": device_token} if device_token else {}
        r = client.post("/api/v1/auth/pos/login/", {"store_number": store_number, "username": username,
                                                    "password": password}, format="json", **headers)
    else:
        r = client.post("/api/v1/auth/login/", {"username": username, "password": password}, format="json")
    assert r.status_code == 200, r.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['tokens']['access']}",
                       **({"HTTP_X_DEVICE_TOKEN": device_token} if device_token else {}))
    return r.json()


@pytest.fixture
def as_user(api):
    def _as(username, **kw):
        login(api, username, **kw)
        return api
    return _as


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body) if self._body else ""
        self.content = self.text.encode()

    def json(self):
        return self._body


@pytest.fixture
def provider_http(monkeypatch):
    """Record outbound provider HTTP calls; tests register responses by (method, url-substring)."""
    calls = []
    routes = []

    def fake_request(method, url, **kw):
        calls.append({"method": method, "url": url, **kw})
        for m, frag, resp in routes:
            if m == method and frag in url:
                return resp() if callable(resp) else resp
        return FakeResponse(202, {})

    import apps.integrations.adapters.base as base

    monkeypatch.setattr(base.requests, "request", fake_request)
    return {"calls": calls, "routes": routes}


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
