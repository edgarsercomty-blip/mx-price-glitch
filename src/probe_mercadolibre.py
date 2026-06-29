"""Probe de Mercado Libre: baja el HTML de listado y muestra qué trae el
adaptador, para validar selectores si dejan de funcionar.

  python -m src.probe_mercadolibre "refrigerador"
"""
from __future__ import annotations

import sys

from .adapters.mercadolibre import MercadoLibreAdapter


def main() -> None:
    q = sys.argv[1] if len(sys.argv) > 1 else "refrigerador"
    ad = MercadoLibreAdapter({"key": "mercadolibre", "search_terms": [q],
                              "max_products_per_term": 20})
    url = ad._search_url(q)
    print(f"fetch {url}")
    html = ad._fetch(url)
    if not html:
        print("ERROR: sin HTML"); return
    print(f"bytes: {len(html)}")
    prods = ad._parse(html)
    print(f"productos parseados: {len(prods)}")
    for p in prods[:8]:
        d = f"(lista ${p.list_price:,.0f})" if p.list_price else ""
        print(f"  ${p.price:,.0f} {d}  {p.name[:60]!r}  model={p.model}")
        print(f"      {p.url[:90]}")


if __name__ == "__main__":
    main()
