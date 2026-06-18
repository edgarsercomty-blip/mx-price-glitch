"""Probe de Walmart MX vía Bright Data: descarga la búsqueda y vuelca la
estructura de __NEXT_DATA__ para construir el adaptador.

  python -m src.probe_walmart "taladro"
"""
from __future__ import annotations

import json
import re
import sys

from . import brightdata

NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def find_item_lists(node, path="", depth=0):
    """Encuentra listas de dicts que parezcan productos (con priceInfo/price)."""
    if depth > 14:
        return
    if isinstance(node, list):
        if node and isinstance(node[0], dict):
            keys = set(node[0].keys())
            if keys & {"priceInfo", "price", "primaryOffer", "name", "usItemId"}:
                yield path, node
        for i, v in enumerate(node[:3]):
            yield from find_item_lists(v, f"{path}[{i}]", depth + 1)
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from find_item_lists(v, f"{path}/{k}", depth + 1)


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "taladro"
    url = f"https://www.walmart.com.mx/search?q={query}"
    print(f"fetch {url}")
    try:
        html = brightdata.fetch(url, country="mx", timeout=60, retries=2)
    except brightdata.FetchError as e:
        print(f"FETCH ERROR: {e}")
        return
    print(f"html bytes: {len(html)}")
    if "/blocked" in html[:2000] or "blocked?url" in html[:2000]:
        print("OJO: parece página de bloqueo aún con Bright Data.")
    m = NEXT.search(html)
    if not m:
        print("Sin __NEXT_DATA__. Primeros 400:")
        print(html[:400])
        return
    data = json.loads(m.group(1))
    lists = list(find_item_lists(data))
    print(f"listas candidatas de productos: {len(lists)}")
    for path, lst in lists[:4]:
        print(f"\n--- {path}  ({len(lst)} items) ---")
        it = lst[0]
        print("keys:", sorted(it.keys())[:40])
        for k in ("name", "model", "brand", "canonicalUrl", "usItemId",
                  "priceInfo", "price", "primaryOffer"):
            if k in it:
                print(f"  {k} = {json.dumps(it[k], ensure_ascii=False)[:160]}")


if __name__ == "__main__":
    main()
