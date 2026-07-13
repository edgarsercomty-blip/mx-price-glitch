"""Probe de Coppel: valida el flujo GraphQL del adaptador de punta a punta
(token anónimo + GET_SEARCH_RESULTS vía Bright Data).

  python -m src.probe_coppel "licuadora"

Si falla el paso 1 (token), la zona de Bright Data está caída o Coppel cambió
el endpoint de auth. Si falla el paso 2 con PersistedQueryNotFound, cambió el
hash del frontend -> recapturar con el navegador (ver docstring del adaptador).
"""
from __future__ import annotations

import json
import sys

from .adapters.coppel import CoppelAdapter


def main() -> None:
    q = sys.argv[1] if len(sys.argv) > 1 else "licuadora"
    ad = CoppelAdapter({"key": "coppel", "search_terms": [q],
                        "max_products_per_term": 10})

    print("1) POST /auth/access-token ...")
    token = ad._get_token()
    if not token:
        print("   FALLO: sin token (¿Bright Data caído o endpoint cambiado?)")
        return
    print(f"   OK: token de {len(token)} chars, expira en "
          f"{ad._token_exp:.0f} (epoch)")

    print(f"2) GET_SEARCH_RESULTS '{q}' ...")
    raws = ad._search(q, page_size=10)
    print(f"   {len(raws)} productos crudos")
    for raw in raws[:5]:
        p = ad._to_product(raw)
        if p:
            print(f"   - {p.name[:55]!r} ${p.price:,.0f}"
                  + (f" (lista ${p.list_price:,.0f})" if p.list_price else "")
                  + f" [{(p.extra or {}).get('seller')}]")
        else:
            print(f"   - sin mapear: {json.dumps(raw, ensure_ascii=False)[:100]}")

    if raws:
        print("\nOK: el adaptador funciona; habilitar coppel en stores.yaml")
    else:
        print("\nSin productos: revisar hash de la persisted query o la zona")


if __name__ == "__main__":
    main()
