from __future__ import annotations

from .base import ProviderAdapter
from .doordash import DoorDashAdapter
from .skip import SkipAdapter
from .ubereats import UberEatsAdapter

ADAPTERS: dict[str, type[ProviderAdapter]] = {
    UberEatsAdapter.code: UberEatsAdapter,
    DoorDashAdapter.code: DoorDashAdapter,
    SkipAdapter.code: SkipAdapter,
}


def get_adapter(provider) -> ProviderAdapter:
    cls = ADAPTERS.get(provider.code)
    if cls is None:
        raise KeyError(f"No adapter for provider '{provider.code}'")
    return cls(provider)
