"""Starter catalogue reference data: product categories, tax categories and rates, kitchen prep categories,
crusts and topping/sauce/dip groups. Idempotent (get_or_create by code): existing records are never changed.

Used by `seed_demo` and by the HQ portal's "Load starter setup" button on a fresh installation.
"""
from django.db import transaction

from .models import Category, Crust, Modifier, ModifierGroup, PrepCategory, TaxCategory, TaxRate

TOPPINGS = [("TOP-PEP", "Pepperoni", 175), ("TOP-MUSH", "Mushrooms", 150), ("TOP-ONION", "Red onion", 150),
            ("TOP-GPEP", "Green peppers", 150), ("TOP-OLIVE", "Black olives", 150), ("TOP-BACON", "Bacon", 200),
            ("TOP-SAUS", "Italian sausage", 200), ("TOP-HAM", "Ham", 175), ("TOP-PINE", "Pineapple", 150),
            ("TOP-CHEESE", "Mozzarella", 175), ("TOP-JAL", "Jalapeños", 150), ("TOP-CHKN", "Grilled chicken", 250)]

# Canadian sales tax on prepared food. "*" applies to any region without its own rates (GST only: AB, BC, MB,
# SK, territories). Review against current rates for every province you operate in.
TAX_RATES = [("ON", "HST", 1300), ("QC", "GST", 500), ("QC", "QST", 998), ("*", "GST", 500)]


@transaction.atomic
def apply_starter_catalog() -> dict:
    created = {"categories": 0, "tax_categories": 0, "tax_rates": 0, "prep_categories": 0, "crusts": 0,
               "modifier_groups": 0, "modifiers": 0}

    def count(key, was_created):
        created[key] += int(was_created)

    food, c = TaxCategory.objects.get_or_create(code="FOOD", defaults={"name": "Prepared food"})
    count("tax_categories", c)
    for region, name, bps in TAX_RATES:
        count("tax_rates", TaxRate.objects.get_or_create(tax_category=food, region=region, name=name,
                                                         defaults={"rate_bps": bps})[1])
    preps = {}
    for code, name in [("OVEN", "Pizza oven"), ("FRYER", "Fryer"), ("COLD", "Cold / drinks")]:
        preps[code], c = PrepCategory.objects.get_or_create(code=code, defaults={"name": name})
        count("prep_categories", c)
    cats = {}
    for i, (code, name) in enumerate([("PIZZA", "Pizzas"), ("SIDES", "Sides"), ("DRINKS", "Drinks")], start=1):
        cats[code], c = Category.objects.get_or_create(code=code, defaults={"name": name, "sort_order": i})
        count("categories", c)
    crusts = []
    for i, (code, name, price) in enumerate([("REG", "Regular", 0), ("THIN", "Thin", 0),
                                             ("STUFF", "Cheese-stuffed", 300), ("GF", "Gluten-free", 400)]):
        crust, c = Crust.objects.get_or_create(code=code, defaults={"name": name, "price_cents": price, "sort_order": i})
        crusts.append(crust)
        count("crusts", c)
    toppings, c = ModifierGroup.objects.get_or_create(code="TOPPINGS", defaults={
        "name": "Toppings", "max_select": 10, "max_quantity_per_option": 2, "allow_half": True,
        "half_price_percent": 50, "scale_with_size": True})
    count("modifier_groups", c)
    for i, (code, name, price) in enumerate(TOPPINGS):
        count("modifiers", Modifier.objects.get_or_create(code=code, defaults={
            "group": toppings, "name": name, "price_cents": price, "is_topping": True, "sort_order": i})[1])
    sauce, c = ModifierGroup.objects.get_or_create(code="SAUCE", defaults={"name": "Sauce", "max_select": 1})
    count("modifier_groups", c)
    for i, (code, name) in enumerate([("SAU-TOM", "Tomato"), ("SAU-BBQ", "BBQ"), ("SAU-ALF", "Alfredo")]):
        count("modifiers", Modifier.objects.get_or_create(code=code, defaults={"group": sauce, "name": name,
                                                                               "sort_order": i})[1])
    dips, c = ModifierGroup.objects.get_or_create(code="DIPS", defaults={"name": "Dips", "max_select": 4,
                                                                        "max_quantity_per_option": 3})
    count("modifier_groups", c)
    for i, (code, name) in enumerate([("DIP-GAR", "Garlic"), ("DIP-RAN", "Ranch"), ("DIP-BLU", "Blue cheese")]):
        count("modifiers", Modifier.objects.get_or_create(code=code, defaults={"group": dips, "name": name,
                                                                               "price_cents": 99, "sort_order": i})[1])
    return {"created": created, "food": food, "preps": preps, "categories": cats, "crusts": crusts,
            "groups": {"TOPPINGS": toppings, "SAUCE": sauce, "DIPS": dips}}
