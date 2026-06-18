"""Adaptador genérico para tiendas montadas en VTEX.

VTEX expone una API pública de catálogo que devuelve, por cada item, el precio
actual (`Price`), el precio de lista (`ListPrice` / `PriceWithoutDiscount`) y el
`ean`. Eso nos da DIRECTO la señal de descuento propio y la llave para cruzar
productos entre tiendas. Es, con diferencia, la fuente más confiable.

Tiendas mexicanas conocidas sobre VTEX:
  coppel.com, homedepot.com.mx, elpalaciodehierro.com, suburbia.com.mx,
  sears.com.mx, sanborns.com.mx, entre otras.

Config (stores.yaml):
  type: vtex
  host: www.coppel.com
  categories:               # rutas de categoría a escanear
    - tecnologia/celulares
    - ofertas
  max_per_category: 200     # tope de items por categoría
"""
from __future__ import annotations

from typing import Iterable, Iterator
from urllib.parse import quote

from .. import brightdata
from ..models import Product
from .base import StoreAdapter

PAGE = 50  # VTEX limita a 50 items por request


class VtexAdapter(StoreAdapter):
    quality = "solid"

    def __init__(self, config: dict):
        super().__init__(config)
        self.key = config["key"]
        self.name = config.get("name", self.key)
        self.host = config["host"].rstrip("/")
        self.categories: list[str] = config.get("categories", [])
        self.max_per_category = int(config.get("max_per_category", 200))

    def _category_url(self, category: str, frm: int, to: int) -> str:
        # Búsqueda por ruta de categoría: map=c por cada segmento.
        segments = [s for s in category.split("/") if s]
        path = "/".join(quote(s) for s in segments)
        maps = ",".join(["c"] * len(segments))
        return (f"https://{self.host}/api/catalog_system/pub/products/search/"
                f"{path}?map={maps}&_from={frm}&_to={to}")

    def _scan_category(self, category: str) -> Iterator[Product]:
        fetched = 0
        frm = 0
        while fetched < self.max_per_category:
            to = min(frm + PAGE - 1, frm + (self.max_per_category - fetched) - 1)
            url = self._category_url(category, frm, to)
            data = brightdata.fetch_json(url)
            if not isinstance(data, list) or not data:
                break
            for raw in data:
                p = self._parse_product(raw)
                if p:
                    yield p
            fetched += len(data)
            if len(data) < (to - frm + 1):
                break
            frm = to + 1

    def _parse_product(self, raw: dict) -> Product | None:
        items = raw.get("items") or []
        if not items:
            return None
        item = items[0]
        sellers = item.get("sellers") or []
        offer = None
        for s in sellers:
            co = s.get("commertialOffer") or {}
            if co.get("Price"):
                offer = co
                break
        if not offer:
            return None

        price = _f(offer.get("Price"))
        list_price = _f(offer.get("ListPrice")) or _f(offer.get("PriceWithoutDiscount"))
        if not price:
            return None

        link = raw.get("link") or raw.get("linkText")
        if link and not link.startswith("http"):
            link = f"https://{self.host}/{link}/p"

        ean = item.get("ean") or None
        if ean in ("", "0", "null"):
            ean = None

        return Product(
            store=self.key,
            name=raw.get("productName") or item.get("name") or "?",
            url=link or f"https://{self.host}",
            price=price,
            list_price=list_price,
            ean=ean,
            brand=raw.get("brand"),
            available=bool(offer.get("AvailableQuantity", 0)),
            extra={"productId": raw.get("productId")},
        )

    def scan(self) -> Iterable[Product]:
        for category in self.categories:
            try:
                yield from self._scan_category(category)
            except brightdata.FetchError as e:
                print(f"[{self.key}] aviso: categoría '{category}' falló: {e}")


def _f(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None
