from django.db import models

from apps.core.models import TimeStampedModel


class KitchenStation(TimeStampedModel):
    store = models.ForeignKey("organization.Store", on_delete=models.CASCADE, related_name="kitchen_stations")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    prep_categories = models.JSONField(default=list, blank=True,
                                       help_text="Prep category codes routed here; empty = all items")
    is_expo = models.BooleanField(default=False, help_text="Expo/expeditor station sees whole orders")
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        unique_together = [("store", "code")]
        ordering = ["store", "sort_order"]

    def __str__(self):
        return f"{self.store.number}:{self.code}"


class TicketStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PREPARING = "preparing", "Preparing"
    READY = "ready", "Ready"
    COMPLETED = "completed", "Completed"
    VOID = "void", "Void"


class KitchenTicket(TimeStampedModel):
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="kitchen_tickets")
    store = models.ForeignKey("organization.Store", on_delete=models.CASCADE, related_name="kitchen_tickets")
    station = models.ForeignKey(KitchenStation, on_delete=models.CASCADE, related_name="tickets")
    status = models.CharField(max_length=20, choices=TicketStatus.choices, default=TicketStatus.RECEIVED,
                              db_index=True)
    items = models.JSONField(default=list)
    received_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    bumped_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = [("order", "station")]
        indexes = [models.Index(fields=["store", "status", "received_at"])]
