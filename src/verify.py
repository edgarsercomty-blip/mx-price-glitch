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


def guard_costly(findings: list[Finding], guard_adapters: dict[str, StoreAdapter],
                 confirm_pct: float, cache: dict, ttl_hours: float = 48) -> list[Finding]:
    """Última verificación de los confirmados contra tiendas 'costosas'
    (Amazon/Walmart/Sam's): busca el MISMO producto ahí; si lo tienen igual o
    más barato (la diferencia real cae por debajo de confirm_pct), se descarta.
    Solo corre sobre los pocos confirmados, así que casi no gasta."""
    if not guard_adapters or not findings:
        return findings
    ttl = timedelta(hours=ttl_hours)
    kept: list[Finding] = []
    for f in findings:
        p = f.product
        query = _query_for(p)
        rivals: list[Product] = []
        for key, ad in guard_adapters.items():
            if key == p.store:
                continue
            for op in _cached_lookup(ad, key, query, cache, ttl):
                if (op.price > 0 and not is_refurbished(op.name)
                        and same_product(p, op)):
                    rivals.append(op)

        # #2: un solo comparable (sin corroborar por tiendas costosas) no es
        # confiable -> un listado erróneo puede inflar el descuento (caso #8).
        if f.n_comparables <= 1 and not rivals:
            print(f"   [guard] descartado (1 solo comparable, sin corroborar): {p.name[:40]}")
            continue

        # #1: junta TODAS las ofertas conocidas y recalcula contra el competidor
        # más barato real. El "ganador" (más barato) es el deal; el descuento es
        # vs el segundo más barato.
        offers: list[tuple[float, Product | None]] = [(p.price, p)]
        offers += [(r.price, r) for r in rivals]
        if f.ref_price:
            offers.append((f.ref_price, None))   # piso de mercado de verify (sin Product)
        offers.sort(key=lambda o: o[0])
        winner_price, winner = offers[0]
        floor_price = offers[1][0]               # segundo más barato = competencia real
        disc = round((1 - winner_price / floor_price) * 100, 1)

        if winner is None or disc < confirm_pct:
            print(f"   [guard] descartado: {p.name[:40]} -> real {disc:+.0f}% (< {confirm_pct:.0f}%)")
            continue
        # filtro por tienda del ganador (Amazon: vendido/enviado por Amazon)
        wad = guard_adapters.get(winner.store)
        if wad is not None and not wad.confirm_report(winner):
            continue

        comp = ", ".join(_label(o[1]) for o in offers[1:4] if o[1] is not None)
        if winner.store != p.store:
            print(f"   [guard] volteado a {winner.store} (${winner_price:,.0f}, -{disc:.0f}%): {p.name[:40]}")
        f2 = Finding(
            kind="cross_confirmed", product=winner, discount_pct=disc,
            ref_price=floor_price, n_comparables=len(offers) - 1,
            detail=(f"${winner_price:,.0f} en {winner.store} vs {comp or 'mercado'} "
                    f"-> -{disc:.0f}% bajo la competencia"))
        kept.append(f2)
    return kept


class _Pool:
    """Índice en memoria de los productos ya escaneados, para comparar candidatos
    SIN consultas de red. Por código de modelo (exacto) y por marca+tokens."""

    def __init__(self, products: list[Product]):
        self.by_model: dict[str, list[Product]] = {}
        self.by_brand: dict[str, list[Product]] = {}
        self._tok: dict[int, set[str]] = {}
        for p in products:
            if p.price <= 0:
                continue
            mn = _norm(p.model)
            if len(mn) >= 5:
                self.by_model.setdefault(mn, []).append(p)
            bn = _norm(p.brand)
            if bn:
                self.by_brand.setdefault(bn, []).append(p)
                self._tok[id(p)] = _tokens(p.name)

    def matches(self, cand: Product, min_overlap: float = 0.55) -> list[Product]:
        out: list[Product] = []
        seen: set[int] = set()
        cmn = _norm(cand.model)
        if len(cmn) >= 5:
            for p in self.by_model.get(cmn, []):
                if p.store != cand.store and id(p) not in seen:
                    seen.add(id(p)); out.append(p)
        cbn = _norm(cand.brand)
        if cbn:
            ctok = _tokens(cand.name)
            if ctok:
                for p in self.by_brand.get(cbn, []):
                    if p.store == cand.store or id(p) in seen:
                        continue
                    pt = self._tok.get(id(p)) or _tokens(p.name)
                    if pt and len(ctok & pt) / len(ctok | pt) >= min_overlap:
                        seen.add(id(p)); out.append(p)
        return out


def verify(candidates: list[Finding], adapters: dict[str, StoreAdapter],
           confirm_pct: float, pool: list[Product] | None = None,
           google=None, google_min_pct: float = 45,
           google_max_lookups: int = 15,
           lookup_cache: dict | None = None,
           lookup_ttl_hours: float = 48,
           net_fallback: int = 40) -> list[Finding]:
    """Confirma candidatos más baratos que el mercado. Estrategia:
    1) comparar contra el POOL ya escaneado (gratis, en memoria);
    2) Google Shopping como árbitro (con modelo, presupuesto);
    3) red como respaldo ACOTADO (`net_fallback` candidatos sin match en pool)."""
    candidates = sorted(candidates,
                        key=lambda f: f.product.discount_pct or 0, reverse=True)
    idx = _Pool(pool or [])
    cache = lookup_cache if lookup_cache is not None else {}
    ttl = timedelta(hours=lookup_ttl_hours)
    confirmed: list[Finding] = []
    n_matched = 0
    net_used = 0
    best_diffs: list[tuple[float, str]] = []
    for f in candidates:
        p = f.product
        query = _query_for(p)
        if not query:
            continue
        # 1) pool en memoria (gratis)
        others: list[Product] = [op for op in idx.matches(p)
                                 if not is_refurbished(op.name)]

        # 2) Google Shopping (con modelo, presupuesto)
        if (google is not None and p.model
                and (p.discount_pct or 0) >= google_min_pct):
            budget_left = google_max_lookups - google.calls
            for gp in google.lookup(query, budget_left):
                if (gp.price > 0 and not is_refurbished(gp.name)
                        and same_product(p, gp)):
                    others.append(gp)

        # 3) respaldo de red acotado: solo si no hubo match y tiene modelo
        if not others and p.model and net_used < net_fallback:
            net_used += 1
            for key, ad in adapters.items():
                if key == p.store:
                    continue
                for op in _cached_lookup(ad, key, query, cache, ttl):
                    if (op.price > 0 and not is_refurbished(op.name)
                            and same_product(p, op)):
                        others.append(op)

        if not others:
            continue
        n_matched += 1
        cheapest = min(others, key=lambda x: x.price)
        real = round((1 - p.price / cheapest.price) * 100, 1)
        best_diffs.append((real, f"{p.store} {p.name[:40]} vs {_label(cheapest)} = {real:+.0f}%"))
        if real < confirm_pct:
            continue                       # no es más barato que el mercado -> descuento falso
        # último filtro por tienda (p. ej. Amazon: solo vendido/enviado por Amazon)
        own_ad = adapters.get(p.store)
        if own_ad is not None and not own_ad.confirm_report(p):
            continue
        comp = ", ".join(_label(o) for o in sorted(others, key=lambda x: x.price)[:3])
        f.kind = "cross_confirmed"
        f.discount_pct = real
        f.ref_price = cheapest.price          # referencia de mercado para la guardia
        f.n_comparables = len(others)
        f.detail = (f"${p.price:,.0f} vs {comp} -> -{real:.0f}% bajo la "
                    f"competencia (descuento propio "
                    f"{f.product.discount_pct or 0:.0f}%)")
        confirmed.append(f)

    # diagnóstico: ayuda a entender por qué hay (o no) confirmados
    print(f"   [verify] candidatos={len(candidates)} con_comparable={n_matched} "
          f"confirmados={len(confirmed)} (red usada={net_used})")
    for diff, label in sorted(best_diffs, reverse=True)[:8]:
        print(f"   [verify] mejor: {label}")

    confirmed.sort(key=lambda x: x.discount_pct, reverse=True)
    return confirmed
