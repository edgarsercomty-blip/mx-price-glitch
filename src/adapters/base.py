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

    def lookup(self, query: str) -> list["Product"]:
        """Busca productos por texto libre (p. ej. un modelo) y devuelve sus
        precios ACTUALES. Lo usa la verificación cruzada entre tiendas.
        Por defecto vacío; cada tienda lo implementa si puede."""
        return []

    def confirm_report(self, product: "Product") -> bool:
        """Último filtro antes de reportar un hallazgo de ESTA tienda.
        Por defecto siempre OK; tiendas como Amazon lo usan para exigir que el
        producto sea vendido/enviado por la propia tienda. Puede hacer red, así
        que solo se llama para los pocos hallazgos ya confirmados."""
        return True
