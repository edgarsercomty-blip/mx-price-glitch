"""Verificación cruzada entre tiendas (sin EAN, por modelo del fabricante).

Problema: el "precio de lista" que muestra una tienda es del MISMO vendedor y
suele estar inflado -> el descuento propio no prueba que sea barato de verdad.

Solución: para cada candidato (con buen descuento propio), buscamos el MISMO
modelo en las OTRAS tiendas y comparamos el precio actual. Solo se reporta si el
candidato está de verdad más barato que el mercado (>= confirm_pct por debajo de
la oferta más barata encontrada en otra tienda). Así se descartan los descuentos
falsos del propio vendedor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .adapters.base import StoreAdapter
from .detect import Finding
from .models import Product


def _norm(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def models_match(a: str | None, b_model: str | None, b_name: str | None) -> bool:
    """¿El modelo `a` corresponde al producto B (por su modelo o su nombre)?"""
    na = _norm(a)
    if len(na) < 5:
        return False
    for cand in (_norm(b_model), _norm(b_name)):
        if not cand:
            continue
        if na == cand or (len(na) >= 6 and na in cand):
            return True
    return False


@dataclass
class CrossResult:
    finding: Finding
    others: list[Product]      # mismas referencias halladas en otras tiendas
    cheapest: Product | None
    real_pct: float | None     # % por debajo de la oferta más barata de otra tienda


def _query_for(p: Product) -> str | None:
    model = p.model
    if model and len(_norm(model)) >= 5:
        return model
    return None


def verify(candidates: list[Finding], adapters: dict[str, StoreAdapter],
           confirm_pct: float) -> list[Finding]:
    """Devuelve solo los candidatos confirmados más baratos que otra tienda,
    anotando en el detalle los precios de la competencia."""
    confirmed: list[Finding] = []
    for f in candidates:
        p = f.product
        query = _query_for(p)
        if not query:
            continue                       # sin modelo no se puede cruzar
        others: list[Product] = []
        for key, ad in adapters.items():
            if key == p.store:
                continue
            try:
                hits = ad.lookup(query)
            except Exception:
                hits = []
            for op in hits:
                if op.price > 0 and models_match(query, op.model, op.name):
                    others.append(op)
        if not others:
            continue                       # no se encontró en otra tienda -> no confirmable
        cheapest = min(others, key=lambda x: x.price)
        real = round((1 - p.price / cheapest.price) * 100, 1)
        if real < confirm_pct:
            continue                       # no es más barato que el mercado -> descuento falso
        comp = ", ".join(f"{o.store} ${o.price:,.0f}"
                         for o in sorted(others, key=lambda x: x.price)[:3])
        f.kind = "cross_confirmed"
        f.discount_pct = real
        f.detail = (f"${p.price:,.0f} vs {comp} -> -{real:.0f}% bajo la "
                    f"competencia (descuento propio "
                    f"{f.product.discount_pct or 0:.0f}%)")
        confirmed.append(f)
    confirmed.sort(key=lambda x: x.discount_pct, reverse=True)
    return confirmed
