import uuid

from django.db import migrations


def forwards(apps, schema_editor):
    """Give every existing device a unique id, a status, and move its legacy token hash into a
    DeviceCredential so registered terminals keep working after the upgrade."""
    Device = apps.get_model("organization", "Device")
    Credential = apps.get_model("devices", "DeviceCredential")
    for d in Device.objects.all():
        d.uid = uuid.uuid4()
        d.status = "active" if d.is_active else "deactivated"
        d.save(update_fields=["uid", "status"])
        if d.token_hash:
            Credential.objects.create(device=d, key_id=f"legacy{d.pk:010d}"[:16], secret_hash=d.token_hash,
                                      revoked_at=None if d.is_active else d.updated_at,
                                      revoked_reason="" if d.is_active else "device revoked before upgrade")


class Migration(migrations.Migration):
    dependencies = [("devices", "0001_initial"), ("organization", "0002_device_management")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
