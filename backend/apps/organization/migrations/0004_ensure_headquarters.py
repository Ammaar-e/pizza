from django.db import migrations


def ensure_headquarters(apps, schema_editor):
    """Fresh installations set up with `createsuperuser` (no demo seed) had no Headquarters row, so creating a
    franchisee failed with a database error. Create the organisation root if it is missing."""
    Headquarters = apps.get_model("organization", "Headquarters")
    if not Headquarters.objects.exists():
        Headquarters.objects.create(name="Headquarters")


class Migration(migrations.Migration):
    dependencies = [("organization", "0003_device_legacy_cleanup")]
    operations = [migrations.RunPython(ensure_headquarters, migrations.RunPython.noop)]
