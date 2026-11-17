from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("payments", "0003_cash_ledger_backfill")]
    operations = [migrations.DeleteModel(name="CashMovement")]
