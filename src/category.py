"""Detecta la categoría de un producto a partir de su nombre.

Devuelve slugs listos para usarse como labels de GitHub (ASCII, sin tildes).
"""
from __future__ import annotations

import re
import unicodedata


# (categoría_interna, [patrones_regex])  — orden importa: el primero que hace match gana
_RULES: list[tuple[str, list[str]]] = [
    # Apple primero: evita que iMac/MacBook matcheen como laptop o pantalla
    ("apple",        [r"\bairpods?\b", r"\bipad\b", r"\biphone\b", r"\bimac\b",
                      r"\bmacbook\b", r"\bapple watch\b"]),
    # Consola antes que pantalla: Nintendo Switch OLED no es una pantalla
    ("consola",      [r"\bnintendo\b", r"\bplaystation\b", r"\bxbox\b", r"videojuego",
                      r"\bconsola\b", r"switch oled"]),
    ("pantalla",     [r"pantalla", r"television", r"\btv\b", r"\boled\b", r"\bqned\b",
                      r"\bqled\b", r"smart tv"]),
    ("laptop",       [r"laptop", r"notebook", r"chromebook"]),
    ("celular",      [r"smartphone", r"celular", r"galaxy s\d", r"galaxy a\d", r"galaxy z"]),
    ("tablet",       [r"\btablet\b", r"\bsurface\b"]),
    ("audio",        [r"audifonos", r"\bauriculares\b", r"bocina", r"altavoz",
                      r"headphone", r"earbuds", r"parlante"]),
    ("refrigerador", [r"refrigerador", r"frigorifico", r"\bnevera\b", r"frigobar",
                      r"congelador"]),
    ("lavadora",     [r"lavasecadora", r"\blavadora\b",
                      r"\bsecadora\b(?! de (cabello|pelo))"]),
    ("estufa",       [r"\bestufa\b", r"\bhorno\b", r"microondas"]),
    ("minisplit",    [r"minisplit", r"aire acondicionado", r"\bclima\b"]),
    ("herramienta",  [r"\btaladro\b", r"sierra circular", r"compresor", r"multiherramienta",
                      r"rotomartillo", r"llave de impacto"]),
    ("bicicleta",    [r"bicicleta", r"\bscooter\b", r"patineta"]),
    ("smartwatch",   [r"smartwatch", r"reloj inteligente"]),
    ("licuadora",    [r"licuadora", r"\bblender\b"]),
]

# Mapa categoria_interna → label de GitHub (ASCII, kebab-case, max ~50 chars)
_LABEL: dict[str, str] = {
    "apple":        "apple",
    "pantalla":     "pantalla-tv",
    "laptop":       "laptop",
    "celular":      "celular-smartphone",
    "tablet":       "tablet",
    "audio":        "audio-audifonos",
    "refrigerador": "refrigerador-congelador",
    "lavadora":     "lavadora-lavasecadora",
    "estufa":       "estufa-microondas",
    "minisplit":    "minisplit-clima",
    "herramienta":  "herramienta",
    "bicicleta":    "bicicleta",
    "consola":      "consola-videojuegos",
    "smartwatch":   "smartwatch",
    "licuadora":    "licuadora",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def detect(name: str | None) -> str | None:
    """Devuelve el label de GitHub para el nombre de producto, o None."""
    if not name:
        return None
    n = _norm(name)
    for cat, patterns in _RULES:
        if any(re.search(pat, n) for pat in patterns):
            return _LABEL.get(cat)
    return None


def detect_all(names: list[str]) -> list[str]:
    """Labels únicos (en orden de aparición) para una lista de nombres de producto."""
    seen: set[str] = set()
    out: list[str] = []
    for nm in names:
        lbl = detect(nm)
        if lbl and lbl not in seen:
            seen.add(lbl)
            out.append(lbl)
    return out
