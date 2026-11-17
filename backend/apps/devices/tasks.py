from celery import shared_task

from .services import detect_offline_devices, prune_heartbeats


@shared_task
def monitor_device_heartbeats():
    return detect_offline_devices()


@shared_task
def prune_device_heartbeats():
    return prune_heartbeats()
