"""Probe aislado de la SERP API de Google Shopping.

Hace UNA consulta y vuelca el estado para diagnosticar (rápido y barato):
  python -m src.probe_serp "OLED55C5PSA"

Imprime: status del fetch, tamaño, claves de primer nivel del JSON y cuántos
items detecta el parser. Sirve para ajustar el parser sin correr todo el flujo.
"""
from __future__ import annotations

import json
import os
import sys

from . import brightdata
from .gshop import _parse_shopping

SERP_BASE = "https://www.google.com/search"


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "OLED55C5PSA"
    zone = os.environ.get("BRIGHTDATA_SERP_ZONE")
    print(f"zone={zone!r} query={query!r}")
    from urllib.parse import quote
    url = f"{SERP_BASE}?tbm=shop&q={quote(query)}&gl=mx&hl=es&brd_json=1"
    print(f"url={url}")
    try:
        body = brightdata.fetch(url, country="mx", zone=zone,
                                timeout=30, retries=1)
    except brightdata.FetchError as e:
        print(f"FETCH ERROR: {e}")
        return
    print(f"body bytes: {len(body)}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print("NO-JSON. Primeros 600 chars:")
        print(body[:600])
        return
    if isinstance(data, dict):
        print("claves nivel 1:", list(data.keys()))
        for k, v in data.items():
            if isinstance(v, list):
                print(f"  lista '{k}': {len(v)} items; "
                      f"ejemplo claves: "
                      f"{list(v[0].keys()) if v and isinstance(v[0], dict) else '-'}")
    items = _parse_shopping(data)
    print(f"parser detecta {len(items)} items con precio")
    for it in items[:5]:
        print("   ", it)


if __name__ == "__main__":
    main()
