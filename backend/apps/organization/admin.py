from django.contrib import admin

from . import models

admin.site.register(models.Headquarters)
admin.site.register(models.Franchisee)


class HoursInline(admin.TabularInline):
    model = models.StoreHours
    extra = 0


@admin.register(models.Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "franchisee", "city", "is_active")
    list_filter = ("franchisee", "is_active")
    inlines = [HoursInline]
    exclude = ("payment_secrets",)


@admin.register(models.Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "device_type", "is_active", "last_seen_at")
    exclude = ("token_hash",)
