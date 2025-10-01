# backend/image_providers/provider_factory.py
from typing import Dict, Any
from .base_provider import ImageProvider
from .gee_provider import GEEProvider
from .maxar_provider import MaxarProvider
from .local_provider import LocalProvider

PROVIDER_MAP = {
    "gee": GEEProvider,
    "maxar": MaxarProvider,
    "local": LocalProvider
}

def get_provider(provider_name: str, credentials: Dict[str, Any]) -> ImageProvider:
    """
    Factory function to get an instance of an image provider.
    """
    provider_class = PROVIDER_MAP.get(provider_name.lower())
    if not provider_class:
        raise ValueError(f"Unknown image provider: '{provider_name}'. Available: {list(PROVIDER_MAP.keys())}")

    return provider_class(credentials)