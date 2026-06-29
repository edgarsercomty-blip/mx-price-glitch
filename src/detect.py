"""Lógica de detección de errores/chollos.

Dos señales, según lo acordado ("ambos"):

1) Descuento propio: el precio actual es >= UMBRAL% menor que el precio de
   lista que la MISMA tienda muestra. Es la señal más limpia para errores.

2) Cruce entre tiendas: agrupa productos por EAN; si la oferta más barata de
   una tienda está >= UMBRAL% por debajo de la mediana del resto, se marca.
   Solo aplica a productos con EAN (principalmente VTEX).
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Iterable

from .models import Product

DEFAULT_THRESHOLD = 50.0  # % de diferencia mínima
DEFAULT_MAX = 99.0        # % máximo: por encima suele ser error de dato, no chollo

# Productos que NO son nuevos: reacondicionado, open box, usado, etc. Se excluyen
# porque su precio bajo no es un error/chollo comparable contra producto nuevo.
_REFURB = re.compile(
    r"(reacondicionad\w*|reacond\b|refurb\w*|renewed|renovad\w*|"
    r"open[\s\-]?box|caja abierta|semi[\s\-]?nuev\w*|remanufactur\w*|"
    r"segunda mano|\busad[oa]s?\b|\bused\b)", re.I)


def is_refurbished(name: str | None) -> bool:
    return bool(name and _REFURB.search(name))


@dataclass
class Finding:
    kind: str               # "own_discount" | "cross_store" | "cross_confirmed" | "own_price_drop"
    product: Product
    discount_pct: float
    detail: str
    ref_price: float | None = None   # precio de referencia del mercado (comparable más barato)
    n_comparables: int = 0           # cuántos comparables se hallaron (robustez)
    store_comparables: int = 0       # de esos, cuántos son de TIENDA real (no Google)
    likely_typo: bool = False        # precio ~1/10 del de referencia: probable error de captura

    @property
    def savings(self) -> float | None:
        """Ahorro absoluto en pesos vs la referencia de mercado."""
        if self.ref_price and self.ref_price > self.product.price:
            return round(self.ref_price - self.product.price, 2)
        return None

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "discount_pct": self.discount_pct,
             "detail": self.detail, "likely_typo": self.likely_typo,
             "savings": self.savings}
        d.update(self.product.to_dict())
        return d


# Un precio entre ~8x y ~12x más barato que su referencia suele ser un error de
# captura (se cayó un dígito: $1,499 en vez de $14,999). Señal de máxima prioridad.
TYPO_MIN_RATIO = 8.0
TYPO_MAX_RATIO = 12.0


def is_price_typo(ref_price: float | None, price: float | None) -> bool:
    if not ref_price or not price or price <= 0:
        return False
    ratio = ref_price / price
    return TYPO_MIN_RATIO <= ratio <= TYPO_MAX_RATIO


def own_discount(products: Iterable[Product], threshold: float,
                 max_pct: float = DEFAULT_MAX) -> list[Finding]:
    out = []
    for p in products:
        d = p.discount_pct
        if d is not None and threshold <= d <= max_pct and p.available:
            out.append(Finding(
                kind="own_discount", product=p, discount_pct=d,
                detail=(f"${p.price:,.0f} vs lista ${p.list_price:,.0f} "
                        f"(-{d:.0f}%)"),
            ))
    return out


def cross_store(products: Iterable[Product], threshold: float,
                max_pct: float = DEFAULT_MAX, min_others: int = 2) -> list[Finding]:
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
        if threshold <= diff <= max_pct:
            out.append(Finding(
                kind="cross_store", product=cheapest, discount_pct=diff,
                detail=(f"EAN {ean}: ${cheapest.price:,.0f} en {cheapest.store} "
                        f"vs mediana ${ref:,.0f} ({len(stores)} tiendas) "
                        f"(-{diff:.0f}%)"),
            ))
    return out


def own_price_drop(products: Iterable[Product], baseline_fn, threshold: float,
                   max_pct: float = DEFAULT_MAX) -> list[Finding]:
    """Señal independiente del cruce entre tiendas: el precio actual está muy por
    debajo de la mediana HISTÓRICA del propio producto (de price_history.json).

    Captura glitches de productos ÚNICOS que no tienen comparable en otras
    tiendas ni precio de lista — solo se podían ver con el histórico propio.
    `baseline_fn(product)` devuelve la mediana histórica o None."""
    out: list[Finding] = []
    for p in products:
        if p.price <= 0 or not p.available:
            continue
        base = baseline_fn(p)
        if not base or base <= 0 or base <= p.price:
            continue
        drop = round((1 - p.price / base) * 100, 1)
        if not (threshold <= drop <= max_pct):
            continue
        typo = is_price_typo(base, p.price)
        out.append(Finding(
            kind="own_price_drop", product=p, discount_pct=drop,
            ref_price=base, n_comparables=1, store_comparables=1,
            likely_typo=typo,
            detail=(f"{'🚨 PROBABLE ERROR DE CAPTURA: ' if typo else ''}"
                    f"${p.price:,.0f} vs su histórico ${base:,.0f} (-{drop:.0f}%)"),
        ))
    return out


def detect(products: list[Product], threshold: float = DEFAULT_THRESHOLD,
           max_pct: float = DEFAULT_MAX) -> list[Finding]:
    findings = (own_discount(products, threshold, max_pct)
                + cross_store(products, threshold, max_pct))
    # ordena por mayor descuento primero
    findings.sort(key=lambda f: f.discount_pct, reverse=True)
    return findings
