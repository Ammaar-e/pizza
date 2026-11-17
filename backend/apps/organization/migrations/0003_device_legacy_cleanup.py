import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    """After legacy device tokens were moved to devices.DeviceCredential (devices 0002)."""

    dependencies = [("organization", "0002_device_management"), ("devices", "0002_legacy_device_tokens")]

    operations = [
        migrations.RemoveField(model_name="device", name="is_active"),
        migrations.RemoveField(model_name="device", name="token_hash"),
        migrations.RemoveField(model_name="device", name="token_prefix"),
        migrations.AlterField(
            model_name="device", name="uid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, help_text="Unique device identifier",
                                   unique=True)),
    ]
