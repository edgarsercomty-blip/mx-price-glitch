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


def _finding_key(f: Finding) -> str:
    """Identidad estable de un hallazgo: tienda + producto + precio.
    Incluye el precio para que una BAJADA distinta se considere novedad."""
    p = f.product
    pid = (p.extra or {}).get("productId") or (p.extra or {}).get("partNumber") or p.url
    return f"{p.store}|{pid}|{int(round(p.price))}"


def split_new(findings: list[Finding]) -> list[Finding]:
    """Devuelve solo los hallazgos no avisados antes; actualiza data/seen.json."""
    now = datetime.now(timezone.utc)
    seen: dict[str, str] = {}
    if SEEN.exists():
        try:
            seen = json.loads(SEEN.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            seen = {}
    # purga lo viejo (permite re-avisar si vuelve a aparecer más adelante)
    cutoff = now - timedelta(days=SEEN_TTL_DAYS)
    seen = {k: v for k, v in seen.items()
            if _parse(v) and _parse(v) > cutoff}

    new = [f for f in findings if _finding_key(f) not in seen]
    for f in findings:                       # marca todo lo visto ahora
        seen[_finding_key(f)] = now.isoformat()

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


def run(stores_filter: set[str] | None, threshold: float, dry_run: bool) -> int:
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
            adapter = build_adapter(key, store_cfg)
            adapters[key] = adapter
            print(f"-> escaneando {adapter.name} ({adapter.quality})...")
            try:
                got = list(adapter.scan())
            except Exception as e:  # un adaptador no debe tumbar la corrida
                print(f"   [{key}] error: {e}")
                got = []
            print(f"   {len(got)} productos")
            products.extend(got)

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
            google_max_lookups=int(gcfg.get("max_lookups", 15)),
            lookup_cache=lookup_cache, lookup_ttl_hours=lookup_ttl,
            net_fallback=int(cfg.get("net_fallback", 40)))
        findings += detect.cross_store(products, threshold, max_pct)  # por EAN si lo hay

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
        findings.sort(key=lambda f: f.discount_pct, reverse=True)
        print(f"Confirmados más baratos que la competencia (>= "
              f"{confirm_pct:.0f}%): {len(findings)}")
    else:
        findings = detect.detect(products, threshold, max_pct)
        print(f"Hallazgos {threshold:.0f}%-{max_pct:.0f}%: {len(findings)}")

    # solo lo NUEVO respecto a corridas anteriores (estado en data/seen.json)
    new = [] if dry_run else split_new(findings)
    if dry_run:
        new = findings
    print(f"Nuevos: {len(new)}")

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
        with open(gha_out, "a", encoding="utf-8") as fh:
            fh.write(f"findings={len(findings)}\n")
            fh.write(f"new={len(new)}\n")
    return len(new)


def main() -> None:
    ap = argparse.ArgumentParser(description="Cazador de errores de precio MX")
    ap.add_argument("--dry-run", action="store_true",
                    help="usa fixtures locales, no hace red")
    ap.add_argument("--stores", default="",
                    help="lista separada por comas para limitar tiendas")
    ap.add_argument("--threshold", type=float, default=None,
                    help="umbral de descuento en %% (default: stores.yaml)")
    args = ap.parse_args()

    stores_filter = {s.strip() for s in args.stores.split(",") if s.strip()} or None
    run(stores_filter, args.threshold, args.dry_run)


if __name__ == "__main__":
    sys.exit(0 if main() is None else 0)
