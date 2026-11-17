"""A fresh production install (migrations + createsuperuser, no demo seed) must be usable from the portal."""
import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.organization.models import Franchisee, Headquarters

pytestmark = pytest.mark.django_db


@pytest.fixture
def superuser_client():
    u = User.objects.create_superuser("admin", "admin@example.com", "Fresh-Install-2026")
    c = APIClient()
    c.force_authenticate(u)
    return c


def test_create_franchisee_without_existing_headquarters(superuser_client):
    Headquarters.objects.all().delete()  # what a fresh install looked like before the data migration
    r = superuser_client.post("/api/v1/franchisees/", {"code": "F100", "name": "First franchisee"}, format="json")
    assert r.status_code == 201, r.content
    assert Franchisee.objects.get(code="F100").headquarters == Headquarters.objects.get()
    assert superuser_client.get("/api/v1/headquarters/").json()["name"] == "Headquarters"


def test_migrations_create_headquarters():
    assert Headquarters.objects.exists()


def test_duplicate_code_is_json_conflict_not_html(superuser_client):
    superuser_client.post("/api/v1/franchisees/", {"code": "F1", "name": "A"}, format="json")
    r = superuser_client.post("/api/v1/franchisees/", {"code": "F1", "name": "B"}, format="json")
    assert r.status_code == 400 and r["Content-Type"].startswith("application/json")


def test_unhandled_errors_return_json(superuser_client, monkeypatch):
    from apps.organization import views

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(views.FranchiseeViewSet, "perform_create", boom)
    c = APIClient(raise_request_exception=False)
    c.force_authenticate(User.objects.get(username="admin"))
    r = c.post("/api/v1/franchisees/", {"code": "F2", "name": "X"}, format="json")
    assert r.status_code == 500 and r.json()["error"]["code"] == "server_error"
