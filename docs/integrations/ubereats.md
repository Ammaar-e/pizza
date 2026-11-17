# Uber Eats Marketplace integration

Research date: 2026-11-17. `developer.uber.com` pages render client-side and could not be fetched
as text from the build environment. The facts below come from searches restricted to the official
`developer.uber.com` domain (pages: `docs/eats/guides/order-integration`, `docs/eats/guides/webhooks`,
`docs/eats/guides/authentication`, `docs/eats/guides/sandbox`, `docs/eats/guides/menu-integration`,
`docs/eats/references/api/v1/post-eats-order-orderid-acceptposorder`, `…-denyposorder`,
`…-cancel`, `docs/eats/references/api/v1/get-eats-stores-storeid`, `docs/eats/api-change-log`).
**Items marked "verify" must be checked against the reference pages and sandbox before go-live.**

## Access

* Requires an Uber developer app. **Production scopes must be approved and whitelisted by the Uber
  Eats team.** Development happens in the sandbox (`test-api.uber.com`, auth via
  `sandbox-login.uber.com`). Stores are linked to the app via Uber's store provisioning
  (integration activation) flow. Record each store's Uber store UUID in the franchise portal.
* The calling app must be the store's nominated order manager to accept/deny orders.

## Authentication

* OAuth 2.0 **client credentials**: `POST https://auth.uber.com/oauth/v2/token` with
  `client_id`, `client_secret`, `grant_type=client_credentials`, `scope`.
* Scopes: `eats.store` (store/menu), `eats.order` (accept/deny/cancel, v1 order read),
  `eats.store.orders.read` (v2 order read).
* Tokens last 30 days and the token endpoint allows 100 requests/hour, so the platform caches the
  token **encrypted** in `ProviderAccount.token_cache` and refreshes 5 minutes before expiry.

## Webhooks

* All events go to the app's **primary webhook URL** (set in the developer dashboard). Use the
  account URL shown in the franchise portal.
* `X-Uber-Signature` = lowercase hex HMAC-SHA256 of the raw body keyed with the **client secret**.
  Compared case-insensitively in constant time.
* Respond **200 with an empty body** or Uber retries.
* Payload: `event_id` (dedupe key), `event_type`, `event_time`, `meta.user_id` (store id),
  `meta.resource_id` (order id), `resource_href` (full GET URL for the order).
* `orders.notification` = new order. `orders.cancel` (or `orders.failure` for stores on API
  version 1.0.0) = cancellation.
* Security: the platform only fetches `resource_href` when it points at `https://api.uber.com/` or
  `https://test-api.uber.com/` (SSRF protection), with the app token.

## Orders

* Accept: `POST https://api.uber.com/v1/eats/orders/{order_id}/accept_pos_order` (optional
  pickup time and external reference id; we send `external_reference_id` = our order UUID).
* Deny: `POST https://api.uber.com/v1/eats/orders/{order_id}/deny_pos_order` with a reason code
  (documented examples: `STORE_CLOSED`, `POS_OFFLINE`, `ITEM_AVAILABILITY`). **Verify** the exact
  body shape. The adapter sends `{"reason":{"explanation":…, "code":…}}`.
* Deadline: accept or deny **within 11.5 minutes** of the webhook or the order auto-cancels.
* **Verify:** the full Get Order Details response schema. The parser
  (`UberEatsAdapter.normalize`) reads `cart.items` / `carts[].items`, item and option ids from
  `external_data` (falling back to `external_id`/`id`), nested `selected_modifier_groups[].selected_items[]`,
  quantities, and money given as `{amount}` or `{amount_e5}`. Anything it cannot recognize raises a
  non-retryable, actionable error. The order is **not** accepted, and the failure appears in the
  integration error history.

## Disabled until verified

* Merchant cancel `POST /v1/eats/orders/{order_id}/cancel`: the endpoint is documented, but the
  2025-08-21 changelog moved the reason into a nested object whose exact shape was not
  retrievable. Until verified, store-side cancellations of Uber orders flag the order: "Cancel
  this order in the Uber Eats merchant tools."
* Menu upload (`PUT …/menus`, overwrites the store's menus, `eats.store` scope): the body schema was not
  verified. Maintain the Uber menu with `external_data` set to the platform ids, or add `ProductMapping` rows.
* No Uber "order ready" API was confirmed for marketplace orders, so ready signals are recorded as unsupported.

## Test connection

Obtains a token, then `GET https://api.uber.com/v1/eats/stores/{store_id}`.
