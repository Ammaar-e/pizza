# DoorDash Marketplace integration

Research date: 2026-11-17. Source: DoorDash's official developer documentation, read in full from
`developer.doordash.com/en-US/docs/marketplace/...` (pages listed below).

## Access & certification

* Marketplace APIs are marked **"Limited access — not yet generally available"**. Prospective
  partners apply in the DoorDash Developer Portal (Integrations tab). Applications are reviewed
  **quarterly**. Statuses: Pending Activation → Development → Approved (`overview/getting_started/requesting_access`).
* After building in sandbox, **certification** is needed for production: screen recordings of each
  required feature (menu create/update via push, menu status webhook with failure details, item
  and option 86ing via the activation endpoints, store deactivation, automatic availability
  polling, order confirmation, rejection with a detailed failure reason, merchant tips, order
  cancellation webhooks, and more) (`overview/getting_started/integration_requirements_new`).
* Merchant-initiated cancellation requires allow-listing by your DoorDash technical account
  manager (`how_to/merchant_order_cancellation`).

## Authentication (`overview/getting_started/jwts_getting_started`)

* Credentials are created in the portal: `developer_id`, `key_id`, `signing_secret` (shown once).
* JWT header `{"alg":"HS256","typ":"JWT","dd-ver":"DD-JWT-V1"}`. Claims `aud="doordash"`,
  `iss=<developer_id>`, `kid=<key_id>`, `iat`, `exp ≤ iat+1800`. Signed with HMAC-SHA256.
* Request headers: `Authorization: Bearer <jwt>`, `auth-version: v2`, CamelCase `User-Agent`.
* Implemented in `DoorDashAdapter.make_jwt`. The signing secret is base64url-decoded before use
  (DoorDash's sample code does the same). Tokens are generated per request.

## Webhooks (`overview/getting_started/create_webhook_subscription_getting_started`)

* Required subscriptions: **Orders**, **Menu Status**, **Menu Request**, **Order Canceled**.
  Optional: Order Release, Dasher Status. One endpoint per environment, HTTPS, protected by an
  **auth token** configured with the subscription.
* Configure all subscriptions with the account webhook URL shown in the franchise portal
  (`/api/v1/integrations/doordash/webhooks/<account_key>/`) and the account's `webhook_auth_token`.
* **Verify in sandbox:** the docs say to protect the endpoint with an auth token but do not show the
  header it arrives in. The adapter accepts it in `Authorization` (raw, `Bearer`, `Basic` or
  `Token` prefix). Adjust `DoorDashAdapter.verify_webhook` if sandbox traffic differs.

## Orders (`how_to/order_integration`)

* Payload: `{"event":{"type":"OrderCreate","status":"NEW"},"order":{...}}`. Fields used include
  `id`, `store.merchant_supplied_id`, `categories[].items[]` with `merchant_supplied_id`, `price`
  (cents), `quantity`, `extras[].options[]` (recursively nested via `extra`), `subtotal`, `tax`,
  `merchant_tip_amount`, `is_pickup`, `fulfillment_type`, `delivery_short_code`, `consumer`,
  `is_tax_remitted_by_doordash`, `tax_amount_remitted_by_doordash`, `commission_type`, `experience`.
* **Async confirmation** (used here): respond `202`, then
  `PATCH https://openapi.doordash.com/marketplace/api/v1/orders/{id}` with
  `{merchant_supplied_id, order_status: "success"|"fail", failure_reason, errors[], prep_time?}`
  within the **3–8 minute** SLA. The platform uses 180 s as its deadline.
* Rejections include `errors[]` with `ITEM_OUT_OF_STOCK` + the item's merchant id when the reason
  is availability.
* `prep_time` is sent only when the platform has its own estimate. DoorDash's `estimated_pickup_time` is
  never echoed back, as the docs instruct.

## Status & cancellation

* Order Ready Signal: `PATCH /marketplace/api/v1/orders/{id}/events/order_ready_for_pickup` with
  `{merchant_supplied_id}`. Sent automatically when all kitchen stations bump to ready
  (`how_to/order_ready_signal`). Can be turned off per store.
* Cancellation webhook: `{external_order_id, client_order_id, store:{merchant_supplied_id,...}}`.
  This cancels the order and voids its kitchen tickets, without echoing a cancel back (`how_to/order_cancellation_webhook`).
* Merchant cancel: `PATCH /marketplace/api/v1/orders/{id}/cancellation` with `cancel_reason` ∈
  `ITEM_OUT_OF_STOCK | STORE_CLOSED | KITCHEN_BUSY | OTHER`. Note that `STORE_CLOSED` deactivates
  the store for 12 h and `KITCHEN_BUSY` for 15 min.

## Menus (`how_to/menu_push`, `how_to/menu_status_callback`)

* Create `POST /marketplace/api/v1/menus`. Update `PATCH /marketplace/api/v1/menus/{id}` (full
  menu each time). Required: `store.merchant_supplied_id`, `store.provider_type`, `open_hours`,
  `special_hours`, `menu.name`.
* Results arrive on the Menu Status webhook (`MenuCreate`/`MenuUpdate`, `SUCCESS`/`FAILURE`,
  `reference`, `details`, `menu.id`). The platform stores `menu.id` for later PATCHes and shows
  failure `details` to the franchisee, as certification requires.
* Pizza modelling: sizes, crusts, removals and toppings are `extras`. Half placement is a nested
  `extras` group (`PLACE:left|right|whole`). See `how_to/implement_pizza_menus` for DoorDash's own
  guidance and validate the rendering in the menu preview.

## Store info / test connection

`GET /marketplace/api/v1/stores/{merchant_supplied_id}/store_details` (`faq/get_store_and_menu_details`).
This is used by **Test connection**.

## Not yet implemented (required for certification)

* Menu Request (menu pull) webhook handler.
* Item / item-option activation endpoints (real-time 86ing) and automatic availability polling.
* Store activation status endpoint (`PUT /api/v1/stores/{merchant_supplied_id}/status`).
* Order adjustment, Dasher status, Order Release webhooks (optional).
These fit the existing adapter and `SyncRecord` model. The platform already records 86 changes
(`StoreAvailabilityView`), which is the hook point.
