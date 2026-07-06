"""Probe de Liverpool: diagnostica por qué el adaptador dejó de traer
productos (0 en todas las corridas desde 2026-06-30). Prueba la ruta
directa (requests) y el fallback de Bright Data por separado, e inspecciona
si __NEXT_DATA__ sigue presente y con la forma esperada.

  python -m src.probe_liverpool "pantalla"
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import quote

import requests

from . import brightdata
from .adapters.liverpool import UA, _NEXT, _walk_products

BASE = "https://www.liverpool.com.mx"


def _report(label: str, html: str | None) -> None:
    if not html:
        print(f"[{label}] sin HTML (fetch falló)")
        return
    print(f"[{label}] bytes: {len(html)}")
    m = _NEXT.search(html)
    if not m:
        print(f"[{label}] sin __NEXT_DATA__. snippet: {html[:200]!r}")
        return
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"[{label}] __NEXT_DATA__ presente pero no parsea como JSON: {e}")
        return
    prods = list(_walk_products(data))
    print(f"[{label}] __NEXT_DATA__ parseado OK, productos con forma esperada: {len(prods)}")
    if not prods:
        # ¿existen los nombres de llave en algún lado del blob, aunque con otra forma?
        blob = m.group(1)
        for k in ("maximumListPrice", "maximumPromoPrice", "minimumPromoPrice",
                  "productId", "availability"):
            print(f"    ocurrencias de {k!r}: {blob.count(k)}")
    for p in prods[:5]:
        print(f"    {p.get('title') or p.get('name')!r} "
              f"promo={p.get('maximumPromoPrice')} lista={p.get('maximumListPrice')}")


def main() -> None:
    q = sys.argv[1] if len(sys.argv) > 1 else "pantalla"
    url = f"{BASE}/tienda/buscar?s={quote(q)}"
    print(f"URL: {url}\n")

    print("--- 1) requests directo (lo que usa el adaptador primero) ---")
    try:
        r = requests.Session()
        r.headers.update({"User-Agent": UA})
        resp = r.get(url, timeout=25)
        print(f"status: {resp.status_code}")
        _report("directo", resp.text if resp.status_code == 200 else None)
    except requests.RequestException as e:
        print(f"ERROR requests: {e}")

    print("\n--- 2) Bright Data (fallback del adaptador) ---")
    try:
        html = brightdata.fetch(url, timeout=25, retries=1)
        _report("brightdata", html)
    except brightdata.FetchError as e:
        print(f"ERROR brightdata: {e}")


if __name__ == "__main__":
    main()
