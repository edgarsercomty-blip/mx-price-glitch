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
    # buscar hosts/endpoints de API y keys que use el frontend
    blob = m.group(1)
    hosts = set(re.findall(r'https?://[a-z0-9.\-]*(?:coppel|algolia|cnstrc|bloomreach|search|api)[a-z0-9.\-]*[/a-z0-9._\-]*', blob, re.I))
    print("hosts/api en __NEXT_DATA__:")
    for h in sorted(hosts)[:25]:
        print("  ", h[:140])
    for kk in ("apiKey", "api_key", "x-api-key", "appId", "applicationId", "graphql", "searchKey", "subscriptionKey", "Ocp-Apim"):
        n = blob.count(kk)
        if n:
            mm = re.search(re.escape(kk) + r'"?\s*[:=]\s*"?([A-Za-z0-9_\-]{6,40})', blob)
            print(f"  {kk}: x{n} ej={mm.group(1) if mm else '?'}")

    # buscar el endpoint de búsqueda en los bundles JS
    chunks = re.findall(r'/_next/static/[^"\']+\.js', html)
    chunks = sorted(set(chunks))[:12]
    print(f"\nrevisando {len(chunks)} chunks JS...")
    endpoints: set[str] = set()
    for c in chunks:
        try:
            js = brightdata.fetch(f"https://www.coppel.com{c}", country="mx",
                                  timeout=30, retries=1)
        except brightdata.FetchError:
            continue
        for pat in re.findall(r'["\'`](https?://[a-z0-9.\-]+/[^"\'`]*(?:search|product|catalog|graphql)[^"\'`]*)', js, re.I):
            endpoints.add(pat[:160])
        for pat in re.findall(r'["\'`](/[a-z0-9/_\-]*(?:search|products?|catalog|graphql)[a-z0-9/_\-]*)["\'`]', js, re.I):
            endpoints.add(pat[:160])
    print("posibles endpoints de búsqueda:")
    for e in sorted(endpoints)[:30]:
        print("  ", e)

    data = json.loads(blob)
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
