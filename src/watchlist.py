"""Lista de vigilancia para alertas de RESTOCK.

Los productos que se detectan como deal se guardan en data/watchlist.json. En
cada corrida se revisa su disponibilidad actual (usando los productos ya
escaneados, sin costo extra): si uno que estaba AGOTADO vuelve a estar
disponible, se genera una alerta de restock.

También se puede sembrar a mano (p. ej. la iMac de Palacio que se agotó) con
available=false para que avise cuando regrese.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .detect import Finding
from .models import Product


def _key(url: str) -> str:
    return (url or "").split("?")[0].rstrip("/").lower()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(s: str):
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def update_and_detect(products: list[Product], confirmed: list[Finding],
                      path: Path, ttl_days: int = 60) -> list[Finding]:
    """Actualiza la watchlist con el escaneo actual y devuelve los hallazgos de
    RESTOCK (productos vigilados que pasaron de agotado -> disponible)."""
    wl: dict = {}
    if path.exists():
        try:
            wl = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            wl = {}

    now = _now()
    iso = now.isoformat()
    current = {_key(p.url): p for p in products if p.url}

    restocks: list[Finding] = []
    # 1) refrescar disponibilidad de lo vigilado contra el escaneo actual
    for k, item in list(wl.items()):
        cur = current.get(k)
        if cur is None:
            continue
        was_available = bool(item.get("available", True))
        if cur.available and not was_available:
            restocks.append(Finding(
                kind="restock", product=cur,
                discount_pct=float(item.get("discount") or 0),
                detail=(f"🔁 DE NUEVO DISPONIBLE en {cur.store}: ${cur.price:,.0f} "
                        f"(estaba agotado)")))
        item["available"] = cur.available
        item["price"] = cur.price
        item["last_seen"] = iso

    # 2) agregar/actualizar los confirmados de esta corrida (deal vigente)
    for f in confirmed:
        p = f.product
        k = _key(p.url)
        if not k:
            continue
        prev = wl.get(k, {})
        wl[k] = {
            "store": p.store, "name": p.name, "url": p.url,
            "price": p.price, "available": True,
            "discount": f.discount_pct,
            "added": prev.get("added", iso), "last_seen": iso,
        }

    # 3) expirar lo viejo
    cutoff = now - timedelta(days=ttl_days)
    for k in [k for k, v in wl.items()
              if not (_parse(v.get("last_seen", "")) and _parse(v["last_seen"]) > cutoff)]:
        del wl[k]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(wl, ensure_ascii=False, indent=2), encoding="utf-8")
    return restocks
