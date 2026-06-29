"""Punto de entrada: escanea las tiendas, detecta y escribe los resultados.

Uso:
  python -m src.main                 # corrida normal (requiere Bright Data)
  python -m src.main --dry-run       # usa fixtures locales, sin red
  python -m src.main --stores coppel,homedepot
  python -m src.main --threshold 40
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import detect
from .adapters import build_adapter
from .detect import Finding
from .models import Product
from .report import append_history, write_new_report, write_outputs
from .verify import verify

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "stores.yaml"
FIXTURES = ROOT / "tests" / "fixtures"
SEEN = ROOT / "data" / "seen.json"
SEEN_TTL_DAYS = 30          # tras este tiempo, un hallazgo que reaparece se vuelve a avisar
# Si el precio del mismo producto (misma URL/PID) varía menos de este % respecto
# al último aviso, se suprime: es la misma oferta fluctuando, no un deal nuevo.
PRICE_TOL = 0.05


def _finding_key(f: Finding) -> str:
    """Identidad exacta: tienda + producto + precio redondeado."""
    p = f.product
    pid = (p.extra or {}).get("productId") or (p.extra or {}).get("partNumber") or p.url
    return f"{p.store}|{pid}|{int(round(p.price))}"


def _pid_key(f: Finding) -> str:
    """Identidad sin precio (para dedup suave de micro-variaciones)."""
    p = f.product
    pid = (p.extra or {}).get("productId") or (p.extra or {}).get("partNumber") or p.url
    return f"~{p.store}|{pid}"


def _entry_ts(v) -> "datetime | None":
    """Extrae el timestamp de una entrada de seen.json (str ISO o dict {ts, price})."""
    if isinstance(v, str):
        return _parse(v)
    if isinstance(v, dict):
        return _parse(v.get("ts", ""))
    return None


def _quality(f: Finding) -> tuple:
    """Orden de confianza para quedarse con el mejor hallazgo de un mismo producto."""
    rank = {"cross_confirmed": 3, "cross_store": 2, "own_price_drop": 1}.get(f.kind, 0)
    return (rank, f.n_comparables, f.discount_pct or 0)


def _dedup_findings(findings: list[Finding]) -> list[Finding]:
    """Un producto puede salir por varias señales (confirmado + caída histórica).
    Se queda con el de mayor confianza para no avisar dos veces lo mismo."""
    best: dict[str, Finding] = {}
    for f in findings:
        k = _pid_key(f)
        if k not in best or _quality(f) > _quality(best[k]):
            best[k] = f
    return list(best.values())


def split_new(findings: list[Finding]) -> list[Finding]:
    """Devuelve solo los hallazgos no avisados antes; actualiza data/seen.json.

    Supresión de duplicados en dos capas:
    1. Exacta: misma tienda + mismo PID + mismo precio → nunca re-avisar (30d TTL).
    2. Suave: mismo producto, precio dentro de ±5% del último aviso → suprimir.
       Evita notificar el mismo deal cuando el precio oscila unos cientos de pesos.
    """
    now = datetime.now(timezone.utc)
    seen: dict = {}
    if SEEN.exists():
        try:
            seen = json.loads(SEEN.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            seen = {}
    cutoff = now - timedelta(days=SEEN_TTL_DAYS)
    seen = {k: v for k, v in seen.items()
            if _entry_ts(v) and _entry_ts(v) > cutoff}

    new: list[Finding] = []
    for f in findings:
        if _finding_key(f) in seen:
            continue
        # dedup suave: mismo producto, precio casi igual → misma oferta
        ent = seen.get(_pid_key(f))
        if isinstance(ent, dict):
            cached_price = ent.get("price", 0)
            if cached_price > 0:
                diff = abs(f.product.price - cached_price) / cached_price
                if diff <= PRICE_TOL:
                    continue
        new.append(f)

    for f in findings:
        seen[_finding_key(f)] = now.isoformat()
        seen[_pid_key(f)] = {"ts": now.isoformat(), "price": f.product.price}

    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return new


def _parse(iso: str):
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


def _prune_lookup_cache(cache: dict, ttl_hours: float) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    for k in [k for k, v in cache.items()
              if not (_parse(v.get("ts", "")) and _parse(v["ts"]) > cutoff)]:
        del cache[k]


def load_config() -> dict:
    with open(CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_fixture_products() -> list[Product]:
    """Productos de ejemplo para --dry-run (prueba pipeline + reporte)."""
    path = FIXTURES / "sample_products.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Product(**row) for row in data]


def run(stores_filter: set[str] | None, threshold: float, dry_run: bool,
        net_fallback: int | None = None) -> int:
    cfg = load_config()
    threshold = threshold if threshold is not None else cfg.get(
        "threshold_pct", detect.DEFAULT_THRESHOLD)
    max_pct = float(cfg.get("max_discount_pct", detect.DEFAULT_MAX))
    verify_cross = bool(cfg.get("verify_cross_store", True))
    candidate_min = float(cfg.get("candidate_min_pct", 30))
    confirm_pct = float(cfg.get("cross_confirm_pct", threshold))
    gcfg = cfg.get("google_shopping", {}) or {}

    products: list[Product] = []
    adapters: dict = {}
    if dry_run:
        os.environ["DRY_RUN"] = "1"
        print("DRY_RUN: usando fixtures locales")
        products = load_fixture_products()
    else:
        for key, store_cfg in cfg.get("stores", {}).items():
            if stores_filter and key not in stores_filter:
                continue
            if not store_cfg.get("enabled", True):
                continue
            adapters[key] = build_adapter(key, store_cfg)

        # escaneo en paralelo: cada tienda es independiente (red), así que correr
        # los adaptadores a la vez recorta mucho la duración de la corrida.
        def _scan_one(item):
            key, adapter = item
            try:
                got = list(adapter.scan())
            except Exception as e:  # un adaptador no debe tumbar la corrida
                print(f"   [{key}] error: {e}")
                got = []
            print(f"-> {adapter.name} ({adapter.quality}): {len(got)} productos")
            return got

        max_workers = min(len(adapters), int(cfg.get("scan_workers", 6))) or 1
        if max_workers > 1 and len(adapters) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for got in ex.map(_scan_one, list(adapters.items())):
                    products.extend(got)
        else:
            for item in adapters.items():
                products.extend(_scan_one(item))

    if not dry_run:
        before = len(products)
        products = [p for p in products if not detect.is_refurbished(p.name)]
        removed = before - len(products)
        if removed:
            print(f"Excluidos por reacondicionado/usado: {removed}")
    print(f"Total productos: {len(products)}")

    if verify_cross and not dry_run:
        # candidatos por descuento propio -> confirmar contra otras tiendas.
        # Se acotan a los de MAYOR descuento (cada uno hace lookups de red).
        verify_max = int(cfg.get("verify_max_candidates", 40))
        all_cands = detect.own_discount(products, candidate_min, max_pct)
        all_cands.sort(key=lambda f: f.product.discount_pct or 0, reverse=True)
        candidates = all_cands[:verify_max]
        print(f"Candidatos (desc. propio >= {candidate_min:.0f}%): "
              f"{len(all_cands)}; verificando top {len(candidates)} "
              f"contra otras tiendas...")

        google = None
        if gcfg.get("enabled"):
            from .gshop import GoogleShopping
            google = GoogleShopping(
                ROOT / "data" / "gshop_cache.json",
                ttl_hours=float(gcfg.get("cache_ttl_hours", 24)))
            if not google.available():
                print("   Google Shopping habilitado pero falta BRIGHTDATA_SERP_ZONE; se omite.")
                google = None

        from .pricehist import PriceHistory
        pricehist = PriceHistory(
            ROOT / "data" / "price_history.json",
            window_days=int(cfg.get("history_window_days", 90)),
            min_points=int(cfg.get("history_min_points", 3)))
        print(f"   Histórico propio: {pricehist.n_series} series cargadas.")

        # Presupuesto por madurez: cuando el histórico ya cubre muchos productos,
        # muchos candidatos se confirman GRATIS con el histórico -> bajamos el
        # gasto de red (Bright Data fallback + Google Shopping).
        mature = pricehist.n_series >= int(cfg.get("history_mature_series", 3000))
        eff_net = (net_fallback if net_fallback is not None
                   else int(cfg.get("net_fallback", 40)))
        eff_google = int(gcfg.get("max_lookups", 15))
        if mature:
            eff_net = min(eff_net, int(cfg.get("net_fallback_mature", 15)))
            eff_google = min(eff_google, int(gcfg.get("max_lookups_mature", 15)))
            print(f"   Histórico maduro: presupuesto reducido "
                  f"(red={eff_net}, google={eff_google}).")

        lookup_cache_path = ROOT / "data" / "lookup_cache.json"
        lookup_cache = {}
        if lookup_cache_path.exists():
            try:
                lookup_cache = json.loads(lookup_cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                lookup_cache = {}
        lookup_ttl = float(cfg.get("lookup_cache_ttl_hours", 12))

        findings = verify(
            candidates, adapters, confirm_pct, pool=products, google=google,
            google_min_pct=float(gcfg.get("min_pct", 45)),
            google_max_lookups=eff_google,
            lookup_cache=lookup_cache, lookup_ttl_hours=lookup_ttl,
            net_fallback=eff_net, pricehist=pricehist)
        findings += detect.cross_store(products, threshold, max_pct)  # por EAN si lo hay

        # señal independiente: caída fuerte vs el histórico propio del producto
        # (captura glitches de productos únicos sin comparable en otras tiendas)
        drops = detect.own_price_drop(products, pricehist.baseline, confirm_pct, max_pct)
        if drops:
            print(f"Caídas vs histórico propio: {len(drops)}")
        findings += drops
        findings = _dedup_findings(findings)

        # guardia final: confirmar contra Amazon/Walmart/Sam's aunque NO se hayan
        # escaneado en esta corrida (evita falsos positivos del carril rápido).
        from .verify import guard_costly
        guard_adapters = {}
        for gk, gsc in cfg.get("stores", {}).items():
            if gsc.get("enabled", True) and gsc.get("type") in ("amazon", "walmart", "sams"):
                guard_adapters[gk] = adapters.get(gk) or build_adapter(gk, gsc)
        findings = guard_costly(findings, guard_adapters, confirm_pct,
                                lookup_cache, lookup_ttl)

        # purga entradas vencidas y guarda el caché de lookups
        _prune_lookup_cache(lookup_cache, lookup_ttl)
        lookup_cache_path.write_text(
            json.dumps(lookup_cache, ensure_ascii=False, indent=2), encoding="utf-8")
        if google is not None:
            google.save()
            print(f"   Consultas Google Shopping (red): {google.calls}")
        # registra los precios de HOY (después de verificar) y poda la ventana
        touched = pricehist.record(products)
        pricehist.prune()
        pricehist.save()
        print(f"   Histórico propio: +{touched} puntos, {pricehist.n_series} series.")
        findings.sort(key=lambda f: f.discount_pct, reverse=True)
        print(f"Confirmados más baratos que la competencia (>= "
              f"{confirm_pct:.0f}%): {len(findings)}")
    else:
        findings = detect.detect(products, threshold, max_pct)
        print(f"Hallazgos {threshold:.0f}%-{max_pct:.0f}%: {len(findings)}")

    # alertas de RESTOCK: productos vigilados que volvieron a estar disponibles
    restocks = []
    if not dry_run:
        from .watchlist import update_and_detect
        restocks = update_and_detect(products, findings, ROOT / "data" / "watchlist.json")
        if restocks:
            print(f"Restock (de nuevo disponibles): {len(restocks)}")

    # solo lo NUEVO respecto a corridas anteriores (estado en data/seen.json)
    new = [] if dry_run else split_new(findings)
    if dry_run:
        new = findings
    new = restocks + new          # los restock siempre se avisan (transición)
    print(f"Nuevos: {len(new)}")

    # notificación instantánea a Telegram (si hay secrets configurados)
    if new and not dry_run:
        from . import notify
        notify.send(new)

    shown_threshold = confirm_pct if (verify_cross and not dry_run) else threshold
    results_path, report_path = write_outputs(
        findings, ROOT / "data", len(products), shown_threshold)
    write_new_report(new, ROOT / "data")
    if not dry_run:
        append_history(new, ROOT / "data")   # historial navegable: data/history.md
    print(f"Escrito: {results_path}")
    print(f"Escrito: {report_path}")

    # exporta para GitHub Actions: solo notificamos si hay NUEVOS
    gha_out = os.environ.get("GITHUB_OUTPUT")
    if gha_out:
        from .category import detect_all
        labels = detect_all([f.product.name for f in new]) if new else []
        with open(gha_out, "a", encoding="utf-8") as fh:
            fh.write(f"findings={len(findings)}\n")
            fh.write(f"new={len(new)}\n")
            fh.write(f"labels={','.join(labels)}\n")
    return len(new)


def main() -> None:
    ap = argparse.ArgumentParser(description="Cazador de errores de precio MX")
    ap.add_argument("--dry-run", action="store_true",
                    help="usa fixtures locales, no hace red")
    ap.add_argument("--stores", default="",
                    help="lista separada por comas para limitar tiendas")
    ap.add_argument("--threshold", type=float, default=None,
                    help="umbral de descuento en %% (default: stores.yaml)")
    ap.add_argument("--net-fallback", type=int, default=None,
                    help="máx. candidatos con lookup de red (0 = solo pool+Google; carril rápido)")
    args = ap.parse_args()

    stores_filter = {s.strip() for s in args.stores.split(",") if s.strip()} or None
    run(stores_filter, args.threshold, args.dry_run, net_fallback=args.net_fallback)


if __name__ == "__main__":
    sys.exit(0 if main() is None else 0)
