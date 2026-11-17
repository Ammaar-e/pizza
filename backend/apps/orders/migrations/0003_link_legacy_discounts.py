from django.db import migrations


def forwards(apps, schema_editor):
    OrderDiscount = apps.get_model("orders", "OrderDiscount")
    Code = apps.get_model("promotions", "PromotionCode")
    for od in OrderDiscount.objects.filter(discount__isnull=False).select_related("discount"):
        c = Code.objects.filter(code=od.discount.code.upper()).first()
        if c:
            od.promotion_id = c.promotion_id
            od.save(update_fields=["promotion"])


class Migration(migrations.Migration):
    dependencies = [("orders", "0002_promotions_and_line_discounts"), ("promotions", "0002_from_legacy_discounts")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
