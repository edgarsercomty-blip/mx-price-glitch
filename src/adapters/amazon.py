"""Adaptador Amazon México (vía Bright Data; Amazon bloquea directo).

Búsqueda: /s?k={term} -> tarjetas con data-asin, título, precio actual y, si
hay oferta, precio de lista (tachado). De ahí salen candidatos por descuento
propio y precios para comparar.

Filtro del usuario: un producto Amazon SOLO se reporta si es **vendido por
Amazon México** o **enviado por Amazon** (FBA). Eso vive en la página del
producto, así que se confirma en `confirm_report()`, que se llama únicamente
para los pocos hallazgos ya confirmados como baratos (para no gastar consultas).
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

CARD = "div[data-asin][data-component-type='s-search-result']"


class AmazonAdapter(StoreAdapter):
    quality = "solid"

    def __init__(self, config: dict):
        super().__init__(config)
        self.key = config["key"]
        self.name = config.get("name", "Amazon MX")
        self.base = config.get("base", "https://www.amazon.com.mx").rstrip("/")
        self.terms: list[str] = config.get("search_terms", [])
        self.max_per_term = int(config.get("max_products_per_term", 48))
        self._lookup_cache: dict[str, list[Product]] = {}
        self._direct_cache: dict[str, bool] = {}

    def _fetch(self, url: str, timeout: int = 45) -> str | None:
        try:
            return brightdata.fetch(url, country="mx", timeout=timeout, retries=2)
        except brightdata.FetchError as e:
            print(f"[{self.key}] aviso: {url} falló: {e}")
            return None

    def _parse_search(self, html: str) -> list[Product]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[Product] = []
        for c in soup.select(CARD):
            asin = c.get("data-asin")
            if not asin:
                continue
            h2 = c.select_one("h2")
            title = h2.get_text(" ", strip=True) if h2 else None
            cur = c.select_one(".a-price .a-offscreen")
            price = _money(cur.get_text(strip=True) if cur else None)
            if not title or not price:
                continue
            strike = c.select_one(".a-price.a-text-price .a-offscreen") \
                or c.select_one("span[data-a-strike='true'] .a-offscreen")
            listp = _money(strike.get_text(strip=True) if strike else None)
            out.append(Product(
                store=self.key, name=title,
                url=f"{self.base}/dp/{asin}", price=price, list_price=listp,
                model=extract_model(title),
                extra={"asin": asin},
            ))
        return out

    def scan(self) -> Iterable[Product]:
        seen: set[str] = set()
        for term in self.terms:
            html = self._fetch(f"{self.base}/s?k={quote(term)}")
            if not html:
                continue
            for p in self._parse_search(html)[: self.max_per_term]:
                asin = (p.extra or {}).get("asin")
                if asin and asin in seen:
                    continue
                if asin:
                    seen.add(asin)
                yield p

    def lookup(self, query: str) -> list[Product]:
        if query in self._lookup_cache:
            return self._lookup_cache[query]
        html = self._fetch(f"{self.base}/s?k={quote(query)}", timeout=35)
        out = self._parse_search(html)[:12] if html else []
        self._lookup_cache[query] = out
        return out

    # ---- filtro: vendido/enviado por Amazon ----
    def confirm_report(self, product: Product) -> bool:
        asin = (product.extra or {}).get("asin")
        if not asin:
            return False
        if asin in self._direct_cache:
            return self._direct_cache[asin]
        html = self._fetch(f"{self.base}/dp/{asin}", timeout=45)
        ok = _is_amazon_direct(html) if html else False
        self._direct_cache[asin] = ok
        if not ok:
            print(f"[{self.key}] descartado (no vendido/enviado por Amazon): {asin}")
        return ok


def _is_amazon_direct(html: str) -> bool:
    """True si el producto es vendido por Amazon México o enviado por Amazon."""
    soup = BeautifulSoup(html, "html.parser")
    # vendedor en el buybox
    for sel in ("#sellerProfileTriggerId", "#merchantInfoFeature_feature_div",
                "#merchant-info", "#tabular-buybox"):
        el = soup.select_one(sel)
        if el and "amazon" in el.get_text(" ", strip=True).lower():
            return True
    text = soup.get_text(" ", strip=True).lower()
    patterns = ("vendido y enviado por amazon", "enviado por amazon",
                "se envía desde amazon", "se envia desde amazon",
                "vendido por amazon")
    return any(p in text for p in patterns)


def _money(v) -> float | None:
    if not v:
        return None
    m = re.search(r"[\d][\d,]*\.?\d*", str(v))
    if not m:
        return None
    try:
        f = float(m.group(0).replace(",", ""))
        return f if f > 0 else None
    except ValueError:
        return None
