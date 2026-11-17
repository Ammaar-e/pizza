from django.urls import path

from .consumers import DevicesFeedConsumer, DispatchFeedConsumer, DriverFeedConsumer, KDSFeedConsumer, POSFeedConsumer

websocket_urlpatterns = [
    path("ws/stores/<int:store_id>/pos/", POSFeedConsumer.as_asgi()),
    path("ws/stores/<int:store_id>/kds/", KDSFeedConsumer.as_asgi()),
    path("ws/stores/<int:store_id>/dispatch/", DispatchFeedConsumer.as_asgi()),
    path("ws/driver/", DriverFeedConsumer.as_asgi()),
    path("ws/devices/", DevicesFeedConsumer.as_asgi()),
]
