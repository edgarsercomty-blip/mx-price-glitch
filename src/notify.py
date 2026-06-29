"""Notificación a Telegram de los hallazgos NUEVOS.

Manda un mensaje al chat configurado vía la Bot API. Es complementario a los
GitHub Issues: llega instantáneo al teléfono, ideal para los glitches de alta
prioridad (errores de captura de dígito).

Secrets requeridos (si faltan, no se envía nada y no falla):
  TELEGRAM_BOT_TOKEN  -> token del bot (@BotFather)
  TELEGRAM_CHAT_ID    -> id del chat/canal destino
"""
from __future__ import annotations

import os

import requests

from .detect import Finding

_API = "https://api.telegram.org/bot{token}/sendMessage"
_MAX = 4000          # límite de Telegram ~4096; dejamos margen


def available() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")
                and os.environ.get("TELEGRAM_CHAT_ID"))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(findings: list[Finding], header: str = "💸 Nuevos errores de precio") -> str:
    lines = [f"<b>{_esc(header)}</b>", ""]
    # prioriza los probables errores de captura, luego por descuento
    ordered = sorted(findings,
                     key=lambda f: (getattr(f, "likely_typo", False),
                                    f.discount_pct or 0), reverse=True)
    for f in ordered:
        p = f.product
        flag = "🚨 " if getattr(f, "likely_typo", False) else ""
        saving = ""
        if f.ref_price and f.ref_price > p.price:
            saving = f" (ahorro ${f.ref_price - p.price:,.0f})"
        name = _esc(p.name[:70])
        line = (f"{flag}<b>-{f.discount_pct:.0f}%</b> {_esc(p.store)} · "
                f"<a href=\"{_esc(p.url)}\">{name}</a> "
                f"${p.price:,.0f}{saving}")
        lines.append(line)
    msg = "\n".join(lines)
    return msg[:_MAX]


def send(findings: list[Finding], header: str = "💸 Nuevos errores de precio") -> bool:
    """Envía la notificación. Devuelve True si se mandó, False si no hay config
    o no hay hallazgos."""
    if not available() or not findings:
        return False
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    text = build_message(findings, header)
    try:
        r = requests.post(_API.format(token=token), timeout=20, json={
            "chat_id": chat, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        if r.status_code != 200:
            print(f"   [telegram] HTTP {r.status_code}: {r.text[:200]}")
            return False
        print(f"   [telegram] enviado: {len(findings)} hallazgos.")
        return True
    except requests.RequestException as e:
        print(f"   [telegram] error: {e}")
        return False
