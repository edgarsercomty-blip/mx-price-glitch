"""Consulta Keepa API para historial de precios de Amazon.com.mx (domain=12).

Keepa devuelve el promedio de 90 días del precio de Amazon para un ASIN.
Si el precio actual está significativamente por debajo de ese promedio,
el deal es real aunque no haya comparables en otras tiendas MX.

Endpoint:
  GET https://api.keepa.com/product
  ?key=KEY&domain=12&asin=ASIN&stats=90&offers=0

Precios en Keepa: enteros en centavos de la moneda local.
  Para Amazon.com.mx (MXN): divide entre 100 para obtener pesos.
  El valor -1 significa "sin datos".

stats.avg90[0] = promedio de 90 días del precio Amazon (listing directo).
stats.avg90[7] = promedio de 90 días del Buy Box price.

Requiere: secret KEEPA_API_KEY.
Sin la clave todos los métodos devuelven None silenciosamente.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

_API = "https://api.keepa.com/product"


def _asin_from_url(url: str | None) -> str | None:
    """Extrae el ASIN de una URL de Amazon (/dp/XXXXXXXXXX)."""
    m = re.search(r"/dp/([A-Z0-9]{10})", url or "")
    return m.group(1) if m else None


class KeepaClient:
    def __init__(self, cache_path: Path, ttl_hours: float = 24):
        self._key = os.environ.get("KEEPA_API_KEY", "")
        self._path = cache_path
        self._ttl = timedelta(hours=ttl_hours)
        self.calls_made = 0          # consultas de red en esta corrida
        self._cache: dict = {}
        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def available(self) -> bool:
        return bool(self._key)

    def lookup_url(self, url: str | None) -> float | None:
        """Precio promedio 90d para una URL de Amazon (extrae ASIN internamente)."""
        asin = _asin_from_url(url)
        return self.lookup(asin) if asin else None

    def lookup(self, asin: str | None) -> float | None:
        """Precio promedio de 90 días en Amazon MX (MXN), o None si no hay datos."""
        if not self._key or not asin:
            return None
        entry = self._cache.get(asin)
        if entry:
            try:
                ts = datetime.fromisoformat(entry["ts"])
                if datetime.now(timezone.utc) - ts < self._ttl:
                    return entry.get("avg90")  # puede ser None si Keepa no tenía datos
            except (KeyError, ValueError, TypeError):
                pass
        avg90 = self._fetch_avg90(asin)
        self._cache[asin] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "avg90": avg90,
        }
        return avg90

    def _fetch_avg90(self, asin: str) -> float | None:
        self.calls_made += 1
        params = {
            "key": self._key,
            "domain": 12,       # Amazon.com.mx
            "asin": asin,
            "stats": 90,        # calcular estadísticas de los últimos 90 días
            "offers": 0,        # no necesitamos oferta detallada; ahorra tokens
        }
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                r = requests.get(_API, params=params, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    return _extract_avg90(data)
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = Exception(f"HTTP {r.status_code}")
                    time.sleep(min(2 ** attempt, 10))
                    continue
                # 400/401/403 → clave inválida o ASIN no existe; no reintentar
                print(f"   [keepa] HTTP {r.status_code} para ASIN {asin}: {r.text[:120]}")
                return None
            except (requests.RequestException, ValueError) as e:
                last_err = e
                time.sleep(min(2 ** attempt, 10))
        print(f"   [keepa] falló ASIN {asin}: {last_err}")
        return None

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _extract_avg90(data: dict) -> float | None:
    """Extrae el precio promedio de 90 días del JSON de Keepa."""
    try:
        prods = data.get("products") or []
        if not prods:
            return None
        st = prods[0].get("stats") or {}
        # avg90: [amazon, marketplace, new, used, ..., buybox, ...]
        # índice 0 = Amazon listing; índice 7 = Buy Box (generalmente más relevante)
        avg90 = st.get("avg90") or []
        # Prefiere Buy Box (idx 7) si existe; si no, Amazon listing (idx 0)
        val: int | None = None
        if len(avg90) > 7 and avg90[7] not in (None, -1):
            val = avg90[7]
        elif avg90 and avg90[0] not in (None, -1):
            val = avg90[0]
        if val is None or val < 0:
            return None
        return round(val / 100, 2)  # centavos → MXN
    except (KeyError, IndexError, TypeError, ValueError):
        return None
