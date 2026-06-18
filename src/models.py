"""Estructura común de un producto, independiente de la tienda."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Product:
    store: str                      # "coppel", "amazon", ...
    name: str
    url: str
    price: float                    # precio actual (lo que pagas)
    list_price: Optional[float] = None   # precio de lista / antes
    ean: Optional[str] = None       # código de barras (para cruce entre tiendas)
    brand: Optional[str] = None
    available: bool = True
    currency: str = "MXN"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def discount_pct(self) -> Optional[float]:
        """Descuento sobre el precio de lista, en porcentaje (0-100)."""
        if not self.list_price or self.list_price <= 0:
            return None
        if self.price <= 0 or self.price > self.list_price:
            return None
        return round((1 - self.price / self.list_price) * 100, 1)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["discount_pct"] = self.discount_pct
        return d
