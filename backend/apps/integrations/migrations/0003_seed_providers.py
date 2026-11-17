from django.db import migrations

PROVIDERS = [
    {
        "code": "ubereats", "name": "Uber Eats", "api_available": True, "ack_deadline_seconds": 690,
        "documentation_url": "https://developer.uber.com/docs/eats",
        "access_requirements": (
            "Uber Eats Marketplace APIs require an approved Uber developer app. Production scopes "
            "(eats.store, eats.order, eats.store.orders.read) must be approved and whitelisted by the Uber Eats "
            "team; develop against the sandbox (test-api.uber.com). Stores are linked to the app by Uber's "
            "store provisioning flow; record the Uber store UUID per store. Configure the app's primary webhook "
            "URL to the account webhook URL shown here."),
        "capabilities": {"webhook_signature": "X-Uber-Signature HMAC-SHA256", "ack": "accept_pos_order / "
                         "deny_pos_order within 11.5 min"},
    },
    {
        "code": "doordash", "name": "DoorDash", "api_available": True, "ack_deadline_seconds": 180,
        "documentation_url": "https://developer.doordash.com/en-US/docs/marketplace/overview/about_marketplace",
        "access_requirements": (
            "DoorDash Marketplace APIs are limited-access: apply via the DoorDash Developer Portal (Integrations "
            "tab); applications are reviewed quarterly. After building in sandbox, certification (screen "
            "recordings of required menu, availability and order features) is required for production. Create "
            "JWT credentials (developer_id, key_id, signing_secret) in the portal and configure Orders, Menu "
            "Status, Menu Request and Order Canceled webhook subscriptions with an auth token. Merchant-initiated "
            "cancellation requires allow-listing by your DoorDash technical account manager."),
        "capabilities": {"auth": "JWT HS256 DD-JWT-V1", "ack": "PATCH /api/v1/orders/{id} within 3-8 min"},
    },
    {
        "code": "skip", "name": "SkipTheDishes", "api_available": False, "ack_deadline_seconds": 180,
        "documentation_url": "https://partners.skipthedishes.com/",
        "access_requirements": (
            "No public restaurant order-ingestion API specification exists. Skip's public OpenAPI (Skip the "
            "Dishes API 1.0.0) covers Delivery-as-a-Service courier endpoints only. A POS order integration must "
            "be arranged through a SkipTheDishes partnership manager, who provides the partner specification "
            "and credentials. Until then this connection cannot receive or acknowledge orders."),
        "capabilities": {},
    },
]


def seed(apps, schema_editor):
    P = apps.get_model("integrations", "IntegrationProvider")
    for p in PROVIDERS:
        P.objects.update_or_create(code=p["code"], defaults=p)


class Migration(migrations.Migration):
    dependencies = [("integrations", "0002_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
