"""Adaptador Coppel (Next.js, vía Bright Data Web Unlocker).

Coppel bloquea acceso directo con WAF, por lo que siempre se descarga vía
Bright Data. La página de búsqueda embebe productos en __NEXT_DATA__ bajo
diversas rutas según la versión del frontend.

URL de búsqueda: https://www.coppel.com/busqueda?texto={TERM}

Config (stores.yaml):
  type: coppel
  search_terms: [refrigerador, lavadora, ...]
  max_products_per_term: 40
"""
from __future__ import annotations

import json
import re
from typing import Iterable
from urllib.parse import quote

from .. import brightdata
from ..models import Product
from .base import StoreAdapter
from .liverpool import extract_model

_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# Coppel ha cambiado la estructura varias veces; se prueba un conjunto amplio
_PRICE_KEYS = ("precio", "precioPromocion", "precioOferta", "priceWithDiscount",
               "price", "salePrice", "discountPrice")
_LIST_KEYS = ("precioTachado", "precioOriginal", "listPrice", "originalPrice", "regularPrice")
_NAME_KEYS = ("nombre", "name", "title", "productName", "descripcion")
_ID_KEYS = ("sku", "id", "productId", "codigo", "codigoProducto", "itemId")


class CoppelAdapter(StoreAdapter):
    quality = "best_effort"
    costly = True       # siempre vía Bright Data (WAF)

    def __init__(self, config: dict):
        super().__init__(config)
        self.key = config["key"]
        self.name = config.get("name", "Coppel")
        self.base = config.get("base", "https://www.coppel.com").rstrip("/")
        self.terms: list[str] = config.get("search_terms", [])
        self.max_per_term = int(config.get("max_products_per_term", 40))
        self._lookup_cache: dict[str, list[Product]] = {}

    def _fetch(self, url: str) -> str | None:
        try:
            return brightdata.fetch(url, country="mx", timeout=55, retries=2)
        except brightdata.FetchError as e:
            print(f"[{self.key}] aviso: {e}")
            return None

    def _search_url(self, term: str) -> str:
        return f"{self.base}/busqueda?texto={quote(term)}"

    def _products_in(self, html: str) -> list[dict]:
        m = _NEXT.search(html)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []
        return list(_walk_products(data))

    def _to_product(self, raw: dict) -> Product | None:
        name = next((raw[k] for k in _NAME_KEYS if raw.get(k)), None)
        if not name:
            return None
        price = _price(next((raw.get(k) for k in _PRICE_KEYS if raw.get(k) is not None), None))
        listp = _price(next((raw.get(k) for k in _LIST_KEYS if raw.get(k) is not None), None))
        if not price:
            return None
        pid = next((str(raw[k]) for k in _ID_KEYS if raw.get(k)), None)
        slug = raw.get("url") or raw.get("slug") or raw.get("uri") or raw.get("link") or ""
        if slug and not slug.startswith("http"):
            slug = f"{self.base}/{slug.lstrip('/')}"
        url = slug or (f"{self.base}/buscar/{pid}" if pid else self.base)
        avail = raw.get("disponible", raw.get("available", raw.get("enStock", True)))
        return Product(
            store=self.key,
            name=str(name),
            url=url,
            price=price,
            list_price=listp,
            model=raw.get("modelo") or raw.get("model") or extract_model(str(name)),
            brand=raw.get("marca") or raw.get("brand"),
            available=avail not in (False, 0, "N", "false", "0", "no"),
            extra={"sku": pid},
        )

    def scan(self) -> Iterable[Product]:
        seen: set[str] = set()
        for term in self.terms:
            html = self._fetch(self._search_url(term))
            if not html:
                continue
            raws = self._products_in(html)
            if not raws:
                print(f"[{self.key}] '{term}': 0 productos en __NEXT_DATA__ "
                      f"({len(html)} bytes) — puede que cambiara la estructura.")
            for raw in raws[: self.max_per_term]:
                pid = next((str(raw.get(k, "")) for k in _ID_KEYS if raw.get(k)), "")
                if pid and pid in seen:
                    continue
                if pid:
                    seen.add(pid)
                p = self._to_product(raw)
                if p:
                    yield p

    def lookup(self, query: str) -> list[Product]:
        if query in self._lookup_cache:
            return self._lookup_cache[query]
        html = self._fetch(self._search_url(query))
        out: list[Product] = []
        if html:
            for raw in self._products_in(html)[:12]:
                p = self._to_product(raw)
                if p:
                    out.append(p)
        self._lookup_cache[query] = out
        return out


def _walk_products(node, depth: int = 0):
    """Busca en __NEXT_DATA__ nodos con la forma de un producto Coppel."""
    if depth > 18:
        return
    if isinstance(node, dict):
        has_price = any(node.get(k) is not None for k in _PRICE_KEYS)
        has_name = any(node.get(k) for k in _NAME_KEYS)
        has_id = any(node.get(k) for k in _ID_KEYS)
        if has_price and has_name and has_id:
            yield node
        else:
            for v in node.values():
                yield from _walk_products(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_products(v, depth + 1)


def _price(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    m = re.search(r"\d[\d,]*\.?\d*", str(v))
    if not m:
        return None
    try:
        f = float(m.group(0).replace(",", ""))
        return f if f > 0 else None
    except ValueError:
        return None
