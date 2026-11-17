# Architecture

A **modular monolith**: one Django 5.2 project (`backend/`) split into bounded apps, one Next.js
application (`frontend/`) serving four separate interfaces, PostgreSQL and Redis on EC2.

```
                    ┌──────────────── Nginx (TLS) ────────────────┐
 Browsers/iPads ───▶│  /            → Next.js  (HQ, Franchise, POS PWA, KDS)
 Call centre   ───▶│  /api/v1/     → Django REST (Daphne ASGI)
 Uber/DoorDash ───▶│  /api/v1/integrations/<p>/webhooks/
                    │  /ws/         → Django Channels (POS + KDS live feeds)
                    └─────────────────────────────────────────────┘
          Celery worker (integrations queue + default) ◀─▶ Redis ◀─▶ Channels layer
                                   │
                              PostgreSQL 16 (on EC2, never RDS)
```

## Backend apps

| App | Responsibility |
|---|---|
| `core` | Audit log (immutable), Fernet field encryption, tenancy scoping, request context, error format, log redaction, throttles |
| `organization` | Headquarters → Franchisee → Store, hours/special hours, channels, delivery/pickup/payment config, devices, per-day order sequences |
| `accounts` | User, Role, Permission (DB-backed RBAC), store assignments, PINs, manager codes, JWT + device authentication, manager authorization |
| `catalog` | Master catalogue (products, sizes, crusts, modifier groups/modifiers, defaults, taxes, prep categories, charges, discounts), store menu config with HQ price ranges, product requests, version history, **customization & pricing engine** |
| `orders` | Normalized order format, **central intake engine**, order state machine, events, receipts, offline sync |
| `payments` | Payment/Refund/CashSession/CashMovement, processor adapters (cash, Stripe Terminal, online reference), payment state machine |
| `kitchen` | Stations, tickets, routing by prep category, bump flow, WebSocket consumers |
| `integrations` | Providers, provider accounts (encrypted credentials), store integrations, webhook events, logs, sync records, mappings, **provider adapters** |
| `api` | External API clients (API keys, scopes, store restrictions), signed outbound status webhooks |
| `reporting` | Permission-scoped sales/channel/payment/product/employee/store reports, audit viewer |
| `promotions` | Promotion engine (types, codes, schedules, limits, stacking), redemptions, franchise policies, reporting |
| `dispatch` | Delivery zones/fees, delivery lifecycle, drivers, manual/automatic assignment, proof of delivery |
| `devices` | Device credentials, registration/approval/enrollment, heartbeats & alerts, configuration profiles |

The cash ledger, reconciliation and shift closing live in `payments/cash.py`. See `docs/MODULES.md` for the four
operations modules.

## Central order intake

Every channel builds a `NormalizedOrder` (`apps/orders/intake.py`) and calls
`OrderIntakeService.submit()`:

1. **Duplicate detection / idempotency.** Look up the provider order id `(source, external_order_id)` or
   `(store, source, Idempotency-Key)`. A reused key with a different body gives 409. Both are also
   **database unique constraints**, so concurrent retries cannot create duplicates: the
   `IntegrityError` is caught and the winning order is returned.
2. **Store routing.** Store id/number. API clients are checked against their store scope. Inactive stores are refused.
3. **Channel checks.** The ordering channel is enabled, delivery/pickup are offered, and the order has items.
4. **Product & modifier mapping.** Internal channels send SKUs and codes. Provider adapters map
   provider ids through the id scheme and `ProductMapping`. Unknown ids become `unmapped_item`.
5. **Validation & pricing.** `catalog.engine.price_line` validates size, crust, modifier groups
   (min/max, quantity, half placement), removals and topping limits. It checks availability, applies
   store/channel pricing and computes tax per line from regional rates.
6. **Decision.** Internal channels return a synchronous 422 with per-line issues, and **nothing is
   persisted**. Provider orders are persisted with their issues and resolved by the store's
   `AcceptancePolicy`: auto-accept, reject when closed, reject unavailable or unmapped items, or route to staff.
7. **Persistence** in one transaction: order, items, modifiers (snapshotted names and prices), events,
   and a row-locked per-store daily order number.
8. **After commit** (`orders/signals.py`): kitchen tickets, WebSocket broadcast to the POS, provider
   acknowledgement/status sync (Celery) and API-client status webhooks.

Provider orders keep the provider's totals (the provider is merchant of record) in
`provider_totals`. The engine's own price is compared and mismatches beyond tolerance are flagged.
A provider-paid `Payment` row is recorded for settlement reconciliation, and the POS refuses to
charge these orders again.

## State machines

Order: `received → (pending_review) → accepted → preparing → ready → (out_for_delivery) → completed`,
plus `rejected` and `cancelled`. Transitions are row-locked and every change writes an `OrderEvent`
(`apps/orders/state.py`).

Payment (kept separate from order state): `pending → authorized → captured →
partially_refunded → refunded`, plus `failed` and `voided` (`apps/payments/services.py`).
The order's `payment_status` is derived from its settled payments.

Kitchen ticket: `received → preparing → ready → completed` (or `void`). The order status is derived
from all of its tickets (first start → preparing, all ready → ready).

## Tenancy

`apps/core/tenancy.py` is the single source of store/franchisee visibility. HQ sees everything.
Franchise users see their franchisee's stores. Store employees see their assigned stores. API
clients see their franchisee's stores, optionally narrowed to a store list. All querysets that touch franchise data
are filtered through it, and objects outside scope return **404** (not 403) to avoid leaking
existence. POS JWTs carry a `store_id` claim that pins the session to one store.

## Real-time

Django Channels with a Redis channel layer. Groups are `store_<id>_pos` (order events) and
`store_<id>_kds` (ticket events). Browsers authenticate WebSockets with `?token=<JWT>`. The
consumer re-checks the permission and store access, and Origin validation is enforced. Clients
re-poll every 20–30 s as a safety net.

## Offline POS

* The menu and store config are cached in IndexedDB, **encrypted with AES-GCM** using a
  non-extractable per-device WebCrypto key (`frontend/src/lib/offline.ts`). A service worker
  keeps the app shell available.
* When the server is unreachable, orders are queued (encrypted) with a client reference. The UI
  shows them in a separate **"Queued locally — not yet accepted by the central server"** list.
* Background sync posts to `/api/v1/pos/sync/`. The idempotency key `offline:<device>:<ref>` makes
  re-uploads safe. Offline cash is applied against the server-calculated total at sync time, and
  card payments are never taken offline.
* Delivery-provider orders are never acknowledged while offline: provider sync happens only
  centrally.

### Future local store gateway

The design leaves room for a per-store gateway (`Device.TYPE_GATEWAY`):
a small service on the store LAN that holds the encrypted queue for all terminals, relays the
Channels feeds to local KDS screens during WAN outages and forwards to `/pos/sync/`. Terminals
would talk to the gateway with the same API contract. The idempotent sync endpoint and
per-device keys already support this.

## Integrations

See `docs/integrations/README.md`.

## Security

* Secrets come only from environment variables. Production refuses to start without
  `DJANGO_SECRET_KEY` and `FIELD_ENCRYPTION_KEYS`.
* Provider credentials, store payment secrets, Uber token caches and API-client webhook secrets are
  stored with `EncryptedJSONField` (MultiFernet, key rotation). Serializers expose only
  `{configured, last4}` markers, and credential fields are write-only.
* Logs pass through `RedactSecretsFilter`. Integration log excerpts are redacted, and audit `data`
  masks secret-like keys.
* API keys and device tokens are stored as SHA-256 hashes and shown once. PINs and manager codes are
  Django password hashes.
* No raw card data is ever accepted. Card payments use Stripe Terminal (card-present, tokenized on
  the reader), and only the brand/last4/reference are kept. With the **bbpos** processor the tablet app
  charges through the bbpos service, and the server re-verifies each bbpos transaction (status, amount + tip,
  currency, single use) server-to-server before recording it; a unique constraint stops one card transaction
  from paying two orders.
* Rate limits: DRF throttles per user/API client (configurable per client), login and webhook
  throttles, and Nginx `limit_req`.
* Every privileged action writes an immutable `AuditLog`, including the manager who authorized it.
