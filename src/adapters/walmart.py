"""Adaptador Walmart México (plataforma "Glass" de Walmart, Next.js).

Walmart bloquea el acceso directo (te manda a /blocked), así que SIEMPRE se baja
vía Bright Data Web Unlocker. La página de búsqueda trae los productos en
<script id="__NEXT_DATA__"> con priceInfo:
  linePrice  -> precio actual (oferta)
  wasPrice   -> precio regular / antes
Comparando ambos sale el descuento propio. Trae brand y a veces model.

Config (stores.yaml):
  type: walmart
  search_terms: [taladro, pantalla, ...]
  max_products_per_term: 60
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


class WalmartAdapter(StoreAdapter):
    quality = "solid"

    def __init__(self, config: dict):
        super().__init__(config)
        self.key = config["key"]
        self.name = config.get("name", "Walmart MX")
        self.base = config.get("base", "https://www.walmart.com.mx").rstrip("/")
        self.terms: list[str] = config.get("search_terms", [])
        self.max_per_term = int(config.get("max_products_per_term", 60))
        self._lookup_cache: dict[str, list[Product]] = {}

    def _fetch_search(self, term: str) -> str | None:
        url = f"{self.base}/search?q={quote(term)}"
        try:
            return brightdata.fetch(url, country="mx", timeout=45, retries=2)
        except brightdata.FetchError as e:
            print(f"[{self.key}] aviso: '{term}' falló: {e}")
            return None

    def _products_in(self, html: str) -> list[dict]:
        m = _NEXT.search(html)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []
        return list(_walk(data))

    def _to_product(self, raw: dict) -> Product | None:
        pi = raw.get("priceInfo") or {}
        price = _money(pi.get("linePrice") or pi.get("linePriceDisplay"))
        listp = _money(pi.get("wasPrice") or pi.get("itemPrice"))
        if not price:
            return None
        url = raw.get("canonicalUrl") or ""
        if url and not url.startswith("http"):
            url = f"{self.base}{url}"
        name = raw.get("name") or raw.get("usItemId") or "?"
        return Product(
            store=self.key, name=name, url=url or self.base,
            price=price, list_price=listp,
            model=raw.get("model") or extract_model(name),
            brand=raw.get("brand"),
            available=str(raw.get("availabilityStatusV2", {}).get("value",
                          raw.get("canAddToCart", True))).upper() != "OUT_OF_STOCK",
            extra={"usItemId": raw.get("usItemId")},
        )

    def scan(self) -> Iterable[Product]:
        seen: set[str] = set()
        for term in self.terms:
            html = self._fetch_search(term)
            if not html:
                continue
            for raw in self._products_in(html)[: self.max_per_term]:
                uid = raw.get("usItemId")
                if uid and uid in seen:
                    continue
                if uid:
                    seen.add(uid)
                p = self._to_product(raw)
                if p:
                    yield p

    def lookup(self, query: str) -> list[Product]:
        if query in self._lookup_cache:
            return self._lookup_cache[query]
        html = self._fetch_search(query)
        out: list[Product] = []
        if html:
            for raw in self._products_in(html)[:12]:
                p = self._to_product(raw)
                if p:
                    out.append(p)
        self._lookup_cache[query] = out
        return out


def _walk(node):
    """Recolecta dicts de producto (con priceInfo + canonicalUrl) en cualquier nivel."""
    if isinstance(node, dict):
        if "priceInfo" in node and ("canonicalUrl" in node or "usItemId" in node):
            yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _money(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    m = re.search(r"[\d][\d,]*\.?\d*", str(v))
    if not m:
        return None
    try:
        f = float(m.group(0).replace(",", ""))
        return f if f > 0 else None
    except ValueError:
        return None
