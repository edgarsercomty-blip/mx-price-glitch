"""Adaptador Liverpool.

Hasta 2026-06-30 la página de resultados renderizaba en servidor con
Next.js pages-router e incluía <script id="__NEXT_DATA__"> con la lista de
productos. Liverpool migró su frontend (Next.js App Router / RSC): esa ruta
de búsqueda (/tienda/buscar) ahora da 404 y __NEXT_DATA__ ya no existe.

Formato actual (validado 2026-07-06):
  - URL de búsqueda: /tienda?s={término}  (antes: /tienda/buscar?s=)
  - Los precios/marca vienen en JSON dentro de los chunks de streaming RSC
    (self.__next_f.push(...)), como texto JSON-escapado. El nombre/URL del
    producto NO está en ese JSON — se arma desde el <a data-testid=
    "{productId}-card-card-link" href="/tienda/pdp/{slug}/{productId}...">
    que sí se renderiza como HTML plano. Se unen ambas fuentes por
    productId (ver _PRICE_BLOCK / _PDP_HREF).
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

# Formato viejo (pages-router), se deja como fallback por si Liverpool revierte.
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# Formato actual (App Router / RSC): bloque de precio+marca+productId, tal
# como aparece (JSON-escapado) dentro de un chunk de self.__next_f.push(...).
# El orden de llaves es el que genera el template de Liverpool; si lo cambian
# esta regex deja de matchear y hay que re-probar con probe_liverpool.py.
_PRICE_BLOCK = re.compile(
    r'\\"brand\\":\\"(?P<brand>[^\\"]*)\\"[\s\S]{0,900}?'
    r'\\"maximumListPrice\\":(?P<list>[\d.]+)'
    r',\\"minimumPromoPrice\\":(?P<minpromo>[\d.]+)'
    r',\\"maximumPromoPrice\\":(?P<promo>[\d.]+)'
    r',\\"numRecords\\":\d+'
    r',\\"productId\\":\\"(?P<pid>\d+)\\"'
)

# Enlace de la tarjeta del producto, renderizado como HTML normal (no JSON).
# El "?skuid=..." es opcional (algunas categorías no lo traen).
_PDP_HREF = re.compile(
    r'href="(/tienda/pdp/(?P<slug>[a-z0-9][a-z0-9-]*)/(?P<pid>\d{6,}))(?:\?[^"]*)?"'
)


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
        self._lookup_cache: dict[str, list[Product]] = {}

    def _get_html(self, url: str) -> str | None:
        try:
            r = self._session.get(url, timeout=25)
            if r.status_code == 200 and r.text:
                return r.text
        except requests.RequestException:
            pass
        try:
            return brightdata.fetch(url, timeout=25, retries=1)
        except brightdata.FetchError:
            return None

    def _products_in(self, html: str) -> list[dict]:
        # formato viejo (pages-router), por si Liverpool revierte el cambio
        m = _NEXT.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
                prods = list(_walk_products(data))
                if prods:
                    return prods
            except json.JSONDecodeError:
                pass

        # formato actual: precio/marca (RSC) + nombre/URL (HTML), unidos por productId
        prices: dict[str, dict] = {}
        for mm in _PRICE_BLOCK.finditer(html):
            prices[mm.group("pid")] = {
                "brand": mm.group("brand") or None,
                "maximumListPrice": mm.group("list"),
                "minimumPromoPrice": mm.group("minpromo"),
                "maximumPromoPrice": mm.group("promo"),
            }
        if not prices:
            return []

        out: list[dict] = []
        seen: set[str] = set()
        for mm in _PDP_HREF.finditer(html):
            pid = mm.group("pid")
            if pid in seen:
                continue
            price = prices.get(pid)
            if not price:
                continue
            seen.add(pid)
            raw = dict(price)
            raw["productId"] = pid
            raw["uri"] = mm.group(1)
            raw["title"] = mm.group("slug").replace("-", " ").strip().title()
            raw["availability"] = "IN_STOCK"  # no se observó otro valor en búsquedas
            out.append(raw)
        return out

    def scan(self) -> Iterable[Product]:
        seen: set[str] = set()
        for term in self.terms:
            for page in range(1, self.pages + 1):
                url = f"{self.base}/tienda?s={quote(term)}"
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
        name = raw.get("title") or raw.get("name") or raw.get("productId")
        return Product(
            store=self.key,
            name=name,
            url=uri,
            price=price,
            list_price=listp,
            model=extract_model(name),
            brand=raw.get("brand"),
            available=str(raw.get("availability")).upper() == "IN_STOCK",
            extra={"productId": raw.get("productId"),
                   "marketplace": raw.get("isMarketPlace")},
        )

    def lookup(self, query: str) -> list[Product]:
        if query in self._lookup_cache:
            return self._lookup_cache[query]
        url = f"{self.base}/tienda?s={quote(query)}"
        html = self._get_html(url)
        out: list[Product] = []
        if html:
            for raw in self._products_in(html):
                p = self._to_product(raw)
                if p:
                    out.append(p)
        self._lookup_cache[query] = out[:12]
        return self._lookup_cache[query]


def _walk_products(node):
    if isinstance(node, dict):
        if "maximumListPrice" in node and ("productId" in node or "id" in node):
            yield node
        for v in node.values():
            yield from _walk_products(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_products(v)


_MODEL = re.compile(r"\b(?=[A-Za-z0-9/+\-]*\d)(?=[A-Za-z0-9/+\-]*[A-Za-z])"
                    r"[A-Za-z0-9][A-Za-z0-9/+\-]{4,}\b")


def extract_model(name: str | None) -> str | None:
    """Saca el token de modelo del fabricante de un título (alfanumérico con
    letras y dígitos, p. ej. OLED55C5PSA, WA21B3554GV)."""
    if not name:
        return None
    cands = [m.group(0) for m in _MODEL.finditer(name)]
    if not cands:
        return None
    return max(cands, key=len)


def _f(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None
