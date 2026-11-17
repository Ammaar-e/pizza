# Third-party delivery integrations

All three providers feed the same central intake engine (`backend/apps/orders/intake.py`).
Each provider has an adapter (`backend/apps/integrations/adapters/`) that only translates formats
and calls the provider API. Validation, pricing, acceptance decisions, persistence, kitchen routing
and status sync are shared.

| | Uber Eats | DoorDash | SkipTheDishes |
|---|---|---|---|
| Public order-ingestion docs | Yes (developer.uber.com/docs/eats) | Yes (developer.doordash.com, Marketplace) | **No** |
| Access model | App approval + scope whitelisting by Uber Eats | Limited access; quarterly application review; certification | Partnership manager only |
| Auth to provider | OAuth2 client credentials | JWT HS256 (`DD-JWT-V1`) | n/a |
| Webhook auth | `X-Uber-Signature` HMAC-SHA256 | Auth token configured on the subscription | n/a |
| Acknowledgement | accept/deny within 11.5 min | confirm within 3–8 min (async, 202) | n/a |
| Implemented in this build | webhook, signature, token, fetch, accept, deny, cancel-webhook, store lookup | everything listed in `doordash.md` | configuration only; webhook returns 501 |
| Needs sandbox verification | order payload schema, cancel body, menu upload body | webhook auth header name | everything |

Order flow (all providers):

1. Provider POSTs to `/api/v1/integrations/<provider>/webhooks/[<account_key>/]`.
2. The request is authenticated (signature/token) **before** anything is stored. Bad auth → 401.
3. A `WebhookEvent` row is inserted with a unique `(provider, event_id)` constraint, then the
   endpoint answers immediately (Uber 200 empty body, DoorDash 202). Redeliveries hit the
   constraint, increment `delivery_count`, and never create a second order.
4. A Celery task resolves the store connection, fetches/normalizes the order, and submits it to the
   intake engine with the store's acceptance policy.
5. The order is accepted, rejected (with a provider reason code) or routed to staff review. The
   resulting status change triggers the provider acknowledgement (accept/confirm/deny) — the
   acknowledgement never waits for kitchen staff.
6. Orders in staff review are resolved automatically 60 s before the provider deadline according
   to `on_review_timeout` (default: reject with reason) so they never silently time out.
7. Kitchen "ready" triggers the DoorDash Order Ready Signal. Store cancellations call the
   provider's merchant-cancel API where supported. Otherwise the order is flagged so staff can
   cancel in the provider's tools.
8. Every outbound call is logged in `IntegrationLog` (secrets redacted) and every sync attempt is
   recorded as a `SyncRecord`. Failures surface on the POS order and in the franchise portal.

Menu ids: menus published by this platform use our own identifiers as provider ids
(`<SKU>`, `SIZE:<code>`, `CRUST:<code>`, `MOD:<code>`, `PLACE:<half>`, `NO:<code>`) — see
`backend/apps/integrations/idscheme.py`. Items with ids we did not publish can be mapped in
`ProductMapping`; anything still unmapped is flagged, never guessed.

See `ubereats.md`, `doordash.md` and `skipthedishes.md` for the research notes, endpoint lists and
outstanding access requirements.
