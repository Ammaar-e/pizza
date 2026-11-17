from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("orders", "0003_link_legacy_discounts")]
    operations = [migrations.RemoveField(model_name="orderdiscount", name="discount")]
