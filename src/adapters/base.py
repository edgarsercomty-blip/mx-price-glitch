"""Interfaz común de los adaptadores de tienda."""
from __future__ import annotations

from typing import Iterable

from ..models import Product


class StoreAdapter:
    """Cada tienda implementa scan() devolviendo productos con precio.

    `key`     identificador corto (coppel, amazon, ...).
    `name`    nombre legible.
    `quality` 'solid' (API estructurada, descuento confiable) o
              'best_effort' (HTML/JSON-LD, puede faltar precio de lista).
    """

    key: str = ""
    name: str = ""
    quality: str = "best_effort"

    def __init__(self, config: dict):
        self.config = config

    def scan(self) -> Iterable[Product]:
        raise NotImplementedError
