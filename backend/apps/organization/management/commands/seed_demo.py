"""Create the demonstration organisation: HQ → franchisees A/B/C → stores, menu, staff.

Idempotent. Passwords come from --password (default prompts are avoided for automation);
never use the demo password in production.
"""
from datetime import time

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Role, StoreAssignment, User
from apps.catalog.models import (
    AdditionalCharge,
    Category,
    Crust,
    Modifier,
    ModifierGroup,
    PrepCategory,
    Product,
    ProductDefaultModifier,
    ProductModifierGroup,
    ProductSize,
    StoreProduct,
    TaxCategory,
    TaxRate,
)
from apps.kitchen.models import KitchenStation
from apps.organization.models import Franchisee, Headquarters, Store, StoreHours

STRUCTURE = {
    "A": ("Franchisee A Holdings", [("1001", "Downtown", "Toronto", "ON"), ("1002", "Midtown", "Toronto", "ON")]),
    "B": ("Franchisee B Foods", [("2001", "Kanata", "Ottawa", "ON"), ("2002", "Orleans", "Ottawa", "ON")]),
    "C": ("Franchisee C Group", [("3001", "Plateau", "Montreal", "QC")]),
}

TOPPINGS = [("TOP-PEP", "Pepperoni", 175), ("TOP-MUSH", "Mushrooms", 150), ("TOP-ONION", "Red onion", 150),
            ("TOP-GPEP", "Green peppers", 150), ("TOP-OLIVE", "Black olives", 150), ("TOP-BACON", "Bacon", 200),
            ("TOP-SAUS", "Italian sausage", 200), ("TOP-HAM", "Ham", 175), ("TOP-PINE", "Pineapple", 150),
            ("TOP-CHEESE", "Mozzarella", 175), ("TOP-JAL", "Jalapeños", 150), ("TOP-CHKN", "Grilled chicken", 250)]


class Command(BaseCommand):
    help = "Seed demonstration data (organisation, menu, users)"

    def add_arguments(self, parser):
        parser.add_argument("--password", default="ChangeMe!2026", help="Password for all demo users")

    @transaction.atomic
    def handle(self, *args, **opts):
        pw = opts["password"]
        hq, _ = Headquarters.objects.get_or_create(name="Pizza Network HQ", defaults={
            "legal_name": "Pizza Network Inc.", "currency": "CAD", "policies": {
                "max_discount_percent": 50, "cash_variance_threshold_cents": 500,
                "devices_require_approval": True, "franchisee_can_approve_devices": True,
                "require_registered_device_for_pos": False, "device_offline_after_seconds": 180}})

        # --- taxes, prep categories ------------------------------------------------
        food, _ = TaxCategory.objects.get_or_create(code="FOOD", defaults={"name": "Prepared food"})
        TaxRate.objects.get_or_create(tax_category=food, region="ON", name="HST", defaults={"rate_bps": 1300})
        TaxRate.objects.get_or_create(tax_category=food, region="QC", name="GST", defaults={"rate_bps": 500})
        TaxRate.objects.get_or_create(tax_category=food, region="QC", name="QST", defaults={"rate_bps": 998})
        TaxRate.objects.get_or_create(tax_category=food, region="*", name="GST", defaults={"rate_bps": 500})
        oven, _ = PrepCategory.objects.get_or_create(code="OVEN", defaults={"name": "Pizza oven"})
        fryer, _ = PrepCategory.objects.get_or_create(code="FRYER", defaults={"name": "Fryer"})
        cold, _ = PrepCategory.objects.get_or_create(code="COLD", defaults={"name": "Cold / drinks"})

        # --- catalogue --------------------------------------------------------------
        pizzas, _ = Category.objects.get_or_create(code="PIZZA", defaults={"name": "Pizzas", "sort_order": 1})
        sides, _ = Category.objects.get_or_create(code="SIDES", defaults={"name": "Sides", "sort_order": 2})
        drinks, _ = Category.objects.get_or_create(code="DRINKS", defaults={"name": "Drinks", "sort_order": 3})
        crusts = []
        for i, (code, name, price) in enumerate([("REG", "Regular", 0), ("THIN", "Thin", 0),
                                                 ("STUFF", "Cheese-stuffed", 300), ("GF", "Gluten-free", 400)]):
            c, _ = Crust.objects.get_or_create(code=code, defaults={"name": name, "price_cents": price, "sort_order": i})
            crusts.append(c)
        tg, _ = ModifierGroup.objects.get_or_create(code="TOPPINGS", defaults={
            "name": "Toppings", "max_select": 10, "max_quantity_per_option": 2, "allow_half": True,
            "half_price_percent": 50, "scale_with_size": True})
        for i, (code, name, price) in enumerate(TOPPINGS):
            Modifier.objects.get_or_create(code=code, defaults={"group": tg, "name": name, "price_cents": price,
                                                                "is_topping": True, "sort_order": i})
        sauce, _ = ModifierGroup.objects.get_or_create(code="SAUCE", defaults={"name": "Sauce", "max_select": 1})
        for i, (code, name) in enumerate([("SAU-TOM", "Tomato"), ("SAU-BBQ", "BBQ"), ("SAU-ALF", "Alfredo")]):
            Modifier.objects.get_or_create(code=code, defaults={"group": sauce, "name": name, "sort_order": i})
        dips, _ = ModifierGroup.objects.get_or_create(code="DIPS", defaults={"name": "Dips", "max_select": 4,
                                                                            "max_quantity_per_option": 3})
        for i, (code, name) in enumerate([("DIP-GAR", "Garlic"), ("DIP-RAN", "Ranch"), ("DIP-BLU", "Blue cheese")]):
            Modifier.objects.get_or_create(code=code, defaults={"group": dips, "name": name, "price_cents": 99,
                                                                "sort_order": i})

        def pizza(sku, name, defaults, prices, desc):
            p, created = Product.objects.get_or_create(sku=sku, defaults={
                "name": name, "description": desc, "category": pizzas, "status": "approved", "is_pizza": True,
                "tax_category": food, "prep_category": oven, "allow_half_and_half": True, "max_toppings": 10,
                "default_crust": crusts[0], "min_price_cents": 800, "max_price_cents": 4000})
            if created:
                p.crusts.set(crusts)
                for i, (code, sname, price, mult) in enumerate([("S", "Small", prices[0], 75),
                                                                ("M", "Medium", prices[1], 100),
                                                                ("L", "Large", prices[2], 125),
                                                                ("XL", "X-Large", prices[3], 150)]):
                    ProductSize.objects.create(product=p, code=code, name=sname, price_cents=price,
                                               modifier_multiplier_percent=mult, sort_order=i)
                ProductModifierGroup.objects.create(product=p, group=tg, sort_order=1)
                ProductModifierGroup.objects.create(product=p, group=sauce, sort_order=0)
                ProductModifierGroup.objects.create(product=p, group=dips, sort_order=2)
                for d in defaults:
                    ProductDefaultModifier.objects.create(product=p, modifier=Modifier.objects.get(code=d))
            return p

        products = [
            pizza("PZ-CHEESE", "Cheese Pizza", ["TOP-CHEESE", "SAU-TOM"], [999, 1299, 1599, 1899], "Classic mozzarella"),
            pizza("PZ-PEP", "Pepperoni Pizza", ["TOP-CHEESE", "TOP-PEP", "SAU-TOM"], [1149, 1449, 1749, 2099],
                  "Mozzarella and pepperoni"),
            pizza("PZ-DLX", "Deluxe Pizza", ["TOP-CHEESE", "TOP-PEP", "TOP-MUSH", "TOP-GPEP", "SAU-TOM"],
                  [1349, 1649, 1999, 2349], "Pepperoni, mushrooms, green peppers"),
            pizza("PZ-HAW", "Hawaiian Pizza", ["TOP-CHEESE", "TOP-HAM", "TOP-PINE", "SAU-TOM"],
                  [1249, 1549, 1849, 2199], "Ham and pineapple"),
            pizza("PZ-BYO", "Build Your Own", ["TOP-CHEESE", "SAU-TOM"], [999, 1299, 1599, 1899],
                  "Choose your toppings"),
        ]
        wings, created = Product.objects.get_or_create(sku="SD-WINGS", defaults={
            "name": "Chicken Wings (10)", "category": sides, "status": "approved", "base_price_cents": 1299,
            "tax_category": food, "prep_category": fryer, "min_price_cents": 999, "max_price_cents": 1999})
        if created:
            ProductModifierGroup.objects.create(product=wings, group=dips)
        bread, _ = Product.objects.get_or_create(sku="SD-GBREAD", defaults={
            "name": "Garlic Bread", "category": sides, "status": "approved", "base_price_cents": 599,
            "tax_category": food, "prep_category": oven, "min_price_cents": 399, "max_price_cents": 999})
        cola, _ = Product.objects.get_or_create(sku="DR-COLA", defaults={
            "name": "Cola 591ml", "category": drinks, "status": "approved", "base_price_cents": 299,
            "tax_category": food, "prep_category": cold, "min_price_cents": 199, "max_price_cents": 499})
        products += [wings, bread, cola]
        AdditionalCharge.objects.get_or_create(code="BAG", defaults={"name": "Bag fee", "amount_cents": 0,
                                                                     "applies_to": "all", "is_active": False})

        # --- organisation & users ----------------------------------------------------
        role = {r.code: r for r in Role.objects.all()}

        def user(username, r, first, franchisee=None, stores=(), pin=None, mgr=None):
            u, created = User.objects.get_or_create(username=username, defaults={
                "role": role[r], "first_name": first, "franchisee": franchisee})
            if created:
                u.set_password(pw)
                u.set_pin(pin)
                u.set_manager_code(mgr)
                u.save()
            for s in stores:
                StoreAssignment.objects.get_or_create(user=u, store=s)
            return u

        user("hq.admin", "hq_admin", "HQ Admin")
        user("hq.menu", "hq_staff", "HQ Menu Manager")
        for code, (fname, stores) in STRUCTURE.items():
            f, _ = Franchisee.objects.get_or_create(code=code, defaults={"name": fname, "headquarters": hq})
            user(f"owner.{code.lower()}", "franchisee_owner", f"Owner {code}", franchisee=f, mgr="9999")
            for number, name, city, region in stores:
                s, created = Store.objects.get_or_create(number=number, defaults={
                    "franchisee": f, "name": f"{name} #{number}", "address_line1": f"{number} Main Street",
                    "city": city, "region": region, "postal_code": "A1A 1A1", "phone": f"+1-555-{number}",
                    "timezone": "America/Toronto", "receipt_footer": "Thank you!"})
                if created:
                    for d in range(7):
                        StoreHours.objects.create(store=s, day_of_week=d, opens_at=time(0, 0), closes_at=time(23, 59))
                    KitchenStation.objects.create(store=s, code="OVEN", name="Oven line", prep_categories=["OVEN"],
                                                  sort_order=0)
                    KitchenStation.objects.create(store=s, code="FRY", name="Fryer", prep_categories=["FRYER"],
                                                  sort_order=1)
                    KitchenStation.objects.create(store=s, code="EXPO", name="Expo", is_expo=True, sort_order=2)
                    for p in products:
                        StoreProduct.objects.create(store=s, product=p, enabled=True)
                user(f"manager.{number}", "store_manager", f"Manager {number}", stores=[s], pin="1111", mgr="1234")
                user(f"cashier.{number}", "cashier", f"Cashier {number}", stores=[s], pin="2222")
                user(f"kitchen.{number}", "kitchen", f"Cook {number}", stores=[s], pin="3333")
                user(f"driver.{number}", "delivery", f"Driver {number}", stores=[s], pin="4444")
        seed_modules(products)
        self.stdout.write(self.style.SUCCESS("Demo data ready. Users: hq.admin, owner.a, manager.1001, "
                                             "cashier.1001, kitchen.1001 (password as given)."))


def seed_modules(products):
    """Demo data for promotions, dispatch and device management."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.devices.models import DeviceConfiguration
    from apps.dispatch.models import DeliveryZone, DriverProfile
    from apps.promotions.models import (
        FranchisePromotionPolicy,
        Promotion,
        PromotionCode,
        PromotionProduct,
    )

    sku = {p.sku: p for p in products}
    pizzas = Category.objects.get(code="PIZZA")
    toppings = ModifierGroup.objects.get(code="TOPPINGS")

    def promo(name, **kw):
        items = kw.pop("items", [])
        codes = kw.pop("codes", [])
        p, created = Promotion.objects.get_or_create(name=name, franchisee=kw.pop("franchisee", None), defaults=kw)
        if created:
            for i in items:
                PromotionProduct.objects.create(promotion=p, **i)
            for c in codes:
                PromotionCode.objects.get_or_create(code=c, defaults={"promotion": p})
            p.status = p.derive_status()
            p.save(update_fields=["status"])
        return p

    promo("Staff 10%", promo_type="percent", value=1000, requires_code=True, requires_manager=True, codes=["STAFF10"],
          campaign="Staff")
    promo("$5 off $20+", promo_type="fixed", value=500, min_subtotal_cents=2000, requires_code=True, codes=["PROMO5"],
          campaign="Always on")
    promo("Free garlic bread with any large pizza", promo_type="free_product", auto_apply=True, priority=10,
          get_quantity=1, campaign="Garlic bread launch",
          items=[{"role": "eligible", "category": pizzas, "size_code": "L"}, {"role": "reward", "product": sku["SD-GBREAD"]}])
    promo("Wings & Cola deal $14.99", promo_type="bundle", value=1499, auto_apply=True, priority=5, campaign="Game night",
          items=[{"role": "bundle", "product": sku["SD-WINGS"], "slot": 0},
                 {"role": "bundle", "product": sku["DR-COLA"], "slot": 1}])
    promo("2 free toppings", promo_type="free_modifier", get_quantity=2, requires_code=True, codes=["TOPPINGS2"],
          items=[{"role": "eligible", "category": pizzas}, {"role": "modifier", "modifier_group": toppings}],
          max_redemptions=500, max_per_customer=1, campaign="Toppings week")
    promo("Weekend BOGO pizza", promo_type="bogo", auto_apply=True, combinable=False, priority=20, days_of_week=[5, 6],
          starts_at=timezone.now() + timedelta(days=7), ends_at=timezone.now() + timedelta(days=37),
          items=[{"role": "eligible", "category": pizzas}], campaign="Weekend BOGO")
    for code, types, sm in (("A", ["percent", "fixed", "bogo", "free_product", "bundle"], True),
                            ("B", ["percent", "fixed"], False), ("C", [], False)):
        f = Franchisee.objects.get(code=code)
        FranchisePromotionPolicy.objects.get_or_create(franchisee=f, defaults={
            "can_create": bool(types), "allowed_types": types, "store_managers_can_manage": sm})

    for s in Store.objects.all():
        if s.number == "1001" and not s.delivery_config.get("auto_assign"):
            s.latitude, s.longitude = 43.642600, -79.387100
            s.delivery_config = {**s.delivery_config, "auto_assign": True, "proof_of_delivery": "none"}
            s.save()
        if s.number == "1002" and not s.delivery_zones.exists():
            s.latitude, s.longitude = 43.704400, -79.398600
            s.delivery_config = {**s.delivery_config, "proof_of_delivery": "photo"}
            s.save()
            DeliveryZone.objects.create(store=s, name="Core (0–3 km)", min_distance_km=0, max_distance_km=3,
                                        postal_prefixes=["M4S", "M5P"], fee_cents=299, min_order_cents=1500,
                                        estimated_minutes=30)
            DeliveryZone.objects.create(store=s, name="Extended (3–8 km)", min_distance_km=3, max_distance_km=8,
                                        fee_cents=499, min_order_cents=2500, estimated_minutes=45, sort_order=1)
    for u in User.objects.filter(role__code="delivery"):
        DriverProfile.objects.get_or_create(user=u, defaults={"phone": "+1-555-0100", "vehicle": "Hatchback"})

    DeviceConfiguration.objects.get_or_create(name="Standard POS", defaults={
        "description": "Front counter terminals and registers", "device_types": ["pos", "register",
                                                                                 "manager_workstation"],
        "local_overridable": ["receipt_copies", "idle_lock_minutes"], "approved_versions": ["1.1.0"],
        "minimum_version": "1.0.0", "is_default": True})
    DeviceConfiguration.objects.get_or_create(name="Kitchen display", defaults={
        "description": "KDS screens", "device_types": ["kds"], "is_default": True, "approved_versions": ["1.1.0"],
        "minimum_version": "1.0.0", "local_overridable": ["idle_lock_minutes"],
        "settings": {"features": {"cash_drawer": False, "refunds": False, "discounts": False, "coupon_entry": False,
                                  "offline_queue": False, "dispatch": False}, "heartbeat_interval_seconds": 60}})
