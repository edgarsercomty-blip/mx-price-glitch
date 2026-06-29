"""Tasa de aciertos a partir de las reacciones 👍/👎 en los GitHub Issues.

Cada Issue de hallazgos pide al usuario reaccionar 👍 (deal real) o 👎 (falso
positivo). Este módulo lee esas reacciones vía `gh api` y calcula la precisión,
para afinar umbrales con datos reales en vez de a ojo.

Requiere el CLI `gh` autenticado (disponible en GitHub Actions). Si no está,
devuelve None sin fallar.
"""
from __future__ import annotations

import json
import subprocess


def _gh_json(args: list[str]) -> object | None:
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "null")
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def compute(repo: str, limit: int = 100) -> dict | None:
    """Devuelve {up, down, total, rated, precision} o None si gh no está."""
    issues = _gh_json(["issue", "list", "--repo", repo, "--state", "all",
                       "--limit", str(limit), "--json", "number"])
    if issues is None:
        return None
    up = down = rated = 0
    for it in issues:
        num = it.get("number")
        reactions = _gh_json([
            "api", f"repos/{repo}/issues/{num}/reactions",
            "--jq", "[.[].content]",
        ])
        if not reactions:
            continue
        u = sum(1 for c in reactions if c in ("+1", "heart", "hooray", "rocket"))
        d = sum(1 for c in reactions if c in ("-1", "confused"))
        if u or d:
            rated += 1
            up += u
            down += d
    total = up + down
    precision = round(100 * up / total, 1) if total else None
    return {"up": up, "down": down, "total": total,
            "rated_issues": rated, "precision_pct": precision}


def markdown(stats: dict | None) -> str:
    if not stats:
        return "_Win-rate no disponible (sin `gh` o sin reacciones aún)._"
    if stats["total"] == 0:
        return ("_Aún sin reacciones. Reacciona 👍 (deal real) o 👎 (falso "
                "positivo) en los Issues para medir precisión._")
    return (f"**Precisión (reacciones):** {stats['precision_pct']}% "
            f"({stats['up']} 👍 / {stats['down']} 👎 en "
            f"{stats['rated_issues']} issues evaluados)")
