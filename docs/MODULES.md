# POS operations modules

Four modules extend the platform. They reuse the existing tenancy rules (`apps/core/tenancy.py`), RBAC, the immutable
`AuditLog`, the order state machine and the Channels real-time layer. Money is always integer cents.

| Module | Backend | Frontend |
|---|---|---|
| Cash management & shift closing | `apps/payments` (`cash.py`, `views.py`) | POS → **Cash drawer**; portals → **Cash management** |
| Promotions & discounts | `apps/promotions` | POS checkout; portals → **Promotions** |
| Delivery dispatch | `apps/dispatch` | POS → **Dispatch**; `/driver` app; portals → **Delivery** |
| Device management | `apps/devices` (+ `organization.Device`) | portals → **Devices**; heartbeat agent in POS/KDS (`lib/device.ts`) |

New permissions (`accounts/permissions_catalog.py`, granted to the built-in roles by migration `accounts 0004`):
`cash.approve`, `cash.correct`, `cash.review`, `promotions.view|manage_network|manage_franchise|manage_store|apply_code`,
`dispatch.view|manage`, `drivers.manage`, `devices.view|administer|approve|configure_profiles|local_settings`.

---

## 1. Cash management and shift closing

**Model mapping** (recommended entity → implementation)

| Entity | Implementation |
|---|---|
| Register | `payments.Register` (store, optional attached device, code, name) |
| RegisterSession | `payments.CashSession` (existing table, extended: register, responsible employee, approval) |
| CashTransaction | `payments.CashTransaction` (the former `CashMovement`, migrated into a signed ledger) |
| CashReconciliation | `payments.CashReconciliation` (snapshot at close) |
| CashDiscrepancy | `payments.CashDiscrepancy` |
| ShiftClosing | `payments.ShiftClosing` (summary JSON, approval) |

**Ledger.** Every drawer movement is an immutable, signed `CashTransaction`. The kinds are: sale and refund (written
automatically by the payment services), drop, addition, paid-out, petty expense (with category), manager-authorized
correction, and transfer in/out. Expected cash is always `opening float + Σ ledger`. Nothing is typed in by hand.
Financial operations run in a transaction that locks the session row. Transfers lock both sessions in a
deterministic order to avoid deadlocks.

**Rules**
* One open session per register (a database constraint). Several employees work on separate registers at once.
* Cashiers open and close their own drawer. Opening for another employee requires `cash.review`. Drops are allowed
  for the drawer's employee. Additions, paid-outs and petty expenses need `cash.paid_in_out` or a manager code.
  Corrections always need a manager (`cash.correct`), and a manager cannot correct their own drawer alone.
* **Blind count:** cashiers do not see the expected amount while the drawer is open. At close they enter the
  count (optionally by denomination, which must add up). The server computes the variance.
* Variance beyond the threshold (store `payment_config.cash_variance_threshold_cents`, else the HQ policy, default
  500 cents) requires an explanation. The session then goes to `pending_approval` until someone with
  `cash.approve` other than the drawer's employee approves it. `cash_close_requires_approval` forces approval for
  every close.
* **Finalized sessions are immutable** (`CashSession.save` / `CashTransaction.save` guards). Later findings are
  post-close `correction` transactions. Reports show `adjusted_expected_cents` / `adjusted_variance_cents` next
  to the original figures.
* Manual cash transactions accept an `Idempotency-Key` header, so retries never double-record.
* Device configuration can disable the cash drawer or refunds on a device (e.g. kitchen displays). The server
  enforces this.

**Shift closing** is generated on close for the drawer's employee. It covers sales, tax, discounts, tips,
payment-method breakdown, refunds, voids (cancelled orders), discounts given, and the full cash reconciliation and
discrepancy. It can be viewed, printed (42-column text) and exported (CSV).

**API:** `registers/`, `cash-sessions/` (`current/`, `open/`, `{id}/transactions/`, `{id}/transfer/`, `{id}/close/`,
`{id}/approve/`, `{id}/reconciliation/`), `shift-closings/` (`{id}/print/`, `{id}/export/`, `{id}/approve/`),
`reports/cash/` (HQ network-wide, franchise, store).

---

## 2. Promotions and discounts

The earlier flat `catalog.Discount` codes were **migrated** into promotions (`promotions 0002`) and removed, so there
is one discount engine. Manual "open" discounts (manager-authorized amounts) remain as `OrderDiscount` rows without a
promotion.

**Entities:** `Promotion` (owner: HQ, a franchisee, or one store), `PromotionCode`, `PromotionProduct`
(eligible / excluded / reward / bundle slot / free modifier), `PromotionStore` (publication targets),
`PromotionRule` (customer-specific eligibility), `PromotionRedemption`, `FranchisePromotionPolicy`.

**Types:** percent, fixed amount, BOGO, buy X get Y, free product, free toppings/modifiers, bundle/meal deal. Product,
category, size and order-wide scopes are expressed through eligibility rows. Minimum order, max discount,
start/end dates, daily time window (store local time), days of week, channels, order types, total and per-customer
redemption limits, per-code limits, automatic vs. code vs. staff-selected application, priority, and
combinability are all supported.

**Engine (`promotions/engine.py`).** This is the server-authoritative evaluation. Candidates are processed by
priority, then by savings. A non-combinable promotion is never applied together with others; rejected requests are
returned with reasons such as expired, wrong day, minimum not met, limit reached or cannot be combined. Discounts
are allocated per unit and per line, so taxes are recomputed on the discounted amounts and no line goes below 0.

**Where it runs**
* `POST /stores/{id}/quote/` returns original and discounted line prices, applied/rejected/available promotions,
  delivery fee and eligibility, tax and total. The POS calls it as the cart changes.
* Order intake (`OrderIntakeService`) applies automatic promotions plus `promo_codes` / `promotion_ids` inside the
  persistence transaction. Limited promotions are row-locked, so concurrent orders cannot exceed limits. An invalid
  requested code returns 422 and nothing is saved. `expected_total_cents` is checked after discounts.
* `POST /orders/{id}/discounts/` (coupon entry after creation) re-validates every promotion on the order and
  recalculates totals.
* Cancelling or rejecting an order voids its redemptions, which frees the limits again.
* Marketplace orders (Uber Eats, DoorDash, Skip) never receive platform promotions.

**Governance.** HQ (`promotions.manage_network`) creates network promotions and publishes them to selected franchisees
or stores. HQ sets each franchisee's policy: allowed types, max percent, max fixed amount, and whether store managers
may be authorized. The franchisee decides whether its store managers may manage store promotions. A Celery beat
task moves promotions between scheduled, active and expired, and notifies POS terminals.

**Reporting:** `reports/promotions/` and `promotions/{id}/performance/` give redemptions, discount cost, revenue,
average order value with promotions vs. overall, redemption rate, and breakdowns by store, product and campaign.

---

## 3. Delivery dispatch

Only store-delivered orders (`order_type=delivery`) from the POS, phone, call centre or external APIs are
dispatched. Marketplace orders (`provider_delivery`) keep the marketplace's own couriers. No third-party driver
tracking is assumed.

**Entities:** `DriverProfile` (an employee with the delivery role; authorized stores = their store assignments),
`DriverAvailability` (history), `DeliveryZone`, `DeliveryAssignment` (one per order: lifecycle and current driver),
`DeliveryStatusHistory`, `DeliveryProof`.

**Zones and fees.** These are validated by the backend at quote and intake. Zones match by postal-code prefix or by
distance band. Without zones, the store's `delivery_config` radius, fee and minimum apply. Restricted postal
prefixes and per-zone minimum orders are enforced. **Setup requirement:** the platform does not geocode. Distance
checks need `latitude`/`longitude` on the delivery address (supplied by the ordering system) and on the store.
Postal-prefix zones work without coordinates. To geocode automatically, add a geocoding provider (with its
credentials) in `dispatch.services.resolve_delivery`.

**Lifecycle.** The statuses are: awaiting preparation → preparing → ready for dispatch → assigned → picked up → out for
delivery → delivered, plus failed, returned and cancelled. The kitchen and POS drive the early states. Pickup moves the
order to `out_for_delivery`, and delivery completes it. A failed delivery can be re-dispatched (the order goes back to
`ready`) or returned (the order is cancelled with the reason). Every transition is recorded with its timestamp,
actor and driver. Reassignments keep the previous driver.

**Assignment.** Manual assignment, reassignment and multi-assignment are supported. Unavailable, inactive,
over-capacity drivers and drivers from other stores are refused. An optional `version` guards against two
dispatchers racing. Automatic assignment (`delivery_config.auto_assign`) picks an available, authorized driver below
capacity who is not already on the road. It prefers batching with nearby or same-zone deliveries, then the lowest
workload, then the longest idle driver.

**Driver app (`/driver`, mobile).** Drivers sign in with store number and credentials and see **only their own**
deliveries. Other drivers' deliveries return 404. The app shows customer, address, items and the amount to
collect. It opens directions in the external maps app, records pickup, progress, delivery, failure with a reason
and notes, and captures photo proof (required when `delivery_config.proof_of_delivery` is set). Geolocation is
attached when the browser allows it.

**Real-time:** `/ws/stores/{id}/dispatch/` (dispatch board) and `/ws/driver/` (only the driver's own events).

**API:** `stores/{id}/dispatch/` (board with filters), `stores/{id}/dispatch/assign-many/`, `deliveries/{id}/`
(`assign/`, `unassign/`, `auto-assign/`, `status/`), `drivers/` (`availability/`, `history/`), `delivery-zones/`,
`driver/me/`, `driver/deliveries/{id}/status|proof/`, `reports/drivers/`.

---

## 4. Device management

**Entities:** `organization.Device` (extended: unique `uid`, status pending/active/deactivated, parent device for
peripherals, version, OS, app/sync status, pending offline count, current employee, config profile, local
settings, config versions), plus `devices.DeviceCredential`, `DeviceRegistration`, `DeviceHeartbeat`,
`DeviceAlert`, `DeviceConfiguration` and `DeviceConfigurationVersion`. Device audit history uses the platform's
immutable `AuditLog` (`entity_type="Device"`) rather than a second audit table.

**Identity and authentication.** Each device has its own credential (`dvc_<key_id>.<secret>`), which is shown once
and stored as a SHA-256 hash. Credentials expire (HQ policy `device_credential_lifetime_days`), and devices rotate
them through `POST device-auth/rotate/`. Administrators can revoke them (the device must then re-enroll with a
one-time code). Device authentication (`X-Device-Token`) is separate from employee JWTs, and requests from a
revoked, expired, pending or deactivated device are refused even with a valid employee token. Device POS login
checks both device approval and the employee's store assignment. HQ policy
`require_registered_device_for_pos` restricts POS/KDS sign-in to approved devices. Pre-upgrade device tokens were
migrated to `legacy…` credentials and keep working.

**Registration.** There are two paths:
1. A signed-in employee registers the device. It is `pending` unless the actor may approve (HQ, or franchise users
   when `franchisee_can_approve_devices`) or `devices_require_approval` is off.
2. HQ or franchise issues a single-use, short-lived **enrollment code** (`device-registrations/`). The device
   claims it at `device-auth/enroll/` with the store number.

**Heartbeats and monitoring.** Devices POST to `device-auth/heartbeat/`, reporting version, OS, app status, sync
status, pending offline transactions, applied config version and signed-in employee. Peripherals such as printers
are reported by their host device. Online status comes from **server receipt times**: a Celery beat task marks
devices offline after `device_offline_after_seconds` (default 180). Alerts are raised for going offline,
synchronization failure (error status or items pending for more than 15 minutes), outdated or unapproved versions,
rejected credentials, and repeated authentication failures (5 in 10 minutes). Alerts resolve automatically when the
condition clears, and critical ones can be acknowledged. Old heartbeats are pruned after 7 days.

**Configuration.** HQ profiles define features (cash drawer, refunds, discounts, coupon entry, offline queue,
dispatch, price override), heartbeat interval, receipt copies and so on, approved application versions, minimum
version, update policy and which keys store managers may override locally. Every profile save creates a version and
bumps `config_version` on assigned devices. Devices see `config_stale` in the heartbeat response, fetch
`device-auth/config/`, and acknowledge the version they applied. The server enforces features on cash, refunds,
discounts and coupon entry. There is no remote shell, desktop control or command execution.

**Administration scope.** HQ manages everything. Franchise users can view, administer (rename, reassign within their
own stores, deactivate, revoke) and approve devices for their stores when the HQ policy allows it. Store managers can
register devices (pending approval) and change locally overridable settings. A device with an open register session
cannot be reassigned.

**Real-time:** `/ws/devices/` streams status and alerts, scoped to HQ, the franchisee or the store.

**Hardware setup requirements.** Browsers cannot drive receipt printers or cash drawers directly. Receipts print
through the browser print dialog (80 mm layout). A physical printer or drawer integration needs a local bridge such
as the planned store gateway (`Device.TYPE_GATEWAY`) to send ESC/POS commands and report peripheral health in its
heartbeat. Card-present payments need Stripe Terminal credentials and a reader per store (see `payments/processors.py`).
