"""Capa de fetch a través de Bright Data Web Unlocker.

Pasa la mayoría de las protecciones anti-bot (Cloudflare/Akamai/captcha) de las
tiendas. Sirve tanto para descargar HTML como para llamar endpoints JSON
internos (p. ej. la API de catálogo de VTEX), porque devuelve el cuerpo crudo.

Variables de entorno requeridas:
  BRIGHTDATA_API_TOKEN  -> token del API de Bright Data
  BRIGHTDATA_ZONE       -> nombre de la zona Web Unlocker (p. ej. "web_unlocker1")

Modo sin token: si DRY_RUN=1, no se hace red; los adaptadores deben usar
fixtures locales. Útil para probar el pipeline y la creación del Issue.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

API_URL = "https://api.brightdata.com/request"


class FetchError(RuntimeError):
    pass


def _dry_run() -> bool:
    return os.environ.get("DRY_RUN", "") == "1"


def fetch(url: str, *, country: str = "mx", timeout: int = 60,
          retries: int = 3, expect: str | None = None,
          unblock_headers: dict[str, str] | None = None,
          zone: str | None = None) -> str:
    """Descarga `url` a través de Bright Data y devuelve el cuerpo como texto.

    `country`         geolocalización del request (mx => precios/stock de México).
    `expect`          CSS selector/texto a esperar antes de devolver la página
                      (x-unblock-expect): evita HTML parcial en páginas que
                      pintan precios por JS.
    `unblock_headers` cabeceras x-unblock-* extra (p. ej. x-unblock-zipcode para
                      precios regionales de Amazon).
    Reintenta con backoff ante errores transitorios (429/5xx/timeouts).
    """
    if _dry_run():
        raise FetchError(f"DRY_RUN activo: no se descarga {url}")

    token = os.environ.get("BRIGHTDATA_API_TOKEN")
    zone = zone or os.environ.get("BRIGHTDATA_ZONE")
    if not token or not zone:
        raise FetchError(
            "Faltan BRIGHTDATA_API_TOKEN y/o zona en el entorno. "
            "Configúralos como secrets del repositorio."
        )

    # Geo por defecto desde el entorno (permite cambiarla sin tocar código).
    country = os.environ.get("BRIGHTDATA_COUNTRY", country)

    unblock = dict(unblock_headers or {})
    if expect:
        unblock["x-unblock-expect"] = expect

    payload: dict[str, Any] = {
        "zone": zone,
        "url": url,
        "format": "raw",
        "country": country,
    }
    if unblock:
        payload["headers"] = unblock

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(API_URL, headers=headers,
                              data=json.dumps(payload), timeout=timeout)
            if r.status_code == 200:
                return r.text
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = FetchError(f"HTTP {r.status_code} en {url}")
            else:
                raise FetchError(f"HTTP {r.status_code} en {url}: {r.text[:300]}")
        except requests.RequestException as e:  # red/timeout
            last_err = e
        sleep = min(2 ** attempt, 15)
        time.sleep(sleep)

    raise FetchError(f"Falló la descarga de {url}: {last_err}")


def fetch_json(url: str, **kw: Any) -> Any:
    """Igual que fetch() pero parsea la respuesta como JSON."""
    body = fetch(url, **kw)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise FetchError(f"Respuesta no-JSON de {url}: {e}") from e
