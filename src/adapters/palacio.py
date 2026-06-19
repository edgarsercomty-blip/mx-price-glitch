"""Adaptador El Palacio de Hierro (Salesforce Commerce Cloud + búsqueda
Constructor.io).

La búsqueda del sitio está servida por Constructor.io, cuya API pública
responde JSON directo (sin Bright Data) con precio actual y de lista:
  priceObject.sales.value -> precio actual
  priceObject.list.value  -> precio de lista / antes
Comparando ambos sale el descuento propio. Trae marca, url y discountPercentage.

Config (stores.yaml):
  type: palacio
  cnstrc_key: key_5fTaaMhNEscECxIa
  search_terms: [bolsa, reloj, ...]
  num_results_per_page: 50
"""
from __future__ import annotations

from typing import Iterable
from urllib.parse import quote

import requests

from .. import brightdata
from ..models import Product
from .base import StoreAdapter
from .liverpool import extract_model

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
API = "https://ac.cnstrc.com/search"


class PalacioAdapter(StoreAdapter):
    quality = "solid"

    def __init__(self, config: dict):
        super().__init__(config)
        self.key = config["key"]
        self.name = config.get("name", "El Palacio de Hierro")
        self.base = config.get("base", "https://www.elpalaciodehierro.com").rstrip("/")
        self.cnstrc_key = config.get("cnstrc_key", "key_5fTaaMhNEscECxIa")
        self.terms: list[str] = config.get("search_terms", [])
        self.num = int(config.get("num_results_per_page", 50))
        self.pages = int(config.get("pages_per_term", 1))
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA, "Accept": "application/json"})
        self._lookup_cache: dict[str, list[Product]] = {}

    def _api_url(self, query: str, page: int = 1) -> str:
        return (f"{API}/{quote(query)}?key={self.cnstrc_key}"
                f"&num_results_per_page={self.num}&page={page}&c=ciojs&i=mxprice&s=1")

    def _get_json(self, query: str, page: int = 1):
        url = self._api_url(query, page)
        try:
            r = self._session.get(url, timeout=25)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        try:
            import json
            return json.loads(brightdata.fetch(url, timeout=25, retries=1))
        except (brightdata.FetchError, ValueError):
            return None

    def _results(self, query: str, page: int = 1) -> list[dict]:
        data = self._get_json(query, page)
        if not isinstance(data, dict):
            return []
        return (data.get("response") or {}).get("results") or []

    def _to_product(self, r: dict) -> Product | None:
        d = r.get("data") or {}
        po = d.get("priceObject") or {}
        price = _f((po.get("sales") or {}).get("value")) or _f(d.get("price"))
        listp = _f((po.get("list") or {}).get("value"))
        if not price:
            return None
        url = d.get("url") or ""
        if url and not url.startswith("http"):
            url = f"{self.base}{url}"
        name = r.get("value") or d.get("id") or "?"
        return Product(
            store=self.key, name=name, url=url or self.base,
            price=price, list_price=listp,
            model=extract_model(name),
            brand=d.get("brandName") or d.get("brand"),
            available=str(d.get("availabilityStatus", "")).upper() != "OUTOFSTOCK",
            extra={"id": d.get("id"), "outlet": d.get("isOutlet")},
        )

    def scan(self) -> Iterable[Product]:
        seen: set[str] = set()
        for term in self.terms:
            for page in range(1, self.pages + 1):
                results = self._results(term, page)
                if not results:
                    break
                for r in results:
                    p = self._to_product(r)
                    if not p:
                        continue
                    pid = (p.extra or {}).get("id") or p.url
                    if pid in seen:
                        continue
                    seen.add(pid)
                    yield p

    def lookup(self, query: str) -> list[Product]:
        if query in self._lookup_cache:
            return self._lookup_cache[query]
        out = [p for p in (self._to_product(r) for r in self._results(query)) if p]
        self._lookup_cache[query] = out[:12]
        return self._lookup_cache[query]


def _f(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None
