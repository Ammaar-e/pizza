# API guide (v1)

Interactive OpenAPI documentation: **`/api/v1/docs/`** (Swagger UI), **`/api/v1/redoc/`**, raw schema
**`/api/v1/schema/`**. All paths are versioned under `/api/v1/` and end with `/`.

## Authentication

| Client | Header |
|---|---|
| External application (call centre, website back-end, mobile back-end) | `Authorization: Api-Key <prefix>.<secret>` |
| Staff (portals) | `Authorization: Bearer <JWT>` from `POST /auth/login/` |
| POS / KDS | `Authorization: Bearer <JWT>` from `POST /auth/pos/login/` (+ `X-Device-Token` on registered devices) |
| Delivery providers | Provider-specific signature/token on `/integrations/<provider>/webhooks/` |

API keys are created in the HQ or franchise portal (**API clients**) with scopes:
`stores:read`, `menu:read`, `orders:write`, `orders:read`, `orders:cancel`. Keys can be restricted to
specific stores and given their own rate limit. The key is shown once and stored hashed.

## Conventions

* Money is in **integer cents**. Times are ISO-8601 UTC.
* `POST /orders/` from API clients **requires an `Idempotency-Key` header**. Repeating a request with
  the same key returns the original order (`200`, `intake.decision = "duplicate"`). Reusing a key
  with a different body returns `409 idempotency_key_reused`.
* Errors share one shape:
  ```json
  {"error": {"code": "order_invalid", "message": "Order validation failed",
             "details": null, "errors": [{"code": "invalid_size", "message": "...", "ref": "XXL", "line": 0}],
             "request_id": "…"}}
  ```
* Pagination: `?page=` and `?page_size=` (max 200). Responses have `count`, `next`, `previous` and `results`.
* Rate limiting returns `429` with `Retry-After`.

## Call-centre flow

```http
GET  /api/v1/stores/                          # identify the store (number, address, hours, is_open_now)
GET  /api/v1/stores/{id}/menu/                # categories → products → sizes, crusts, defaults, modifier groups, prices
GET  /api/v1/stores/{id}/availability/        # 86'd products/modifiers, open/closed, delivery/pickup enabled
POST /api/v1/stores/{id}/quote/               # price & validate lines without creating an order
POST /api/v1/orders/                          # submit (Idempotency-Key required)
GET  /api/v1/orders/{id}/status/              # poll status
POST /api/v1/orders/{id}/cancel/              # scope orders:cancel; only before preparation starts
```

Order submission:

```json
POST /api/v1/orders/
Idempotency-Key: cc-2026-11-17-000123
{
  "store_number": "1001",
  "order_type": "delivery",
  "items": [
    {"product": "PZ-PEP", "size": "L", "crust": "THIN", "quantity": 1,
     "modifiers": [{"code": "TOP-MUSH", "placement": "left", "quantity": 1},
                   {"code": "TOP-PEP", "quantity": 2}],
     "removed": ["SAU-TOM"], "special_instructions": "well done"}
  ],
  "customer": {"name": "Pat", "phone": "+15551234567"},
  "delivery_address": {"line1": "1 Main St", "city": "Toronto", "postal_code": "M1M 1M1"},
  "expected_total_cents": 2681,
  "payment": {"status": "unpaid"}
}
```

* `placement`: `whole | left | right`. Half placement needs a half-and-half product and modifier group.
* `quantity: 2` on a modifier means **extra**. For a default ingredient only the extra unit is charged.
* `removed`: default ingredients to leave off.
* `expected_total_cents` (optional) must equal the server total, otherwise `422 price_mismatch` and
  nothing is saved. Use the quote endpoint to get it.
* `payment.status = "prepaid"` with a gateway `reference` records an online payment. Card data is never accepted.

Response `201` returns the order with `status` (`accepted` for valid internal orders), totals, tax,
items and `intake: {decision, created}`.

## Status webhooks to API clients

If the client has a `webhook_url`, every status change is POSTed:

```http
POST <webhook_url>
X-Pizza-Event-Id: 3f2a…
X-Pizza-Timestamp: 1794883000
X-Pizza-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>." + body)>
{"id":"3f2a…","type":"order.status_changed","created_at":"…",
 "data":{"order_id":"…","order_number":12,"store_number":"1001","previous_status":"accepted","status":"ready",
         "rejection_reason":null,"cancellation_reason":null}}
```

Verify the signature with the webhook signing secret shown when the client was created, reject
stale timestamps, and dedupe on `X-Pizza-Event-Id`. Failed deliveries retry with exponential backoff
(8 attempts). Delivery history is at `GET /api-clients/{id}/deliveries/`.

## Endpoint catalogue

**Auth:** `auth/login/`, `auth/refresh/`, `auth/pos/login/`, `auth/me/`, `auth/manager-authorize/`

**Organisation:** `headquarters/`, `network/`, `franchisees/`, `stores/` (+ `{id}/menu/`,
`{id}/availability/`, `{id}/payment-secrets/`), `stores/{id}/orders/`, `devices/`, `users/`, `roles/`

**Catalogue:** `products/` (+ `{id}/versions/`), `categories/`, `crusts/`, `modifier-groups/`,
`modifiers/`, `tax-categories/`, `tax-rates/`, `prep-categories/`, `charges/`, `discounts/`,
`product-requests/` (+ `approve/`, `reject/`), `stores/{id}/menu-config/`,
`stores/{id}/availability/update/`, `stores/{id}/menu-versions/`, `stores/{id}/quote/`

**Orders:** `orders/` (list/create), `orders/{id}/` (GET/PATCH), `orders/{id}/cancel/`,
`orders/{id}/status/` (GET/POST), `orders/{id}/events/`, `orders/{id}/accept/`,
`orders/{id}/reject/`, `orders/{id}/retry-sync/`, `orders/{id}/discounts/`,
`orders/{id}/payments/`, `orders/{id}/receipt/`, `payments/{id}/refund|confirm|reconcile/`,
`pos/sync/`, `cash-sessions/` (+ `current/`, `open/`, `{id}/close/`, `{id}/movements/`)

**Kitchen:** `kitchen/stations/`, `kitchen/tickets/` (+ `{id}/bump/`). WebSockets:
`/ws/stores/{id}/pos/`, `/ws/stores/{id}/kds/`

**Integrations:** `integrations/{provider}/webhooks/[<account_key>/]` (public),
`integration-providers/`, `provider-accounts/`, `stores/{id}/integrations/`,
`stores/{id}/integrations/{provider}/` (PUT), `…/{provider}/test/`, `…/{provider}/menu-sync/`,
`…/{provider}/activity/`, `webhook-events/` (+ `retry/`), `integration-mappings/`

**External clients & reporting:** `api-clients/` (+ `rotate-key/`, `deliveries/`),
`reports/summary/`, `audit-logs/`
