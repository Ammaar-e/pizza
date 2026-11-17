from django.db import migrations


def forwards(apps, schema_editor):
    """Convert legacy catalog.Discount codes into code-based promotions (same code, value, owner)."""
    Discount = apps.get_model("catalog", "Discount")
    Promotion = apps.get_model("promotions", "Promotion")
    Code = apps.get_model("promotions", "PromotionCode")
    for d in Discount.objects.all():
        p = Promotion.objects.create(
            name=d.name, description="Migrated from legacy discount", promo_type=d.discount_type,
            franchisee_id=d.franchisee_id, value=d.value, max_discount_cents=d.max_amount_cents, requires_code=True,
            auto_apply=False, requires_manager=d.requires_manager, is_enabled=d.is_active,
            status="active" if d.is_active else "disabled")
        code = d.code.upper()
        if d.franchisee_id and Code.objects.filter(code=code).exists():
            code = f"{code}-{d.franchisee_id}"
        Code.objects.create(promotion=p, code=code)
    if Promotion.objects.filter(franchisee__isnull=False).exists() or Discount.objects.exists():
        Policy = apps.get_model("promotions", "FranchisePromotionPolicy")
        for fid in set(Discount.objects.exclude(franchisee=None).values_list("franchisee_id", flat=True)):
            Policy.objects.get_or_create(franchisee_id=fid, defaults={"allowed_types": ["percent", "fixed"]})


class Migration(migrations.Migration):
    dependencies = [("promotions", "0001_initial"), ("catalog", "0002_initial")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
