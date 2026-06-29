"""Adaptador Mercado Libre México (el marketplace #1 del país).

La API pública (/sites/MLM/search) pasó a requerir autenticación en 2024, así que
se baja el HTML de la página de listado vía Bright Data Web Unlocker y se parsea
la estructura "polycard" actual.

URL de búsqueda: https://listado.mercadolibre.com.mx/{terminos-con-guiones}

Estructura (puede cambiar; validar con probe si deja de traer productos):
  .poly-card / .ui-search-result        -> tarjeta de producto
  a.poly-component__title               -> título + link
  .poly-price__current .andes-money-amount__fraction  -> precio actual
  s.andes-money-amount--previous ...    -> precio anterior (tachado)

Filtro: solo productos vendidos como NUEVO (Mercado Libre mezcla usados); se
descartan los marcados como usados/reacondicionados (la capa global de
detect.is_refurbished ya filtra por nombre, aquí reforzamos por etiqueta).

Config (stores.yaml):
  type: mercadolibre
  search_terms: [refrigerador, pantalla, ...]
  max_products_per_term: 50
"""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import quote

from bs4 import BeautifulSoup

from .. import brightdata
from ..models import Product
from .base import StoreAdapter
from .liverpool import extract_model

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class MercadoLibreAdapter(StoreAdapter):
    quality = "best_effort"
    costly = True               # vía Bright Data

    def __init__(self, config: dict):
        super().__init__(config)
        self.key = config["key"]
        self.name = config.get("name", "Mercado Libre MX")
        self.base = config.get("base", "https://listado.mercadolibre.com.mx").rstrip("/")
        self.terms: list[str] = config.get("search_terms", [])
        self.max_per_term = int(config.get("max_products_per_term", 50))
        self._lookup_cache: dict[str, list[Product]] = {}

    def _search_url(self, term: str) -> str:
        # Mercado Libre usa guiones en la ruta para los términos de búsqueda
        slug = quote(term.strip().replace(" ", "-"))
        return f"{self.base}/{slug}"

    def _fetch(self, url: str, timeout: int = 50) -> str | None:
        try:
            return brightdata.fetch(url, country="mx", timeout=timeout, retries=2,
                                    unblock_headers={"User-Agent": UA})
        except brightdata.FetchError as e:
            print(f"[{self.key}] aviso: {e}")
            return None

    def _parse(self, html: str) -> list[Product]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".poly-card") or soup.select("li.ui-search-layout__item") \
            or soup.select(".ui-search-result")
        out: list[Product] = []
        for c in cards:
            a = (c.select_one("a.poly-component__title")
                 or c.select_one("a.ui-search-item__group__element")
                 or c.select_one("a.ui-search-link")
                 or c.select_one("h2 a") or c.select_one("a[href*='articulo.mercadolibre']")
                 or c.select_one("a[href*='/MLM-']"))
            if not a:
                continue
            title = a.get_text(" ", strip=True) or a.get("title")
            url = a.get("href") or ""
            if not title or not url:
                continue
            # etiqueta de condición (usado) -> descartar
            cond = c.select_one(".poly-component__seller, .ui-search-item__condition")
            if cond and "usado" in cond.get_text(" ", strip=True).lower():
                continue
            fractions = c.select(".andes-money-amount__fraction")
            if not fractions:
                continue
            price = _money(fractions[0].get_text(strip=True))
            # precio anterior: dentro de un <s> o con clase --previous
            prev = c.select_one("s .andes-money-amount__fraction") \
                or c.select_one(".andes-money-amount--previous .andes-money-amount__fraction")
            listp = _money(prev.get_text(strip=True)) if prev else None
            if not price:
                continue
            out.append(Product(
                store=self.key, name=title, url=url.split("#")[0],
                price=price, list_price=listp,
                model=extract_model(title),
                extra={"mlm": _mlm_id(url)},
            ))
        return out

    def scan(self) -> Iterable[Product]:
        seen: set[str] = set()
        for term in self.terms:
            html = self._fetch(self._search_url(term))
            if not html:
                continue
            prods = self._parse(html)
            if not prods:
                print(f"[{self.key}] '{term}': 0 productos ({len(html)} bytes) — "
                      f"revisar selectores con probe si persiste.")
            for p in prods[: self.max_per_term]:
                uid = (p.extra or {}).get("mlm") or p.url
                if uid in seen:
                    continue
                seen.add(uid)
                yield p

    def lookup(self, query: str) -> list[Product]:
        if query in self._lookup_cache:
            return self._lookup_cache[query]
        html = self._fetch(self._search_url(query), timeout=40)
        out = self._parse(html)[:12] if html else []
        self._lookup_cache[query] = out
        return out


def _mlm_id(url: str) -> str | None:
    m = re.search(r"(MLM-?\d+)", url)
    return m.group(1).replace("-", "") if m else None


def _money(v) -> float | None:
    if not v:
        return None
    # "12,999" o "12.999" (ML usa coma de miles en MX) -> 12999
    s = re.sub(r"[^\d]", "", str(v))
    if not s:
        return None
    try:
        f = float(s)
        return f if f > 0 else None
    except ValueError:
        return None
