from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.orders.realtime import store_group


class StoreFeedConsumer(AsyncJsonWebsocketConsumer):
    """Real-time feed for a store. kind = 'pos' (order events) or 'kds' (kitchen tickets)."""

    kind = "pos"
    required_permission = "orders.view"

    async def connect(self):
        self.store_id = int(self.scope["url_route"]["kwargs"]["store_id"])
        user = self.scope.get("user")
        if not user or not user.is_authenticated or not await self._allowed(user):
            await self.close(code=4403)
            return
        self.group = store_group(self.store_id, self.kind)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_json({"event": "connected", "store_id": self.store_id, "feed": self.kind})

    async def disconnect(self, code):
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"event": "pong"})

    async def push(self, event):
        await self.send_json(event["payload"])

    @database_sync_to_async
    def _allowed(self, user) -> bool:
        from apps.accounts.services import user_can_operate_store
        from apps.organization.models import Store

        store = Store.objects.filter(pk=self.store_id).first()
        pinned = self.scope.get("pos_store_id")
        if store is None or (pinned and pinned != store.id):
            return False
        return user.has_permission(self.required_permission) and user_can_operate_store(user, store)


class POSFeedConsumer(StoreFeedConsumer):
    kind = "pos"
    required_permission = "orders.view"


class KDSFeedConsumer(StoreFeedConsumer):
    kind = "kds"
    required_permission = "kitchen.access"
