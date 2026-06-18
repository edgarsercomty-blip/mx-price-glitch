"""Adaptador Liverpool (Next.js; productos embebidos en __NEXT_DATA__).

La página de resultados de búsqueda renderiza en servidor e incluye en
<script id="__NEXT_DATA__"> la lista de productos con precios:
  maximumListPrice  -> precio regular / de lista
  maximumPromoPrice -> precio actual (promoción)
Comparando ambos sale el descuento propio. No expone EAN, así que Liverpool
aporta a la señal de "descuento propio", no al cruce entre tiendas.

Config (stores.yaml):
  type: liverpool
  search_terms: [taladro, pantalla, ...]
  pages_per_term: 1            # cuántas páginas de resultados recorrer
"""
from __future__ import annotations

import json
import re
from typing import Iterable
from urllib.parse import quote

import requests

from .. import brightdata
from ..models import Product
from .base import StoreAdapter

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class LiverpoolAdapter(StoreAdapter):
    quality = "solid"

    def __init__(self, config: dict):
        super().__init__(config)
        self.key = config["key"]
        self.name = config.get("name", "Liverpool")
        self.base = config.get("base", "https://www.liverpool.com.mx").rstrip("/")
        self.terms: list[str] = config.get("search_terms", [])
        self.pages = int(config.get("pages_per_term", 1))
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def _get_html(self, url: str) -> str | None:
        try:
            r = self._session.get(url, timeout=25)
            if r.status_code == 200 and r.text:
                return r.text
        except requests.RequestException:
            pass
        try:
            return brightdata.fetch(url)
        except brightdata.FetchError:
            return None

    def _products_in(self, html: str) -> Iterable[dict]:
        m = _NEXT.search(html)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []
        return list(_walk_products(data))

    def scan(self) -> Iterable[Product]:
        seen: set[str] = set()
        for term in self.terms:
            for page in range(1, self.pages + 1):
                url = f"{self.base}/tienda/buscar?s={quote(term)}"
                if page > 1:
                    url += f"&page={page}"
                html = self._get_html(url)
                if not html:
                    continue
                for raw in self._products_in(html):
                    pid = raw.get("productId") or raw.get("id")
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    p = self._to_product(raw)
                    if p:
                        yield p

    def _to_product(self, raw: dict) -> Product | None:
        price = _f(raw.get("maximumPromoPrice")) or _f(raw.get("minimumPromoPrice"))
        listp = _f(raw.get("maximumListPrice")) or _f(raw.get("minimumListPrice"))
        if not price:
            return None
        uri = raw.get("uri") or f"{self.base}/tienda/pdp/{raw.get('productId')}"
        if uri and not uri.startswith("http"):
            uri = f"{self.base}{uri}"
        return Product(
            store=self.key,
            name=raw.get("title") or raw.get("name") or raw.get("productId"),
            url=uri,
            price=price,
            list_price=listp,
            brand=raw.get("brand"),
            available=str(raw.get("availability")).upper() == "IN_STOCK",
            extra={"productId": raw.get("productId"),
                   "marketplace": raw.get("isMarketPlace")},
        )


def _walk_products(node):
    if isinstance(node, dict):
        if "maximumListPrice" in node and ("productId" in node or "id" in node):
            yield node
        for v in node.values():
            yield from _walk_products(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_products(v)


def _f(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None
