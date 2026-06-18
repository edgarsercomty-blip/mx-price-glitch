"""Adaptador Home Depot México (backend HCL/WebSphere Commerce + Next.js).

Flujo confirmado por reconocimiento:
  1) Enumerar productos por término de búsqueda:
       GET /search/resources/store/{store_id}/productview/bySearchTerm/{term}
     Devuelve catalogEntryView[] con name, partNumber, seo.href, manufacturer y
     atributos (incluye iconos de promoción tipo PROMOICON_*). NO trae precios.
  2) Precio regular + oferta: solo el JSON de la página de producto los trae:
       GET /_next/data/{buildId}{seo.href}.json
     -> price[] con usage "Offer" (actual) y "Display" (regular/tachado).

Para no descargar 50 páginas por término, por defecto solo se piden los detalles
de los productos marcados con icono de promoción (only_promo). El precio actual
(Offer) también está disponible en lote vía el endpoint de price, pero el precio
de lista (Display) solo en la página, así que ahí se obtienen ambos.

No expone EAN, así que Home Depot aporta a la señal de "descuento propio", no al
cruce entre tiendas.
"""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import quote

import requests

from .. import brightdata
from ..models import Product
from .base import StoreAdapter

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class HomeDepotAdapter(StoreAdapter):
    quality = "solid"

    def __init__(self, config: dict):
        super().__init__(config)
        self.key = config["key"]
        self.name = config.get("name", "Home Depot MX")
        self.base = config.get("base", "https://www.homedepot.com.mx").rstrip("/")
        self.store_id = str(config.get("store_id", "10351"))
        self.terms: list[str] = config.get("search_terms", [])
        self.max_per_term = int(config.get("max_products_per_term", 50))
        self.only_promo = bool(config.get("only_promo", True))
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA,
                                      "Accept": "application/json"})
        self._build_id: str | None = None

    # ---- fetch: directo y, si falla, vía Bright Data ----
    def _get_text(self, url: str) -> str | None:
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

    def _get_json(self, url: str):
        import json
        txt = self._get_text(url)
        if not txt:
            return None
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            return None

    def _get_build_id(self) -> str | None:
        if self._build_id:
            return self._build_id
        html = self._get_text(f"{self.base}/")
        if not html:
            return None
        m = re.search(r'"buildId":"([^"]+)"', html)
        self._build_id = m.group(1) if m else None
        return self._build_id

    # ---- enumeración ----
    def _search(self, term: str) -> list[dict]:
        url = (f"{self.base}/search/resources/store/{self.store_id}"
               f"/productview/bySearchTerm/{quote(term)}")
        data = self._get_json(url)
        if not isinstance(data, dict):
            return []
        return (data.get("catalogEntryView") or [])[: self.max_per_term]

    @staticmethod
    def _has_promo(entry: dict) -> bool:
        for a in entry.get("attributes") or []:
            ident = (a.get("identifier") or "")
            if ident.startswith("PROMOICON"):
                return True
        return False

    # ---- precios desde la página de producto ----
    def _prices_from_product(self, href: str) -> tuple[float | None, float | None]:
        build = self._get_build_id()
        if not build:
            return None, None
        url = f"{self.base}/_next/data/{build}{href}.json"
        data = self._get_json(url)
        if data is None:
            return None, None
        offer = display = None
        for price_list in _find_price_lists(data):
            for pr in price_list:
                val = _f(pr.get("value"))
                usage = pr.get("usage")
                if usage == "Offer" and val:
                    offer = val
                elif usage == "Display" and val:
                    display = val
            if offer:
                break
        return offer, display

    def scan(self) -> Iterable[Product]:
        seen: set[str] = set()
        for term in self.terms:
            for entry in self._search(term):
                pn = entry.get("partNumber")
                if not pn or pn in seen:
                    continue
                if self.only_promo and not self._has_promo(entry):
                    continue
                seen.add(pn)
                seo = entry.get("seo") or {}
                href = seo.get("href")
                if not href:
                    continue
                offer, display = self._prices_from_product(href)
                if not offer:
                    continue
                yield Product(
                    store=self.key,
                    name=entry.get("name") or pn,
                    url=f"{self.base}{href}",
                    price=offer,
                    list_price=display,
                    brand=entry.get("manufacturer"),
                    available=str(entry.get("buyable")).lower() == "true",
                    extra={"partNumber": pn},
                )


def _find_price_lists(node, _depth: int = 0):
    """Encuentra recursivamente listas de precios [{usage, value, ...}]."""
    if _depth > 12:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "price" and isinstance(v, list) and v and \
                    isinstance(v[0], dict) and "usage" in v[0]:
                yield v
            else:
                yield from _find_price_lists(v, _depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _find_price_lists(item, _depth + 1)


def _f(v) -> float | None:
    try:
        f = float(str(v).replace(",", "").strip())
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None
