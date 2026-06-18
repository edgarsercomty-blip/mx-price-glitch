"""Adaptador best-effort por JSON-LD (schema.org Product/Offer).

Para tiendas sin API de catálogo abierta (Amazon, Walmart, Sam's, Liverpool).
Recorre URLs de listado o de producto que tú definas en stores.yaml y extrae
los bloques <script type="application/ld+json">. Casi siempre trae nombre y
precio; el precio de lista (para el descuento propio) NO siempre está, así que
estas tiendas aportan sobre todo al cruce por EAN cuando el EAN está presente.

Config (stores.yaml):
  type: jsonld
  urls:                      # páginas de producto o listado a revisar
    - https://www.amazon.com.mx/dp/B0XXXXXXX
  product_links_selector: a.product   # opcional: extraer links de un listado

NOTA: el HTML de estas tiendas cambia seguido. Trátalo como punto de partida;
afinar selectores por tienda es parte del mantenimiento.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from bs4 import BeautifulSoup

from .. import brightdata
from ..models import Product
from .base import StoreAdapter


class JsonLdAdapter(StoreAdapter):
    quality = "best_effort"

    def __init__(self, config: dict):
        super().__init__(config)
        self.key = config["key"]
        self.name = config.get("name", self.key)
        self.urls: list[str] = config.get("urls", [])

    def scan(self) -> Iterable[Product]:
        for url in self.urls:
            try:
                html = brightdata.fetch(url)
            except brightdata.FetchError as e:
                print(f"[{self.key}] aviso: {url} falló: {e}")
                continue
            yield from self._extract(html, url)

    def _extract(self, html: str, source_url: str) -> Iterable[Product]:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("script", type="application/ld+json"):
            blob = tag.string or tag.get_text() or ""
            for node in _iter_json(blob):
                p = self._product_from_node(node, source_url)
                if p:
                    yield p

    def _product_from_node(self, node: dict, source_url: str) -> Product | None:
        if not isinstance(node, dict):
            return None
        if node.get("@type") not in ("Product", ["Product"]):
            return None
        offers = node.get("offers")
        price = list_price = None
        url = node.get("url") or source_url
        if isinstance(offers, dict):
            price = _f(offers.get("price") or offers.get("lowPrice"))
            list_price = _f(offers.get("highPrice"))
            url = offers.get("url") or url
        elif isinstance(offers, list) and offers:
            price = _f(offers[0].get("price"))
        if not price:
            return None
        ean = node.get("gtin13") or node.get("gtin") or node.get("gtin12") or None
        return Product(
            store=self.key,
            name=node.get("name") or "?",
            url=url,
            price=price,
            list_price=list_price,
            ean=str(ean) if ean else None,
            brand=_brand(node.get("brand")),
        )


def _iter_json(blob: str):
    blob = blob.strip()
    if not blob:
        return
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        # algunos sitios concatenan objetos; intenta rescatar el primero
        m = re.search(r"\{.*\}", blob, re.S)
        if not m:
            return
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return
    if isinstance(data, list):
        yield from data
    elif isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            yield from data["@graph"]
        else:
            yield data


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(str(v).replace(",", "").replace("$", "").strip())
        return f if f > 0 else None
    except ValueError:
        return None


def _brand(b) -> str | None:
    if isinstance(b, dict):
        return b.get("name")
    if isinstance(b, str):
        return b
    return None
