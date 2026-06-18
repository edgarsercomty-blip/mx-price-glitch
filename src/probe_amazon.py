"""Probe de Amazon MX vía Bright Data: explora la búsqueda y una página de
producto para ver dónde está el precio y el "Vendido/Enviado por Amazon".

  python -m src.probe_amazon "taladro"
"""
from __future__ import annotations

import re
import sys

from bs4 import BeautifulSoup

from . import brightdata


def parse_search(html: str):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div[data-asin][data-component-type='s-search-result']")
    print(f"tarjetas de resultado: {len(cards)}")
    out = []
    for c in cards[:8]:
        asin = c.get("data-asin")
        h2 = c.select_one("h2")
        title = h2.get_text(" ", strip=True) if h2 else None
        price = c.select_one(".a-price .a-offscreen")
        price = price.get_text(strip=True) if price else None
        # pistas de fulfillment en la tarjeta
        txt = c.get_text(" ", strip=True).lower()
        hint = [w for w in ("amazon", "vendido", "enviado", "prime")
                if w in txt]
        out.append((asin, price, title))
        print(f"  asin={asin} price={price} hints={hint} | {str(title)[:60]}")
    return out


def probe_pdp(asin: str):
    url = f"https://www.amazon.com.mx/dp/{asin}"
    print(f"\nPDP {url}")
    try:
        html = brightdata.fetch(url, country="mx", timeout=60, retries=2)
    except brightdata.FetchError as e:
        print(f"  ERROR: {e}")
        return
    soup = BeautifulSoup(html, "html.parser")
    for sel in ("#sellerProfileTriggerId", "#merchant-info",
                "#tabular-buybox", "#fulfillerInfoFeature_feature_div",
                "#merchantInfoFeature_feature_div", "#bylineInfo"):
        el = soup.select_one(sel)
        if el:
            print(f"  [{sel}] -> {el.get_text(' ', strip=True)[:160]}")
    # búsqueda textual de patrones clave
    body = soup.get_text(" ", strip=True)
    for pat in ("Vendido por", "Enviado por", "Se envía desde", "Vendido y enviado por"):
        m = re.search(re.escape(pat) + r"[:\s]+([A-Za-z0-9ÁÉÍÓÚáéíóúñ.\- ]{2,40})", body)
        if m:
            print(f"  patrón '{pat}': {m.group(1).strip()}")


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "taladro"
    url = f"https://www.amazon.com.mx/s?k={query}"
    print(f"fetch {url}")
    try:
        html = brightdata.fetch(url, country="mx", timeout=60, retries=2)
    except brightdata.FetchError as e:
        print(f"FETCH ERROR: {e}")
        return
    print(f"html bytes: {len(html)}")
    items = parse_search(html)
    if items and items[0][0]:
        probe_pdp(items[0][0])


if __name__ == "__main__":
    main()
