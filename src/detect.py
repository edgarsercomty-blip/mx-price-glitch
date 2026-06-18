"""Lógica de detección de errores/chollos.

Dos señales, según lo acordado ("ambos"):

1) Descuento propio: el precio actual es >= UMBRAL% menor que el precio de
   lista que la MISMA tienda muestra. Es la señal más limpia para errores.

2) Cruce entre tiendas: agrupa productos por EAN; si la oferta más barata de
   una tienda está >= UMBRAL% por debajo de la mediana del resto, se marca.
   Solo aplica a productos con EAN (principalmente VTEX).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable

from .models import Product

DEFAULT_THRESHOLD = 50.0  # % de diferencia mínima


@dataclass
class Finding:
    kind: str               # "own_discount" | "cross_store"
    product: Product
    discount_pct: float
    detail: str

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "discount_pct": self.discount_pct,
             "detail": self.detail}
        d.update(self.product.to_dict())
        return d


def own_discount(products: Iterable[Product], threshold: float) -> list[Finding]:
    out = []
    for p in products:
        d = p.discount_pct
        if d is not None and d >= threshold and p.available:
            out.append(Finding(
                kind="own_discount", product=p, discount_pct=d,
                detail=(f"${p.price:,.0f} vs lista ${p.list_price:,.0f} "
                        f"(-{d:.0f}%)"),
            ))
    return out


def cross_store(products: Iterable[Product], threshold: float,
                min_others: int = 2) -> list[Finding]:
    by_ean: dict[str, list[Product]] = {}
    for p in products:
        if p.ean and p.available and p.price > 0:
            by_ean.setdefault(p.ean, []).append(p)

    out = []
    for ean, group in by_ean.items():
        # necesitamos varias tiendas distintas para comparar
        stores = {p.store for p in group}
        if len(stores) < (min_others + 1):
            continue
        cheapest = min(group, key=lambda x: x.price)
        others = [p.price for p in group if p is not cheapest]
        if len(others) < min_others:
            continue
        ref = statistics.median(others)
        if ref <= 0:
            continue
        diff = round((1 - cheapest.price / ref) * 100, 1)
        if diff >= threshold:
            out.append(Finding(
                kind="cross_store", product=cheapest, discount_pct=diff,
                detail=(f"EAN {ean}: ${cheapest.price:,.0f} en {cheapest.store} "
                        f"vs mediana ${ref:,.0f} ({len(stores)} tiendas) "
                        f"(-{diff:.0f}%)"),
            ))
    return out


def detect(products: list[Product], threshold: float = DEFAULT_THRESHOLD
           ) -> list[Finding]:
    findings = own_discount(products, threshold) + cross_store(products, threshold)
    # ordena por mayor descuento primero
    findings.sort(key=lambda f: f.discount_pct, reverse=True)
    return findings
