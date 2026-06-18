"""Fuente de precios de Google Shopping vía Bright Data SERP API.

Sirve como árbitro de mercado en la verificación cruzada: agrega precios de
muchos vendedores, así se valida si un precio es realmente bajo aunque el
producto no esté en las otras tiendas que escaneamos.

Requisitos:
  - BRIGHTDATA_API_TOKEN (mismo token)
  - BRIGHTDATA_SERP_ZONE  (zona de tipo "SERP API" en Bright Data)

Control de costo:
  - Caché persistente en data/gshop_cache.json con vigencia (TTL) configurable:
    cada modelo se consulta a lo mucho una vez por ventana, no en cada corrida.
  - Tope de consultas de red por corrida (presupuesto), gestionado desde verify.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import brightdata
from .models import Product

SERP_BASE = "https://www.google.com/search"


class GoogleShopping:
    def __init__(self, cache_path: Path, ttl_hours: float = 24,
                 country: str = "mx", hl: str = "es"):
        self.zone = None  # se resuelve en cada fetch desde env
        self.cache_path = cache_path
        self.ttl = timedelta(hours=ttl_hours)
        self.country = country
        self.hl = hl
        self.calls = 0                 # consultas de red hechas esta corrida
        self._cache = self._load()

    def available(self) -> bool:
        import os
        return bool(os.environ.get("BRIGHTDATA_SERP_ZONE")
                    and os.environ.get("BRIGHTDATA_API_TOKEN"))

    # ---- caché persistente ----
    def _load(self) -> dict:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def _fresh(self, entry: dict) -> bool:
        try:
            ts = datetime.fromisoformat(entry["ts"])
        except (KeyError, ValueError, TypeError):
            return False
        return datetime.now(timezone.utc) - ts < self.ttl

    # ---- consulta ----
    def lookup(self, query: str, budget_left: int) -> list[Product]:
        """Devuelve productos de Google Shopping para `query`.
        Usa caché si está fresca (no consume presupuesto). Si no, consulta solo
        si budget_left > 0. Devuelve [] si no hay presupuesto o no hay datos."""
        key = re.sub(r"\s+", " ", query.strip().lower())
        cached = self._cache.get(key)
        if cached and self._fresh(cached):
            return self._to_products(cached.get("items", []))
        if budget_left <= 0:
            return []                  # sin presupuesto: no consultamos

        items = self._query_serp(query)
        self._cache[key] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }
        self.calls += 1
        return self._to_products(items)

    _debugged = False

    def _query_serp(self, query: str) -> list[dict]:
        zone = __import__("os").environ.get("BRIGHTDATA_SERP_ZONE")
        url = (f"{SERP_BASE}?tbm=shop&q={_q(query)}"
               f"&gl={self.country}&hl={self.hl}&brd_json=1")
        try:
            # falla rápido: timeout corto y sin reintentos largos (evita que la
            # corrida se eternice si la SERP no responde).
            body = brightdata.fetch(url, country=self.country, zone=zone,
                                    timeout=25, retries=1)
        except brightdata.FetchError as e:
            if not GoogleShopping._debugged:
                print(f"   [gshop] error SERP para '{query}': {e}")
                GoogleShopping._debugged = True
            return []
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            if not GoogleShopping._debugged:
                print(f"   [gshop] SERP no-JSON ({len(body)}b): {body[:300]}")
                GoogleShopping._debugged = True
            return []
        items = _parse_shopping(data)
        if not GoogleShopping._debugged:
            top = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            print(f"   [gshop] OK '{query}': {len(items)} items; claves={top}")
            GoogleShopping._debugged = True
        return items

    @staticmethod
    def _to_products(items: list[dict]) -> list[Product]:
        out = []
        for it in items:
            price = it.get("price")
            if not price:
                continue
            out.append(Product(
                store="google", name=it.get("title") or "?",
                url=it.get("link") or "https://www.google.com/search?tbm=shop",
                price=float(price), brand=None,
                extra={"merchant": it.get("merchant")},
            ))
        return out


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


def _price(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    m = re.search(r"[\d][\d,]*\.?\d*", str(v).replace(",", ""))
    if not m:
        return None
    try:
        f = float(m.group(0))
        return f if f > 0 else None
    except ValueError:
        return None


def _parse_shopping(data: dict) -> list[dict]:
    """Extrae items {title, price, merchant, link} del JSON parseado de SERP.

    Tolerante a variaciones de esquema: busca recursivamente dicts que tengan
    título + precio. Cubre claves comunes de Bright Data SERP shopping.
    """
    items: list[dict] = []

    def title_of(d: dict):
        for k in ("title", "name", "product_title"):
            if d.get(k):
                return str(d[k])
        return None

    def merchant_of(d: dict):
        for k in ("merchant", "source", "seller", "store", "shop"):
            if d.get(k):
                return str(d[k])
        return None

    def walk(node):
        if isinstance(node, dict):
            t = title_of(node)
            pr = None
            for k in ("price", "extracted_price", "current_price", "offer_price"):
                if k in node:
                    pr = _price(node[k])
                    if pr:
                        break
            if t and pr:
                items.append({"title": t, "price": pr,
                              "merchant": merchant_of(node),
                              "link": node.get("link") or node.get("url")})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return items
