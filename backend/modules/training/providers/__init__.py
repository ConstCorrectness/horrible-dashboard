"""Provider registry: built-ins first, backend-plugin providers as fallback."""

from __future__ import annotations

from backend.modules.training.models import ProviderInfoModel
from backend.modules.training.providers.base import (
    EnvironmentProvider,
    FetchResult,
    ProviderError,
    ScaffoldResult,
)
from backend.modules.training.providers.gymnasium_provider import GymnasiumProvider
from backend.modules.training.providers.huggingface_provider import (
    HuggingFaceProvider,
)
from backend.modules.training.providers.kaggle_provider import KaggleProvider

_PROVIDERS: dict[str, EnvironmentProvider] = {
    p.provider: p
    for p in (KaggleProvider(), HuggingFaceProvider(), GymnasiumProvider())
}


def get_provider(provider_id: str) -> EnvironmentProvider:
    """A provider by id — built-ins first, then backend-plugin contributions."""
    found = _PROVIDERS.get(provider_id)
    if found is not None:
        return found
    from backend.sdk.registry import registry

    plugin_provider = registry.training_providers.get(provider_id)
    if plugin_provider is None:
        raise ProviderError(f"unknown environment provider: {provider_id}")
    return plugin_provider


def list_providers() -> list[ProviderInfoModel]:
    from backend.sdk.registry import registry

    every: list[EnvironmentProvider] = [
        *_PROVIDERS.values(),
        *registry.training_providers.values(),
    ]
    return [
        ProviderInfoModel(provider=p.provider, label=p.label, kinds=list(p.kinds))
        for p in every
    ]


__all__ = [
    "EnvironmentProvider",
    "FetchResult",
    "ProviderError",
    "ScaffoldResult",
    "get_provider",
    "list_providers",
]
