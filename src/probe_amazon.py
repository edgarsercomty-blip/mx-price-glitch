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


def inspect_asin(asin: str):
    url = f"https://www.amazon.com.mx/dp/{asin}"
    print(f"inspect {url}")
    try:
        html = brightdata.fetch(url, country="mx", timeout=60, retries=2)
    except brightdata.FetchError as e:
        print(f"  ERROR: {e}")
        return
    print(f"  html bytes: {len(html)}")
    soup = BeautifulSoup(html, "html.parser")
    t = soup.select_one("#productTitle")
    print("  titulo:", t.get_text(strip=True)[:90] if t else None)
    # precio actual
    cur = soup.select_one("#corePrice_feature_div .a-offscreen") \
        or soup.select_one(".a-price .a-offscreen")
    print("  precio actual:", cur.get_text(strip=True) if cur else None)
    # precio de lista / tachado
    strike = soup.select_one(".a-price.a-text-price .a-offscreen") \
        or soup.select_one("span[data-a-strike='true'] .a-offscreen")
    print("  precio lista:", strike.get_text(strip=True) if strike else "NO HAY")
    # cupón
    body = soup.get_text(" ", strip=True)
    cm = re.search(r"(cup[oó]n[^.]{0,60}|ahorra[^.]{0,40}al aplicar[^.]{0,30})",
                   body, re.I)
    print("  cupon:", cm.group(0)[:80] if cm else "no")
    # vendedor/envío
    for sel in ("#sellerProfileTriggerId", "#merchantInfoFeature_feature_div"):
        el = soup.select_one(sel)
        if el:
            print(f"  [{sel}]:", el.get_text(' ', strip=True)[:80])


def inspect_deals():
    for path in ("/deals", "/gp/goldbox",
                 "/s?k=ofertas&rh=p_n_deal_type%3A23655121011"):
        url = f"https://www.amazon.com.mx{path}"
        print(f"\n=== {url} ===")
        try:
            html = brightdata.fetch(url, country="mx", timeout=60, retries=2)
        except brightdata.FetchError as e:
            print(f"  ERROR: {e}"); continue
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("div[data-asin][data-component-type='s-search-result']")
        any_asin = soup.select("[data-asin]")
        prices = soup.select(".a-price .a-offscreen")
        print(f"  bytes={len(html)} s-cards={len(cards)} "
              f"data-asin={len(any_asin)} precios={len(prices)}")
        # ¿hay un blob JSON con deals?
        for kw in ('"dealId"', '"dealPrice"', 'data-testid="deal-card"',
                   '__NEXT_DATA__', '"asin"'):
            print(f"    {kw}: {html.count(kw)}")


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "taladro"
    if arg == "deals":
        inspect_deals()
        return
    if re.fullmatch(r"B0[A-Z0-9]{8}", arg):
        inspect_asin(arg)
        return
    query = arg
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
