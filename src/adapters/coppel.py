"""Adaptador Coppel (GraphQL persisted query, vía Bright Data Web Unlocker).

Flujo descubierto con el navegador (2026-07-12) — la búsqueda de Coppel es
GraphQL con persisted queries detrás de Apigee y ANTES era impráctica de
capturar sin DevTools:

  1. POST https://www.coppel.com/auth/access-token   (sin body ni headers)
     -> {"access_token": "eyJ...", "expires_in": 1800}   (JWT anónimo Azure AD)
  2. GET  https://www.coppel.com/graphql?operationName=GET_SEARCH_RESULTS
       &variables={"searchTerm":T,"pageNumber":N,"pageSize":K,
                   "orderBy":"0","filter":[]}            (nodeContext opcional)
       &extensions={"persistedQuery":{"version":1,"sha256Hash":_SEARCH_HASH}}
     con headers Authorization: Bearer <token> y Content-Type: application/json
     (sin content-type, Apollo lo rechaza por CSRF).

Producto (LucidProduct): name, brand, partNumber ("PM-123" propio /
"MKP-..." marketplace), sku, href (/pdp/...), mpSellerName/sellerId,
price.discountedPrice (actual; null si no hay oferta) y price.salesPrice
(lista). Coppel bloquea el acceso directo por TLS -> siempre Bright Data.

Config (stores.yaml):
  type: coppel
  search_terms: [refrigerador, lavadora, ...]
  max_products_per_term: 40
"""
from __future__ import annotations

import json
import time
from typing import Iterable
from urllib.parse import quote

from .. import brightdata
from ..models import Product
from .base import StoreAdapter
from .liverpool import extract_model

# hash de la persisted query GET_SEARCH_RESULTS (capturado del sitio; si Coppel
# despliega un frontend nuevo puede cambiar -> recapturar con el navegador)
_SEARCH_HASH = "3a0d140c355847035f9539ea0e0eaf19b83a404c1d80d0bd3493b3dae3cd8658"
# margen antes de expirar el token (expires_in llega en 1800 s)
_TOKEN_SLACK = 120


class CoppelAdapter(StoreAdapter):
    quality = "best_effort"
    costly = True       # siempre vía Bright Data (WAF bloquea directo)

    def __init__(self, config: dict):
        super().__init__(config)
        self.key = config["key"]
        self.name = config.get("name", "Coppel")
        self.base = config.get("base", "https://www.coppel.com").rstrip("/")
        self.terms: list[str] = config.get("search_terms", [])
        self.max_per_term = int(config.get("max_products_per_term", 40))
        self._lookup_cache: dict[str, list[Product]] = {}
        self._token: str | None = None
        self._token_exp: float = 0.0

    # ---- auth ----
    def _get_token(self) -> str | None:
        if self._token and time.time() < self._token_exp:
            return self._token
        try:
            raw = brightdata.fetch(f"{self.base}/auth/access-token",
                                   method="POST", country="mx",
                                   timeout=45, retries=2)
            data = json.loads(raw)
            self._token = data["access_token"]
            self._token_exp = time.time() + float(
                data.get("expires_in", 1800)) - _TOKEN_SLACK
            return self._token
        except (brightdata.FetchError, json.JSONDecodeError, KeyError,
                TypeError, ValueError) as e:
            print(f"[{self.key}] aviso: no se pudo obtener token: {e}")
            return None

    # ---- búsqueda ----
    def _search_url(self, term: str, page: int, page_size: int) -> str:
        variables = {"searchTerm": term, "pageNumber": page,
                     "pageSize": page_size, "orderBy": "0", "filter": []}
        ext = {"persistedQuery": {"version": 1, "sha256Hash": _SEARCH_HASH}}
        return (f"{self.base}/graphql?operationName=GET_SEARCH_RESULTS"
                f"&variables={quote(json.dumps(variables, separators=(',', ':')))}"
                f"&extensions={quote(json.dumps(ext, separators=(',', ':')))}")

    def _search(self, term: str, page: int = 1, page_size: int = 50) -> list[dict]:
        token = self._get_token()
        if not token:
            return []
        try:
            raw = brightdata.fetch(
                self._search_url(term, page, page_size), country="mx",
                timeout=55, retries=2,
                unblock_headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json",
                                 "Accept": "application/json"})
        except brightdata.FetchError as e:
            print(f"[{self.key}] aviso: '{term}' falló: {e}")
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"[{self.key}] aviso: '{term}' respuesta no-JSON "
                  f"({len(raw)} bytes)")
            return []
        if data.get("errors") or "data" not in data:
            # token vencido/inválido -> reintentar una vez con token fresco
            self._token = None
            token = self._get_token()
            if not token:
                print(f"[{self.key}] aviso: '{term}' GraphQL error: "
                      f"{str(data)[:150]}")
                return []
            try:
                raw = brightdata.fetch(
                    self._search_url(term, page, page_size), country="mx",
                    timeout=55, retries=1,
                    unblock_headers={"Authorization": f"Bearer {token}",
                                     "Content-Type": "application/json",
                                     "Accept": "application/json"})
                data = json.loads(raw)
            except (brightdata.FetchError, json.JSONDecodeError):
                return []
        results = (data.get("data") or {}).get("getSearchResults") or {}
        return results.get("products") or []

    # ---- mapeo ----
    def _to_product(self, raw: dict) -> Product | None:
        name = raw.get("name")
        pr = raw.get("price") or {}
        sales = pr.get("salesPrice")
        disc = pr.get("discountedPrice")
        price = disc or sales
        if not name or not price or price <= 0:
            return None
        href = raw.get("href") or ""
        url = f"{self.base}{href}" if href.startswith("/") else (href or self.base)
        return Product(
            store=self.key,
            name=str(name),
            url=url,
            price=float(price),
            list_price=float(sales) if disc and sales and sales > disc else None,
            model=extract_model(str(name)),
            brand=raw.get("brand"),
            available=True,     # la búsqueda solo lista disponibles
            extra={"sku": raw.get("sku"),
                   "partNumber": raw.get("partNumber"),
                   "seller": raw.get("mpSellerName")},
        )

    def scan(self) -> Iterable[Product]:
        seen: set[str] = set()
        for term in self.terms:
            raws = self._search(term, page_size=min(self.max_per_term, 100))
            if not raws:
                print(f"[{self.key}] '{term}': 0 productos (GraphQL)")
            for raw in raws[: self.max_per_term]:
                uid = str(raw.get("sku") or raw.get("partNumber") or "")
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
        out: list[Product] = []
        for raw in self._search(query, page_size=12)[:12]:
            p = self._to_product(raw)
            if p:
                out.append(p)
        self._lookup_cache[query] = out
        return out
