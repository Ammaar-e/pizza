# SkipTheDishes integration — access not available

Research date: 2026-11-17.

## Findings

* **Public API spec:** Skip's only public OpenAPI document is "Skip the Dishes API 1.0.0", served
  at `https://uk.api.just-eat.io/docs/skip/openapi.yaml`. It covers **Delivery as a Service**
  (courier) endpoints only. The operations are `POST /v1/delivery`, `POST /v1/delivery/estimate`,
  `PUT /v1/delivery/cancellation-request`, `GET /v1/delivery/status/{requestId}`,
  notification-config CRUD, `GET /v1/delivery/collect-points`, `POST /v1/delivery/request-assistance`
  and a DaaS JWT token endpoint. Its servers are `api.skipthedishes.com` (CA) and regional variants.
  The spec says partner APIs "require an API key that will have been issued to you".
* That spec has **no restaurant marketplace order ingestion**: no order webhook, no
  accept/reject, no menu or store status.
* **Partner programme:** Skip's partner site (`partners.skipthedishes.com`) states restaurants can
  build a POS integration to Skip's APIs, and that integrations are arranged through a **Skip
  partnership manager**. The corresponding specification is not public.
* Skip orders also reach POS systems through Skip-approved middleware partners (e.g. those listed
  on Skip's own blog). This platform does not use that route.

## What the platform does

* `IntegrationProvider(code="skip")` is seeded with `api_available=False` and the access
  requirements above. These are shown to HQ and franchisees.
* Franchisees can record the Skip restaurant id and enable/disable Skip per store (once HQ permits
  it), so configuration is ready when access is granted. Status shows **Awaiting provider access**.
* `SkipAdapter` refuses every operation with an actionable "access required" error. The Skip webhook
  endpoint returns **501** and logs the attempt. **No orders are fabricated and no success is
  reported.**
* Staff can still record a Skip tablet order manually in the POS (`source=skip`,
  `external_order_id` required). It is marked provider-paid and cannot be charged again.

## To complete the integration

1. Contact a SkipTheDishes partnership manager. Request the restaurant/POS integration
   specification, sandbox credentials and certification requirements.
2. Implement `SkipAdapter` from that specification (webhook auth, event parsing, acknowledgement,
   cancellation, menu sync), following `doordash.py` as the template. The intake engine needs no changes.
3. Set `api_available=True` on the provider via a migration once implemented and certified.
