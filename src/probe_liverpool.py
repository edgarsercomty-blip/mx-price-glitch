"""Probe de Liverpool: corre el adaptador real contra una búsqueda y muestra
los productos parseados, para validar rápido si el sitio volvió a cambiar.

  python -m src.probe_liverpool "pantalla"
"""
from __future__ import annotations

import sys

from .adapters.liverpool import LiverpoolAdapter


def main() -> None:
    q = sys.argv[1] if len(sys.argv) > 1 else "pantalla"
    ad = LiverpoolAdapter({"key": "liverpool", "search_terms": [q], "pages_per_term": 1})
    url = f"{ad.base}/tienda?s={q}"
    print(f"URL: {url}")
    html = ad._get_html(url)
    if not html:
        print("ERROR: sin HTML (falló requests directo y el fallback de Bright Data)")
        return
    print(f"bytes: {len(html)}")
    raws = ad._products_in(html)
    print(f"bloques de producto encontrados: {len(raws)}")
    prods = [p for raw in raws if (p := ad._to_product(raw))]
    print(f"productos válidos (con precio): {len(prods)}")
    for p in prods[:10]:
        d = f"(lista ${p.list_price:,.0f})" if p.list_price else ""
        print(f"  ${p.price:,.0f} {d}  {p.name[:60]!r}  marca={p.brand} model={p.model}")
        print(f"      {p.url}")


if __name__ == "__main__":
    main()
