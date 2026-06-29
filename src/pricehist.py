"""Historial de precios auto-hospedado (nuestro "Keepa" gratis).

El scanner ya recorre miles de productos en cada corrida (cada 20 min el carril
rápido, cada 6h el completo). En vez de tirar esos precios, los persistimos: con
el tiempo tenemos el precio REAL al que cada producto se ha vendido en su tienda.

Ese promedio histórico es mejor señal de error de precio que el `list_price`
(que el vendedor suele inflar): si el precio actual está muy por debajo de su
propia mediana histórica, es un glitch real — y funciona para CUALQUIER tienda,
no solo Amazon, sin pagar API externa.

Persistencia: data/price_history.json
  { "tienda|pid": {"name": str, "pts": [["YYYY-MM-DD", precio], ...]} }

Acotado: un punto por producto por DÍA (dedup) y ventana móvil de N días, para
que el archivo no crezca sin control en el repo público.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .models import Product


def _pid(p: Product) -> str:
    e = p.extra or {}
    return str(e.get("asin") or e.get("usItemId") or e.get("productId")
               or e.get("sku") or e.get("partNumber") or p.url)


def _key(p: Product) -> str:
    return f"{p.store}|{_pid(p)}"


class PriceHistory:
    def __init__(self, path: Path, window_days: int = 90, min_points: int = 3):
        self.path = path
        self.window_days = window_days
        self.min_points = min_points       # mínimo de días distintos para confiar
        self._data: dict[str, dict] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _window_prices(self, key: str) -> list[float]:
        ent = self._data.get(key)
        if not ent:
            return []
        cutoff = self._today_minus(self.window_days)
        return [pr for (d, pr) in ent.get("pts", []) if d >= cutoff and pr > 0]

    @staticmethod
    def _today_minus(days: int) -> str:
        from datetime import timedelta
        return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    def baseline(self, p: Product) -> float | None:
        """Mediana del precio histórico del producto (en su ventana), o None si
        aún no hay suficientes observaciones (días distintos) para confiar."""
        prices = self._window_prices(_key(p))
        if len(prices) < self.min_points:
            return None
        return round(statistics.median(prices), 2)

    def record(self, products: list[Product]) -> int:
        """Anota el precio de HOY de cada producto (un punto por día). Devuelve
        cuántas series se tocaron. Llamar DESPUÉS de verificar, para que el
        baseline de esta corrida no se contamine con el precio actual."""
        today = self._today()
        touched = 0
        for p in products:
            if p.price <= 0:
                continue
            k = _key(p)
            ent = self._data.setdefault(k, {"name": p.name[:80], "pts": []})
            pts = ent["pts"]
            if pts and pts[-1][0] == today:
                pts[-1][1] = p.price            # actualiza el punto de hoy
            else:
                pts.append([today, p.price])
                touched += 1
            ent["name"] = p.name[:80]
        return touched

    def prune(self) -> None:
        """Recorta puntos fuera de la ventana y elimina series ya vacías."""
        cutoff = self._today_minus(self.window_days)
        for k in list(self._data.keys()):
            pts = [pt for pt in self._data[k].get("pts", []) if pt[0] >= cutoff]
            if pts:
                self._data[k]["pts"] = pts
            else:
                del self._data[k]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")

    @property
    def n_series(self) -> int:
        return len(self._data)
