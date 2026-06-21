"""Probe de páginas de OFERTAS para Liverpool / Walmart / Home Depot.

Prueba varias URLs candidatas y reporta cuántos productos trae cada una, para
decidir el 'deals mode' de cada tienda.

  python -m src.probe_deals liverpool
  python -m src.probe_deals walmart
  python -m src.probe_deals homedepot
"""
from __future__ import annotations

import sys

import requests

from . import brightdata

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

CANDIDATES = {
    "liverpool": [
        "https://www.liverpool.com.mx/tienda/ofertas",
        "https://www.liverpool.com.mx/tienda/coleccion/ofertas",
        "https://www.liverpool.com.mx/tienda/buscar?s=ofertas",
        "https://www.liverpool.com.mx/tienda/buscar?s=&fh_sort_by=discount",
    ],
    "walmart": [
        "https://www.walmart.com.mx/cp/ofertas",
        "https://www.walmart.com.mx/search?q=ofertas",
        "https://www.walmart.com.mx/browse/ofertas",
        "https://www.walmart.com.mx/cp/eventos/ofertas",
    ],
    "homedepot": [
        "https://www.homedepot.com.mx/search/resources/store/10351/productview/bySearchTerm/oferta",
        "https://www.homedepot.com.mx/search/resources/store/10351/productview/bySearchTerm/descuento",
        "https://www.homedepot.com.mx/search/resources/store/10351/productview/bySearchTerm/liquidacion",
    ],
    "coppel": [
        "https://www.coppel.com/api/catalog_system/pub/products/search?ft=taladro&_from=0&_to=9",
        "https://www.coppel.com/api/catalog_system/pub/products/search/taladro",
        "https://www.coppel.com/taladro",
    ],
}

# señal de "producto con precio" por tienda en el cuerpo
SIGNAL = {
    "liverpool": '"maximumListPrice"',
    "walmart": '"linePrice"',
    "homedepot": '"catalogEntryView"',
    "coppel": '"commertialOffer"',
}


def fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA,
                         "Accept": "application/json, text/html"}, timeout=25)
        if r.status_code == 200 and "/blocked" not in r.url:
            return r.text
    except requests.RequestException:
        pass
    try:
        return brightdata.fetch(url, country="mx", timeout=45, retries=1)
    except brightdata.FetchError as e:
        print(f"   BD error: {e}")
        return None


def main() -> None:
    store = sys.argv[1] if len(sys.argv) > 1 else "liverpool"
    sig = SIGNAL.get(store, '"price"')
    for url in CANDIDATES.get(store, []):
        print(f"\n=== {url} ===")
        body = fetch(url)
        if not body:
            print("   (sin cuerpo)")
            continue
        print(f"   bytes={len(body)}  señal {sig}={body.count(sig)}  "
              f"price={body.count(chr(34)+'Price'+chr(34))} ean={body.count('ean')}")
        if body.count(sig) == 0:
            print(f"   snippet: {body[:160]!r}")


if __name__ == "__main__":
    main()
