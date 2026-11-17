from django.db import migrations

NEW = {
    "devices.view", "devices.administer", "devices.approve", "devices.configure_profiles", "devices.local_settings",
    "cash.approve", "cash.correct", "cash.review", "promotions.view", "promotions.manage_network",
    "promotions.manage_franchise", "promotions.manage_store", "promotions.apply_code", "dispatch.view",
    "dispatch.manage", "drivers.manage",
}


def seed(apps, schema_editor):
    """Add the permissions introduced by the cash, promotions, dispatch and device modules and grant
    them to the built-in roles (existing grants are left untouched)."""
    from apps.accounts.permissions_catalog import PERMISSIONS, ROLES

    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")
    perms = {}
    for code, desc in PERMISSIONS.items():
        perms[code], _ = Permission.objects.update_or_create(code=code, defaults={"description": desc})
    for code, (_name, _level, granted) in ROLES.items():
        role = Role.objects.filter(code=code).first()
        if role:
            role.permissions.add(*[perms[c] for c in granted & NEW])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_seed_roles")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
