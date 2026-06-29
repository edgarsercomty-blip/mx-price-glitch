"""Registro de adaptadores: construye el adaptador correcto desde la config."""
from __future__ import annotations

from .amazon import AmazonAdapter
from .base import StoreAdapter
from .coppel import CoppelAdapter
from .homedepot import HomeDepotAdapter
from .jsonld import JsonLdAdapter
from .liverpool import LiverpoolAdapter
from .palacio import PalacioAdapter
from .sams import SamsAdapter
from .vtex import VtexAdapter
from .walmart import WalmartAdapter

_TYPES = {
    "vtex": VtexAdapter,
    "jsonld": JsonLdAdapter,
    "homedepot": HomeDepotAdapter,
    "liverpool": LiverpoolAdapter,
    "walmart": WalmartAdapter,
    "amazon": AmazonAdapter,
    "palacio": PalacioAdapter,
    "sams": SamsAdapter,
    "coppel": CoppelAdapter,
}


def build_adapter(store_key: str, config: dict) -> StoreAdapter:
    cfg = dict(config)
    cfg["key"] = store_key
    kind = cfg.get("type")
    if kind not in _TYPES:
        raise ValueError(
            f"Tienda '{store_key}': type '{kind}' desconocido. "
            f"Usa uno de: {', '.join(_TYPES)}"
        )
    return _TYPES[kind](cfg)
