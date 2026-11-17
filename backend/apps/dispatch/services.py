"""Delivery zones/fees, the delivery lifecycle, driver assignment (manual + automatic) and reporting."""
from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.utils import timezone

from apps.core.audit import audit
from apps.core.exceptions import ConflictError, DomainError
from apps.orders.models import Order, OrderStatus, OrderType
from apps.orders.realtime import broadcast

from .models import (
    ACTIVE_FOR_DRIVER,
    DeliveryAssignment,
    DeliveryProof,
    DeliveryStatus,
    DeliveryStatusHistory,
    DeliveryZone,
    DriverAvailability,
    DriverProfile,
    DriverStatus,
)

DS = DeliveryStatus

TRANSITIONS = {
    DS.AWAITING_PREPARATION: {DS.PREPARING, DS.READY, DS.ASSIGNED, DS.CANCELLED},
    DS.PREPARING: {DS.READY, DS.ASSIGNED, DS.CANCELLED},
    DS.READY: {DS.ASSIGNED, DS.CANCELLED},
    DS.ASSIGNED: {DS.PICKED_UP, DS.READY, DS.ASSIGNED, DS.CANCELLED},
    DS.PICKED_UP: {DS.OUT_FOR_DELIVERY, DS.DELIVERED, DS.FAILED},
    DS.OUT_FOR_DELIVERY: {DS.DELIVERED, DS.FAILED},
    DS.FAILED: {DS.READY, DS.RETURNED},
    DS.DELIVERED: set(),
    DS.RETURNED: set(),
    DS.CANCELLED: set(),
}
DRIVER_ALLOWED = {DS.PICKED_UP, DS.OUT_FOR_DELIVERY, DS.DELIVERED, DS.FAILED}


# ---------------------------------------------------------------------------
# Zones & fees
# ---------------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp, dl = p2 - p1, math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _postal(address: dict) -> str:
    return (address.get("postal_code") or "").replace(" ", "").upper()


def resolve_delivery(store, address: dict, subtotal_cents: int | None = None) -> dict:
    """Validate that the store delivers to ``address`` and return fee/minimum/ETA.

    Distance needs coordinates (``latitude``/``longitude`` on the address and on the store); the
    platform does not geocode. Zones may instead match postal-code prefixes."""
    cfg = store.delivery_config or {}
    if not cfg.get("enabled"):
        raise DomainError("Store does not offer delivery", code="delivery_disabled")
    address = address or {}
    postal = _postal(address)
    for prefix in cfg.get("restricted_postal_prefixes") or []:
        if postal.startswith(prefix.replace(" ", "").upper()):
            raise DomainError("Delivery is not available to this address", code="delivery_restricted")
    distance = None
    if address.get("latitude") is not None and address.get("longitude") is not None and store.latitude is not None \
            and store.longitude is not None:
        distance = round(haversine_km(store.latitude, store.longitude, address["latitude"], address["longitude"]), 2)
    zones = list(store.delivery_zones.filter(is_active=True))
    zone = None
    if zones:
        for z in zones:
            if z.postal_prefixes and any(postal.startswith(p.replace(" ", "").upper()) for p in z.postal_prefixes):
                zone = z
                break
            if distance is not None and z.max_distance_km is not None and \
                    Decimal(str(z.min_distance_km)) <= Decimal(str(distance)) < Decimal(str(z.max_distance_km)):
                zone = z
                break
        if zone is None:
            if distance is None and any(z.max_distance_km is not None for z in zones):
                raise DomainError("Address coordinates are required to determine the delivery zone",
                                  code="address_location_required")
            raise DomainError("Address is outside the store's delivery area", code="outside_delivery_area")
        fee, minimum, eta = zone.fee_cents, zone.min_order_cents, zone.estimated_minutes
    else:
        radius = cfg.get("radius_km")
        if distance is not None and radius is not None and distance > float(radius):
            raise DomainError(f"Address is {distance} km away; the store delivers within {radius} km",
                              code="outside_delivery_area")
        fee = int(cfg.get("delivery_fee_cents") or 0)
        minimum = int(cfg.get("minimum_order_cents") or 0)
        eta = int(cfg.get("estimated_minutes") or 40)
    if subtotal_cents is not None and minimum and subtotal_cents < minimum:
        raise DomainError(f"Delivery requires a minimum order of {minimum / 100:.2f}", code="below_delivery_minimum",
                          errors=[{"code": "below_delivery_minimum", "minimum_cents": minimum,
                                   "message": "Order subtotal is below the delivery minimum"}])
    return {"zone_id": zone.id if zone else None, "zone_name": zone.name if zone else None, "fee_cents": fee,
            "min_order_cents": minimum, "estimated_minutes": eta, "distance_km": distance,
            "distance_verified": distance is not None}


# ---------------------------------------------------------------------------
# Real-time
# ---------------------------------------------------------------------------
def summary(a: DeliveryAssignment) -> dict:
    o = a.order
    return {"id": a.id, "order_id": str(o.pk), "order_number": o.order_number, "store_id": a.store_id,
            "status": a.status, "order_status": o.status, "source": o.source,
            "driver": {"id": a.driver_id, "username": a.driver.user.username} if a.driver_id else None,
            "customer": o.customer_snapshot, "address": o.delivery_address, "total_cents": o.total_cents,
            "payment_status": o.payment_status, "placed_at": o.placed_at.isoformat(),
            "estimated_delivery_at": a.estimated_delivery_at.isoformat() if a.estimated_delivery_at else None,
            "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
            "picked_up_at": a.picked_up_at.isoformat() if a.picked_up_at else None,
            "delivered_at": a.delivered_at.isoformat() if a.delivered_at else None,
            "failure_reason": a.failure_reason, "zone": a.zone.name if a.zone_id else None,
            "distance_km": float(a.distance_km) if a.distance_km is not None else None, "version": a.version}


def push(a: DeliveryAssignment, event: str, previous_driver_user_id=None):
    def send():
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        a.refresh_from_db()
        payload = {"event": event, "delivery": summary(a)}
        broadcast(a.store_id, "dispatch", payload)
        layer = get_channel_layer()
        if layer is None:
            return
        for uid in {a.driver.user_id if a.driver_id else None, previous_driver_user_id} - {None}:
            async_to_sync(layer.group_send)(f"driver_{uid}", {"type": "push", "payload": payload})
    transaction.on_commit(send)


def push_driver(d: DriverProfile, event: str = "driver.status"):
    payload = {"event": event, "driver": {"id": d.id, "username": d.user.username, "status": d.status,
                                          "current_store": d.current_store_id}}
    for sid in d.store_ids():
        transaction.on_commit(lambda sid=sid: broadcast(sid, "dispatch", payload))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def create_for_order(order: Order, resolution: dict | None) -> DeliveryAssignment | None:
    if order.order_type != OrderType.DELIVERY:
        return None
    r = resolution or {}
    a, _ = DeliveryAssignment.objects.get_or_create(order=order, defaults={
        "store": order.store, "zone_id": r.get("zone_id"), "fee_cents": r.get("fee_cents") or 0,
        "distance_km": r.get("distance_km"), "estimated_minutes": r.get("estimated_minutes"),
        "estimated_delivery_at": (order.scheduled_for or order.placed_at) + timedelta(
            minutes=r.get("estimated_minutes") or 40)})
    DeliveryStatusHistory.objects.get_or_create(assignment=a, to_status=a.status, defaults={"actor_label": "system"})
    return a


def _log(a, frm, to, actor=None, note="", driver=None, previous=None, lat=None, lng=None, label=None):
    DeliveryStatusHistory.objects.create(
        assignment=a, from_status=frm, to_status=to, driver=driver or a.driver, previous_driver=previous,
        actor=actor, actor_label=label or (f"user:{actor.username}" if actor else "system"), note=note[:2000],
        latitude=lat, longitude=lng)


def _set(a: DeliveryAssignment, to: str, actor=None, note="", **kw):
    frm = a.status
    if to not in TRANSITIONS.get(frm, set()):
        raise ConflictError(f"Delivery cannot move from '{frm}' to '{to}'", code="invalid_delivery_transition")
    a.status = to
    a.version += 1
    now = timezone.now()
    if to == DS.PICKED_UP:
        a.picked_up_at = now
    elif to == DS.DELIVERED:
        a.delivered_at = now
    elif to == DS.FAILED:
        a.failed_at = now
    a.save()
    _log(a, frm, to, actor, note, lat=kw.get("lat"), lng=kw.get("lng"), label=kw.get("label"))


def on_order_status_changed(order: Order, from_status: str, to_status: str) -> None:
    """Keep the delivery record in step with kitchen/POS order status changes."""
    if order.order_type != OrderType.DELIVERY:
        return
    a = DeliveryAssignment.objects.filter(order=order).select_related("driver__user").first()
    if a is None:
        return
    with transaction.atomic():
        a = DeliveryAssignment.objects.select_for_update().get(pk=a.pk)
        if to_status == OrderStatus.PREPARING and a.status == DS.AWAITING_PREPARATION:
            _set(a, DS.PREPARING, label="kitchen")
        elif to_status == OrderStatus.READY and a.status in (DS.AWAITING_PREPARATION, DS.PREPARING):
            _set(a, DS.READY, label="kitchen")
        elif to_status in (OrderStatus.CANCELLED, OrderStatus.REJECTED) and a.status in (
                DS.AWAITING_PREPARATION, DS.PREPARING, DS.READY, DS.ASSIGNED):
            _set(a, DS.CANCELLED, note=order.cancellation_reason or order.rejection_reason, label="order")
        elif to_status == OrderStatus.COMPLETED and a.status in (DS.PICKED_UP, DS.OUT_FOR_DELIVERY):
            _set(a, DS.DELIVERED, note="Completed from POS", label="pos")
        else:
            return
    push(a, "delivery.updated")
    if a.status == DS.READY and (a.store.delivery_config or {}).get("auto_assign"):
        try:
            auto_assign(a, actor=None)
        except DomainError:
            pass


def active_load(driver: DriverProfile) -> int:
    return driver.deliveries.filter(status__in=ACTIVE_FOR_DRIVER).count()


def check_driver(driver: DriverProfile, store, *, exclude_assignment=None):
    if not driver.is_active or not driver.user.is_active:
        raise DomainError("Driver is inactive", code="driver_inactive")
    if store.id not in driver.store_ids():
        raise DomainError("Driver is not authorized for this store", code="driver_not_assigned_to_store",
                          status_code=403)
    if driver.status != DriverStatus.AVAILABLE:
        raise DomainError(f"Driver is {driver.get_status_display().lower()}", code="driver_unavailable")
    load = driver.deliveries.filter(status__in=ACTIVE_FOR_DRIVER).exclude(
        pk=exclude_assignment.pk if exclude_assignment else None).count()
    if load >= driver.max_active_deliveries:
        raise DomainError(f"Driver already has {load} active deliveries (max {driver.max_active_deliveries})",
                          code="driver_at_capacity")


def assign(a: DeliveryAssignment, driver: DriverProfile, actor, *, expected_version: int | None = None,
           auto: bool = False) -> DeliveryAssignment:
    with transaction.atomic():
        a = DeliveryAssignment.objects.select_for_update(of=("self",)).select_related("store", "order").get(pk=a.pk)
        DriverProfile.objects.select_for_update().get(pk=driver.pk)  # serialize workload checks per driver
        driver.refresh_from_db()
        if expected_version is not None and a.version != expected_version:
            raise ConflictError("Delivery was changed by someone else; refresh and retry", code="stale_version")
        if a.order.is_third_party:
            raise DomainError("Marketplace deliveries are handled by the provider", code="provider_delivery")
        if a.driver_id == driver.id and a.status == DS.ASSIGNED:
            return a
        check_driver(driver, a.store, exclude_assignment=a)
        previous = a.driver
        frm = a.status
        if DS.ASSIGNED not in TRANSITIONS.get(frm, set()):
            raise ConflictError(f"Cannot assign a delivery that is '{frm}'", code="invalid_delivery_transition")
        a.driver, a.status, a.assigned_at, a.assigned_by, a.auto_assigned = driver, DS.ASSIGNED, timezone.now(), \
            actor, auto
        a.version += 1
        a.save()
        _log(a, frm, DS.ASSIGNED, actor, "auto-assigned" if auto else ("reassigned" if previous else "assigned"),
             driver=driver, previous=previous, label=None if actor else "auto-dispatch")
        audit("delivery.reassign" if previous else "delivery.assign", a.order, entity_type="Order", actor=actor,
              actor_label=None if actor else "system:auto-dispatch",
              data={"driver": driver.user.username, "previous_driver": previous.user.username if previous else None,
                    "auto": auto})
    push(a, "delivery.assigned", previous.user_id if previous else None)
    return a


def unassign(a: DeliveryAssignment, actor) -> DeliveryAssignment:
    with transaction.atomic():
        a = DeliveryAssignment.objects.select_for_update().get(pk=a.pk)
        if a.status != DS.ASSIGNED:
            raise ConflictError("Only deliveries not yet picked up can be unassigned", code="invalid_delivery_transition")
        previous = a.driver
        order_ready = a.order.status == OrderStatus.READY
        a.driver = None
        a.status = DS.READY if order_ready else DS.PREPARING if a.order.status == OrderStatus.PREPARING \
            else DS.AWAITING_PREPARATION
        a.version += 1
        a.save()
        _log(a, DS.ASSIGNED, a.status, actor, "unassigned", driver=None, previous=previous)
        audit("delivery.unassign", a.order, entity_type="Order", actor=actor,
              data={"previous_driver": previous.user.username if previous else None})
    push(a, "delivery.updated", previous.user_id if previous else None)
    return a


def auto_assign(a: DeliveryAssignment, actor=None) -> DeliveryAssignment:
    """Pick the best available driver: authorized for the store, available, below capacity; prefer
    batching with deliveries to the same zone/nearby, then lowest workload, then longest idle."""
    store = a.store
    candidates = []
    for d in DriverProfile.objects.filter(is_active=True, status=DriverStatus.AVAILABLE,
                                          user__store_assignments__store=store,
                                          user__store_assignments__active=True).select_related("user").distinct():
        if d.current_store_id and d.current_store_id != store.id:
            continue
        pending = list(d.deliveries.filter(status__in=ACTIVE_FOR_DRIVER).select_related("order"))
        if len(pending) >= d.max_active_deliveries:
            continue
        if any(p.status != DS.ASSIGNED for p in pending):
            continue  # already on the road; don't stack new pickups behind an active run
        batch = 0
        for p in pending:
            if a.zone_id and p.zone_id == a.zone_id:
                batch = 1
            pa, aa = p.order.delivery_address or {}, a.order.delivery_address or {}
            if None not in (pa.get("latitude"), pa.get("longitude"), aa.get("latitude"), aa.get("longitude")) and \
                    haversine_km(pa["latitude"], pa["longitude"], aa["latitude"], aa["longitude"]) <= 2.0:
                batch = 1
        dist = float(a.distance_km or 0)
        score = len(pending) * 10 - batch * 15 + (dist / 5 if pending else 0)
        idle = (timezone.now() - (d.status_changed_at or timezone.now())).total_seconds()
        candidates.append((score, -idle, d.id, d))
    if not candidates:
        raise DomainError("No available driver for this delivery", code="no_driver_available")
    candidates.sort(key=lambda c: c[:3])
    return assign(a, candidates[0][3], actor, auto=True)


def set_driver_status(driver: DriverProfile, status: str, actor, store=None) -> DriverProfile:
    if status not in DriverStatus.values:
        raise DomainError("Invalid driver status", code="invalid_status", status_code=400)
    if store is not None and store.id not in driver.store_ids():
        raise DomainError("Driver is not authorized for this store", code="driver_not_assigned_to_store",
                          status_code=403)
    with transaction.atomic():
        d = DriverProfile.objects.select_for_update(of=("self",)).select_related("user").get(pk=driver.pk)
        d.status = status
        d.status_changed_at = timezone.now()
        if store is not None:
            d.current_store = store
        d.save()
        DriverAvailability.objects.create(driver=d, store=d.current_store, status=status, changed_by=actor)
        audit("driver.status", d, entity_type="DriverProfile", actor=actor, store=d.current_store,
              data={"status": status})
        push_driver(d)
    return d


def proof_required(store) -> str:
    return (store.delivery_config or {}).get("proof_of_delivery", "none")


def update_status(a: DeliveryAssignment, to: str, actor, *, by_driver: bool, note: str = "", failure_reason: str = "",
                  lat=None, lng=None) -> DeliveryAssignment:
    from apps.orders.state import transition

    with transaction.atomic():
        a = DeliveryAssignment.objects.select_for_update(of=("self",)).select_related("order", "store", "driver__user").get(
            pk=a.pk)
        if by_driver:
            if a.driver is None or a.driver.user_id != actor.pk:
                raise DomainError("This delivery is not assigned to you", code="not_your_delivery", status_code=403)
            if to not in DRIVER_ALLOWED:
                raise DomainError("Drivers can only mark pickup, delivery progress or failure", code="forbidden",
                                  status_code=403)
        order = a.order
        if to == DS.PICKED_UP and order.status not in (OrderStatus.READY, OrderStatus.OUT_FOR_DELIVERY):
            raise ConflictError("Order is not ready for pickup yet", code="order_not_ready")
        if to == DS.DELIVERED:
            req = proof_required(a.store)
            kinds = set(a.proofs.values_list("kind", flat=True))
            if (req == "photo" and "photo" not in kinds) or (req == "signature" and "signature" not in kinds) or \
                    (req == "any" and not kinds & {"photo", "signature"}):
                raise DomainError(f"Proof of delivery ({req}) is required before completing", code="proof_required")
        if to == DS.FAILED and not failure_reason:
            raise DomainError("A failure reason is required", code="reason_required", status_code=400)
        if to == DS.FAILED:
            a.failure_reason = failure_reason[:300]
        if note:
            a.driver_notes = (a.driver_notes + "\n" + note).strip()[:4000]
        _set(a, to, actor, note or failure_reason, lat=lat, lng=lng)
        if lat is not None and a.driver_id:
            DriverProfile.objects.filter(pk=a.driver_id).update(last_latitude=lat, last_longitude=lng)
        # Order status follows the delivery
        if to in (DS.PICKED_UP, DS.OUT_FOR_DELIVERY) and order.status == OrderStatus.READY:
            transition(order, OrderStatus.OUT_FOR_DELIVERY, actor=actor, reason="Driver picked up")
        elif to == DS.DELIVERED:
            transition(order, OrderStatus.COMPLETED, actor=actor, reason="Delivered")
        elif to == DS.RETURNED:
            transition(order, OrderStatus.CANCELLED, actor=actor, reason=f"Delivery failed: {a.failure_reason}")
        elif to == DS.READY and order.status == OrderStatus.OUT_FOR_DELIVERY:
            a.driver = None
            a.save(update_fields=["driver"])
            transition(order, OrderStatus.READY, actor=actor, reason="Re-dispatch after failed delivery")
        audit(f"delivery.{to}", order, entity_type="Order", actor=actor,
              data={"note": note, "failure_reason": failure_reason, "by_driver": by_driver})
    push(a, "delivery.updated")
    return a


def add_proof(a: DeliveryAssignment, actor, *, kind: str, image=None, recipient_name="", note="", lat=None,
              lng=None) -> DeliveryProof:
    if a.driver is None or a.driver.user_id != actor.pk:
        if not actor.has_permission("dispatch.manage"):
            raise DomainError("This delivery is not assigned to you", code="not_your_delivery", status_code=403)
    if a.status not in (DS.PICKED_UP, DS.OUT_FOR_DELIVERY, DS.DELIVERED):
        raise ConflictError("Proof can only be captured during or after delivery", code="invalid_state")
    if kind in (DeliveryProof.KIND_PHOTO, DeliveryProof.KIND_SIGNATURE) and image is None:
        raise DomainError("An image is required", code="image_required", status_code=400)
    p = DeliveryProof.objects.create(assignment=a, kind=kind, image=image, recipient_name=recipient_name[:100],
                                     note=note, latitude=lat, longitude=lng, captured_by=actor)
    audit("delivery.proof", a.order, entity_type="Order", actor=actor, data={"kind": kind})
    return p


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def driver_performance(assignments_qs) -> list[dict]:
    done = assignments_qs.filter(status=DS.DELIVERED)
    rows = []
    trip = ExpressionWrapper(F("delivered_at") - F("picked_up_at"), output_field=DurationField())
    total = ExpressionWrapper(F("delivered_at") - F("order__placed_at"), output_field=DurationField())
    for d in DriverProfile.objects.filter(deliveries__in=assignments_qs).distinct().select_related("user"):
        mine = assignments_qs.filter(driver=d)
        delivered = done.filter(driver=d)
        agg = delivered.aggregate(trip=Avg(trip), total=Avg(total))
        on_time = delivered.filter(delivered_at__lte=F("estimated_delivery_at")).count()
        n = delivered.count()
        rows.append({"driver_id": d.id, "username": d.user.username, "assigned": mine.count(), "delivered": n,
                     "failed": mine.filter(status__in=[DS.FAILED, DS.RETURNED]).count(),
                     "avg_trip_minutes": round(agg["trip"].total_seconds() / 60, 1) if agg["trip"] else None,
                     "avg_order_to_door_minutes": round(agg["total"].total_seconds() / 60, 1) if agg["total"] else None,
                     "on_time_rate": round(on_time / n, 3) if n else None})
    return sorted(rows, key=lambda r: -r["delivered"])


def dispatch_board(store, params) -> dict:
    qs = DeliveryAssignment.objects.filter(store=store).select_related("order", "driver__user", "zone")
    since = timezone.now() - timedelta(hours=int(params.get("hours") or 12))
    qs = qs.filter(Q(created_at__gte=since) | Q(status__in=[DS.ASSIGNED, DS.PICKED_UP, DS.OUT_FOR_DELIVERY,
                                                            DS.READY, DS.FAILED]))
    if params.get("status"):
        qs = qs.filter(status__in=params["status"].split(","))
    if params.get("driver"):
        qs = qs.filter(driver_id=params["driver"])
    if params.get("source"):
        qs = qs.filter(order__source__in=params["source"].split(","))
    drivers = []
    for d in DriverProfile.objects.filter(user__store_assignments__store=store, user__store_assignments__active=True,
                                          is_active=True).select_related("user").distinct():
        drivers.append({"id": d.id, "username": d.user.username, "name": d.user.get_full_name() or d.user.username,
                        "phone": d.phone, "status": d.status, "current_store": d.current_store_id,
                        "active_deliveries": active_load(d), "max_active_deliveries": d.max_active_deliveries,
                        "status_changed_at": d.status_changed_at})
    return {"deliveries": [summary(a) for a in qs.order_by("created_at")[:300]], "drivers": drivers,
            "counts": dict(qs.values_list("status").annotate(n=Count("id")))}
