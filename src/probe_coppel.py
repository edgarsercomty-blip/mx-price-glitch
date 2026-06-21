"""Probe de Coppel (Next.js, vía Bright Data): mapea __NEXT_DATA__ de la
búsqueda para construir el adaptador.

  python -m src.probe_coppel "taladro"
"""
from __future__ import annotations

import json
import re
import sys

from . import brightdata

NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
PRICE_KEYS = ("price", "salePrice", "listPrice", "discountPrice", "originalPrice",
              "finalPrice", "currentPrice", "precio", "priceWithDiscount",
              "maximumPrice", "minimumPrice", "regularPrice")


def find_products(node, depth=0):
    if depth > 16:
        return
    if isinstance(node, dict):
        has_price = any(k in node for k in PRICE_KEYS)
        has_name = any(k in node for k in ("name", "title", "productName", "description"))
        if has_price and has_name:
            yield node
        for v in node.values():
            yield from find_products(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from find_products(v, depth + 1)


def main() -> None:
    q = sys.argv[1] if len(sys.argv) > 1 else "taladro"
    url = f"https://www.coppel.com/{q}"
    print(f"fetch {url}")
    try:
        html = brightdata.fetch(url, country="mx", timeout=60, retries=2)
    except brightdata.FetchError as e:
        print(f"ERROR: {e}"); return
    print(f"bytes: {len(html)}")
    m = NEXT.search(html)
    if not m:
        print("sin __NEXT_DATA__. snippet:", html[:200]); return
    data = json.loads(m.group(1))
    prods = list(find_products(data))
    print(f"posibles productos: {len(prods)}")
    seen_shapes = 0
    for p in prods[:6]:
        pricek = {k: p[k] for k in PRICE_KEYS if k in p}
        idk = {k: p[k] for k in ("id", "sku", "productId", "ean", "code", "slug", "url") if k in p}
        nm = p.get("name") or p.get("title") or p.get("productName")
        print(f"  name={str(nm)[:50]!r} price={json.dumps(pricek, ensure_ascii=False)[:120]} id={json.dumps(idk, ensure_ascii=False)[:90]}")
        seen_shapes += 1
    if not prods:
        # buscar nombres de claves con 'price'
        keys = set(re.findall(r'"([a-zA-Z]*[Pp]rice[a-zA-Z]*)"', m.group(1)))
        print("claves *price* vistas:", sorted(keys)[:20])


if __name__ == "__main__":
    main()
