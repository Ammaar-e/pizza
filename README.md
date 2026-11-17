# Pizza Network — Franchise POS, Order Management & Integration Platform

An internal, centralized restaurant operations platform for a pizza franchise network:
headquarters administration, franchisee and multi-store management, a touch POS (PWA with offline
queue), a real-time Kitchen Display System, a central order intake engine, delivery-provider
integrations, a versioned external/call-centre API, payments, reporting and a full audit trail —
plus cash management & shift closing, a promotions engine, in-house delivery dispatch with a driver
app, and centralized device management (see [`docs/MODULES.md`](docs/MODULES.md)).
It is **not** a customer-facing ordering site.

| Interface | Path | Who |
|---|---|---|
| Headquarters | `/hq` | HQ admins/staff |
| Franchise portal | `/franchise` | Franchisee owners/managers |
| Restaurant POS | `/pos` | Store managers, supervisors, cashiers, drivers |
| Kitchen display | `/kds` | Kitchen staff |
| Driver app (mobile) | `/driver` | Delivery drivers |
| API docs | `/api/v1/docs/` | Integrators |
| **POS tablet/phone app** (Android + iOS) | [`mobile/`](mobile/README.md) | Store staff — card payments on a BBPOS WisePad 3 via **bbpos** |

## Stack

Python 3.13 · Django 5.2 LTS · DRF · Channels · Celery · PostgreSQL 16 · Redis · Next.js 15 / React 19 /
TypeScript / Tailwind · Nginx · Docker Compose on Ubuntu EC2 (database on EC2, never RDS).

## Repository layout

```
backend/            Django modular monolith (apps: core, organization, accounts, catalog, orders,
                    payments, kitchen, integrations, api, reporting, promotions, dispatch, devices) + tests
frontend/           Next.js app: HQ & franchise portals, POS PWA, KDS
mobile/             Expo / React Native POS app for Android & iOS tablets and phones (bbpos card payments)
deploy/             docker-compose (single EC2 / two EC2), nginx, postgres config, backup & restore scripts
docs/               ARCHITECTURE, API, DEPLOYMENT, integrations/ (Uber Eats, DoorDash, Skip research)
```

## Local development

Requires PostgreSQL 16 and Redis running locally.

```bash
createuser -d pizza -P          # password: pizza (development only)
createdb -O pizza pizza
cd backend
python -m venv ../.venv && . ../.venv/bin/activate && pip install -r requirements-dev.txt
export DJANGO_DEBUG=1 CELERY_TASK_ALWAYS_EAGER=1
python manage.py migrate
python manage.py seed_demo --password 'Demo!Pizza2026'
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

```bash
cd frontend && npm install
NEXT_PUBLIC_WS_HOST=127.0.0.1:8000 npm run dev      # http://localhost:3000 (proxies /api to :8000)
```

To run a real Celery worker instead of eager mode: `celery -A config worker -Q default,integrations -l INFO`
and `celery -A config beat`.

### Demo organisation (`seed_demo`)

Headquarters → Franchisee **A** (stores 1001, 1002), **B** (2001, 2002), **C** (3001), with a pizza
menu (sizes, crusts, toppings with half-and-half, sauces, dips), Ontario/Quebec taxes and kitchen
stations (oven, fryer, expo).

| User | Role | Notes |
|---|---|---|
| `hq.admin` | HQ administrator | portal |
| `owner.a` / `owner.b` / `owner.c` | Franchisee owner | portal; manager code `9999` |
| `manager.<store>` | Store manager | POS; PIN `1111`, manager code `1234` |
| `cashier.<store>` | Cashier | POS; PIN `2222` |
| `kitchen.<store>` | Kitchen | KDS; PIN `3333` |
| `driver.<store>` | Delivery driver | PIN `4444`; driver app at `/driver` |

All demo users share the password given to `seed_demo`. Demo data is for demonstrations only.

The seed also creates promotions (codes `STAFF10` (manager approval), `PROMO5`, `TOPPINGS2`; automatic
"free garlic bread with a large pizza" and "wings & cola $14.99" deals; a scheduled weekend BOGO),
franchise promotion policies, driver profiles, delivery zones for store 1002, auto-dispatch on store 1001,
and device configuration profiles. Devices registered by store managers need approval by the
franchise owner or HQ (portal → **Devices**), or enroll with a one-time code.

## Tests

```bash
cd backend && python -m pytest          # 88 tests
cd frontend && npx tsc --noEmit && npm run build
cd mobile && npm run typecheck && npm test
```

The tests cover order processing and pricing (half-and-half, extras, removals, taxes), validation,
idempotency keys, **duplicate webhook handling** (redelivery, concurrent races, events),
DoorDash/Uber webhook authentication and acknowledgement calls, acceptance policies and the
provider-deadline sweeper, **tenant isolation** (franchisees, store employees, API clients,
secrets), **payment state transitions** (cash sessions, split payments, partial/full refunds with
manager authorization, provider-paid orders), offline sync idempotency, the KDS flow and OpenAPI generation;
and for the operations modules: separate registers per employee, shortage/overage reconciliation with
explanations and approvals, immutable closed sessions and post-close corrections, transfers, duplicate
requests; overlapping, expired, stacked, limited and customer-specific promotions with tax recalculation
and franchise isolation; zone/fee validation, assignment/reassignment, capacity, auto-assignment, failed
deliveries, proof of delivery and driver-only access; device approval, enrollment codes, revoked/rotated
credentials, auth-failure alerts, server-side heartbeat timeouts, outdated versions and config versioning.

## Integrations status

* **DoorDash:** implemented from the official Marketplace docs (JWT auth, orders webhook, async
  confirmation, ready signal, merchant cancel, cancellation webhook, menu push and status callback).
  Needs DoorDash partner approval and certification.
* **Uber Eats:** webhook signature, OAuth, order fetch, accept/deny, cancellation events. Needs
  app approval and scope whitelisting. The order payload schema, cancel body and menu upload must
  be verified in the Uber sandbox before go-live.
* **SkipTheDishes:** no public order-ingestion API exists. Configuration is captured, but the adapter
  refuses operations until Skip provides partner access. Nothing is simulated.

Details: [`docs/integrations/`](docs/integrations/README.md).

## Card payments with bbpos (BBPOS WisePad 3)

Stores can use the companion **bbpos** service (Stripe Terminal) as their card processor (portal → Stores →
Pickup & payments → Card processor = bbpos). The [mobile app](mobile/README.md) charges the exact order total
on the WisePad 3 through bbpos, and this server **verifies every bbpos transaction server-to-server** (status,
amount + tip, currency, not reused on another order) before marking the order paid. Refunds are sent to bbpos;
Interac refunds stay pending until processed with the card present. See `backend/apps/payments/processors.py`
(`BBPOSProcessor`).

## Deployment

API down / 502 errors: run `sh deploy/scripts/diagnose.sh` on the server and see
[`deploy/TROUBLESHOOTING.md`](deploy/TROUBLESHOOTING.md).

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md): single EC2 (`deploy/docker-compose.yml`) or two
EC2 instances (`docker-compose.app.yml` + `docker-compose.db.yml`), TLS, private networking,
automated and tested backups.
