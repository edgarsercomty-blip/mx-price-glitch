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
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .adapters.base import StoreAdapter
from .detect import Finding, is_refurbished
from .models import Product

# palabras vacías / atributos que no identifican el producto
_STOP = set((
    "para con sin de del la el los las un una y o en por al su mas plus pro "
    "color negro blanco azul rojo gris verde rosa dorado plata plateado "
    "talla chico mediano grande chica modelo nuevo edicion set kit pack pza "
    "pzas piezas pieza cm mm pulgadas pulgada lts lt ml kg gr gramos litros "
    "hombre mujer unisex nino nina paquete incluye"
).split())


def _norm(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _strip(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _tokens(s: str | None) -> set[str]:
    s = _strip(s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return {t for t in s.split() if len(t) > 2 and t not in _STOP}


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


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


def same_product(cand: Product, other: Product, min_overlap: float = 0.55) -> bool:
    """¿`cand` y `other` son el MISMO producto? Primero por código de modelo;
    si no hay, por marca igual + alto solape de tokens del título (cubre moda y
    genéricos donde no hay código de modelo)."""
    if cand.model and models_match(cand.model, other.model, other.name):
        return True
    if other.model and models_match(other.model, cand.model, cand.name):
        return True
    if (cand.brand and other.brand
            and _norm(cand.brand) == _norm(other.brand)
            and _overlap(cand.name, other.name) >= min_overlap):
        return True
    return False


@dataclass
class CrossResult:
    finding: Finding
    others: list[Product]      # mismas referencias halladas en otras tiendas
    cheapest: Product | None
    real_pct: float | None     # % por debajo de la oferta más barata de otra tienda


def _label(o: Product) -> str:
    if o.store == "google":
        m = (o.extra or {}).get("merchant")
        return f"google/{m} ${o.price:,.0f}" if m else f"google ${o.price:,.0f}"
    return f"{o.store} ${o.price:,.0f}"


def _query_for(p: Product) -> str | None:
    """Consulta para buscar el producto en otras tiendas: el modelo si existe;
    si no, marca + tokens distintivos del título (para moda/genéricos)."""
    if p.model and len(_norm(p.model)) >= 5:
        return p.model
    toks = sorted(_tokens(p.name), key=len, reverse=True)[:4]
    parts = ([p.brand] if p.brand else []) + toks
    q = " ".join(parts).strip()
    return q or None


def _cached_lookup(ad: StoreAdapter, store_key: str, query: str,
                   cache: dict, ttl: timedelta) -> list[Product]:
    """Lookup con caché persistente entre corridas (precio de la competencia
    cambia poco en horas; evita repetir la misma consulta de red cada corrida)."""
    ckey = f"{store_key}|{_norm(query)}"
    ent = cache.get(ckey)
    if ent:
        try:
            fresh = datetime.now(timezone.utc) - datetime.fromisoformat(ent["ts"]) < ttl
        except (KeyError, ValueError, TypeError):
            fresh = False
        if fresh:
            return [Product(**d) for d in ent.get("items", [])]
    try:
        hits = ad.lookup(query)
    except Exception:
        hits = []
    cache[ckey] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "items": [{"store": h.store, "name": h.name, "url": h.url,
                   "price": h.price, "model": h.model} for h in hits],
    }
    return hits


def verify(candidates: list[Finding], adapters: dict[str, StoreAdapter],
           confirm_pct: float, google=None, google_min_pct: float = 45,
           google_max_lookups: int = 15,
           lookup_cache: dict | None = None,
           lookup_ttl_hours: float = 12) -> list[Finding]:
    """Devuelve solo los candidatos confirmados más baratos que otra tienda,
    anotando en el detalle los precios de la competencia.

    Si `google` (GoogleShopping) está disponible, se usa como árbitro extra solo
    para los candidatos de mayor descuento propio (>= google_min_pct) y dentro
    del presupuesto google_max_lookups (consultas de red por corrida)."""
    # primero los de mayor descuento propio (para que el presupuesto de Google
    # se gaste en los más prometedores)
    candidates = sorted(candidates,
                        key=lambda f: f.product.discount_pct or 0, reverse=True)
    cache = lookup_cache if lookup_cache is not None else {}
    ttl = timedelta(hours=lookup_ttl_hours)
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
            for op in _cached_lookup(ad, key, query, cache, ttl):
                if (op.price > 0 and not is_refurbished(op.name)
                        and same_product(p, op)):
                    others.append(op)

        # árbitro extra: Google Shopping. Solo con código de modelo (puede
        # emparejar por título) y dentro del presupuesto de red por corrida.
        if (google is not None and p.model
                and (p.discount_pct or 0) >= google_min_pct):
            budget_left = google_max_lookups - google.calls
            for gp in google.lookup(query, budget_left):
                if (gp.price > 0 and not is_refurbished(gp.name)
                        and same_product(p, gp)):
                    others.append(gp)

        if not others:
            continue                       # no se encontró en otra tienda -> no confirmable
        cheapest = min(others, key=lambda x: x.price)
        real = round((1 - p.price / cheapest.price) * 100, 1)
        if real < confirm_pct:
            continue                       # no es más barato que el mercado -> descuento falso
        # último filtro por tienda (p. ej. Amazon: solo vendido/enviado por Amazon)
        own_ad = adapters.get(p.store)
        if own_ad is not None and not own_ad.confirm_report(p):
            continue
        comp = ", ".join(_label(o) for o in sorted(others, key=lambda x: x.price)[:3])
        f.kind = "cross_confirmed"
        f.discount_pct = real
        f.detail = (f"${p.price:,.0f} vs {comp} -> -{real:.0f}% bajo la "
                    f"competencia (descuento propio "
                    f"{f.product.discount_pct or 0:.0f}%)")
        confirmed.append(f)
    confirmed.sort(key=lambda x: x.discount_pct, reverse=True)
    return confirmed
