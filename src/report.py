"""Genera los artefactos de salida: results.json y report.md."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .category import detect_all
from .detect import Finding

KIND_LABEL = {
    "own_discount": "Descuento propio",
    "cross_store": "Más barato entre tiendas",
    "cross_confirmed": "Confirmado vs competencia",
    "own_price_drop": "Caída vs histórico propio",
    "restock": "🔁 De nuevo disponible",
}


def write_outputs(findings: list[Finding], out_dir: Path, scanned: int,
                  threshold: float) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps(
        {
            "generated_at": ts,
            "products_scanned": scanned,
            "threshold_pct": threshold,
            "count": len(findings),
            "findings": [f.to_dict() for f in findings],
        },
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")

    report_path = out_dir / "report.md"
    report_path.write_text(_markdown(findings, ts, scanned, threshold),
                           encoding="utf-8")
    return results_path, report_path


def append_history(new_findings: list[Finding], out_dir: Path) -> None:
    """Agrega los hallazgos NUEVOS a data/history.jsonl (append-only) y regenera
    data/history.md (tabla navegable en GitHub, lo más reciente arriba)."""
    if not new_findings:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    path = out_dir / "history.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        for f in new_findings:
            p = f.product
            fh.write(json.dumps({
                "ts": ts, "discount_pct": f.discount_pct, "store": p.store,
                "name": p.name, "url": p.url, "price": p.price,
                "kind": f.kind, "detail": f.detail,
            }, ensure_ascii=False) + "\n")
    render_history_md(out_dir)


def render_history_md(out_dir: Path, limit: int = 400) -> None:
    path = out_dir / "history.jsonl"
    if not path.exists():
        return
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    total = len(rows)
    rows = rows[-limit:][::-1]            # más reciente primero
    lines = [f"# Historial de ofertas encontradas ({total})", "",
             "Lo más reciente arriba. Cada fila es un hallazgo confirmado.", "",
             "| Fecha | Desc. | Tienda | Producto | Precio | Detalle |",
             "|-------|------:|--------|----------|-------:|---------|"]
    for r in rows:
        name = (r["name"][:55] + "…") if len(r["name"]) > 55 else r["name"]
        name = name.replace("|", "\\|")
        lines.append(
            f"| {r['ts']} | -{r['discount_pct']:.0f}% | {r['store']} | "
            f"[{name}]({r['url']}) | ${r['price']:,.0f} | "
            f"{str(r.get('detail','')).replace('|', '/')} |")
    (out_dir / "history.md").write_text("\n".join(lines), encoding="utf-8")


def write_new_report(new_findings: list[Finding], out_dir: Path,
                     ts: str | None = None) -> Path:
    """Escribe data/new.md con SOLO los hallazgos nuevos (para la notificación).
    También escribe data/labels.txt con los labels de GitHub detectados por categoría."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = ts or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    path = out_dir / "new.md"
    path.write_text(_markdown(new_findings, ts, None, None,
                              heading="🆕 Nuevos posibles errores de precio"),
                    encoding="utf-8")
    labels = detect_all([f.product.name for f in new_findings]) if new_findings else []
    (out_dir / "labels.txt").write_text("\n".join(labels), encoding="utf-8")
    return path


def _markdown(findings: list[Finding], ts: str, scanned: int | None,
              threshold: float | None,
              heading: str = "Errores/chollos de precio") -> str:
    lines = [f"# {heading} — {ts}", ""]
    if scanned is not None:
        lines.append(f"- Productos revisados: **{scanned}**")
    if threshold is not None:
        lines.append(f"- Umbral de diferencia: **{threshold:.0f}%**")
    lines += [f"- Hallazgos: **{len(findings)}**", ""]
    if not findings:
        lines.append("_Sin hallazgos por encima del umbral en esta corrida._")
        return "\n".join(lines)

    # los probables errores de captura primero; luego por ahorro absoluto ($)
    findings = sorted(
        findings,
        key=lambda f: (getattr(f, "likely_typo", False), f.savings or 0),
        reverse=True)

    lines += ["| Desc. | Ahorro | Tienda | Producto | Precio | Tipo | Detalle |",
              "|------:|-------:|--------|----------|-------:|------|---------|"]
    for f in findings:
        p = f.product
        name = (p.name[:60] + "…") if len(p.name) > 60 else p.name
        name = name.replace("|", "\\|")
        flag = "🚨 " if getattr(f, "likely_typo", False) else ""
        link = f"[{name}]({p.url})"
        saving = f"${f.savings:,.0f}" if f.savings else "—"
        lines.append(
            f"| {flag}-{f.discount_pct:.0f}% | {saving} | {p.store} | {link} | "
            f"${p.price:,.0f} | {KIND_LABEL.get(f.kind, f.kind)} | "
            f"{f.detail.replace('|', '/')} |"
        )
    return "\n".join(lines)
