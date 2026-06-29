"""Resumen semanal automático del cazador de precios.

Lee data/history.jsonl, agrega los hallazgos de los últimos 7 días por tienda y
categoría, añade la tasa de aciertos (reacciones) y escribe data/weekly.md. El
workflow lo publica como GitHub Issue cada domingo — meta-monitoreo sin que
nadie tenga que correr nada a mano.

  python -m src.weekly_summary           # escribe data/weekly.md
  python -m src.weekly_summary --repo owner/name   # incluye win-rate
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .category import detect as detect_category

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "history.jsonl"
OUT = ROOT / "data" / "weekly.md"


def _load(days: int) -> list[dict]:
    if not HIST.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    for line in HIST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            ts = datetime.fromisoformat(r["ts"]).replace(tzinfo=timezone.utc)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if ts >= cutoff:
            rows.append(r)
    return rows


def build(days: int = 7, repo: str | None = None) -> str:
    rows = _load(days)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# 📊 Resumen semanal — {now}", "",
             f"- Periodo: últimos **{days} días**",
             f"- Hallazgos: **{len(rows)}**"]

    if not rows:
        lines.append("\n_Sin hallazgos en el periodo._")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return "\n".join(lines)

    by_store = Counter(r.get("store", "?") for r in rows)
    by_cat = Counter(detect_category(r.get("name", "")) or "otros" for r in rows)
    discounts = [r.get("discount_pct", 0) for r in rows]
    best = max(rows, key=lambda r: r.get("discount_pct", 0))

    lines += [
        f"- Descuento promedio: **{sum(discounts)/len(discounts):.0f}%** "
        f"(máx **{max(discounts):.0f}%**)",
        "",
        "## Por tienda", "",
        "| Tienda | Hallazgos |", "|--------|----------:|",
    ]
    for store, n in by_store.most_common():
        lines.append(f"| {store} | {n} |")

    lines += ["", "## Por categoría", "",
              "| Categoría | Hallazgos |", "|-----------|----------:|"]
    for cat, n in by_cat.most_common():
        lines.append(f"| {cat} | {n} |")

    lines += ["", "## Mejor hallazgo de la semana", "",
              f"**-{best.get('discount_pct',0):.0f}%** · {best.get('store','?')} · "
              f"[{best.get('name','?')[:70]}]({best.get('url','')}) "
              f"a ${best.get('price',0):,.0f}"]

    # win-rate (opcional, requiere gh + repo)
    if repo:
        from .winrate import compute, markdown
        lines += ["", "## Precisión", "", markdown(compute(repo))]

    text = "\n".join(lines)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description="Resumen semanal de hallazgos")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--repo", default=None, help="owner/name para incluir win-rate")
    args = ap.parse_args()
    text = build(args.days, args.repo)
    # consola Windows (cp1252) no soporta emojis; el archivo sí (UTF-8)
    sys.stdout.reconfigure(errors="replace")
    print(text)
    print(f"\nEscrito: {OUT}")


if __name__ == "__main__":
    sys.exit(main())
