from django.urls import path

from .consumers import KDSFeedConsumer, POSFeedConsumer

websocket_urlpatterns = [
    path("ws/stores/<int:store_id>/pos/", POSFeedConsumer.as_asgi()),
    path("ws/stores/<int:store_id>/kds/", KDSFeedConsumer.as_asgi()),
]
