"""Adaptador Sam's Club México.

Sam's Club MX corre en la misma plataforma "Glass" que Walmart (mismo grupo):
la página /search?q= trae los productos en __NEXT_DATA__ con el mismo esquema
(priceInfo.linePrice = actual, wasPrice = regular). Por eso reutiliza toda la
lógica de WalmartAdapter, solo cambia el dominio.
"""
from __future__ import annotations

from .walmart import WalmartAdapter


class SamsAdapter(WalmartAdapter):
    def __init__(self, config: dict):
        config = dict(config)
        config.setdefault("base", "https://www.sams.com.mx")
        config.setdefault("name", "Sam's Club MX")
        super().__init__(config)
