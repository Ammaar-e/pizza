from django.db import migrations


def seed(apps, schema_editor):
    from apps.accounts.permissions_catalog import PERMISSIONS, ROLES

    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")
    perms = {}
    for code, desc in PERMISSIONS.items():
        perms[code], _ = Permission.objects.update_or_create(code=code, defaults={"description": desc})
    for code, (name, level, granted) in ROLES.items():
        role, created = Role.objects.get_or_create(code=code, defaults={"name": name, "level": level})
        if created:
            role.permissions.set([perms[c] for c in granted])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
