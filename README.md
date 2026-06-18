# Cazador de errores de precio — comercios online MX

Detecta posibles **errores de precio / chollos** (descuentos > 50 %) en tiendas
mexicanas: Coppel, Home Depot, El Palacio de Hierro, Amazon, Walmart, Sam's,
Liverpool y Suburbia. Corre solo en **GitHub Actions** y abre un **Issue** con
los hallazgos.

## Cómo detecta (dos señales)

1. **Descuento propio** — el precio actual es ≥ 50 % menor que el *precio de
   lista* que la misma tienda publica. Es la señal más limpia para errores.
2. **Cruce entre tiendas** — agrupa productos por **EAN** (código de barras); si
   una tienda está ≥ 50 % por debajo de la mediana del resto, lo marca.

El umbral (50 %) se ajusta en `config/stores.yaml` (`threshold_pct`).

## Por qué Bright Data

Estas tiendas bloquean requests normales (Cloudflare/Akamai/captcha). El fetch
va por **Bright Data Web Unlocker**, que pasa el anti-bot. Sirve igual para HTML
y para los endpoints JSON internos (la API de catálogo de VTEX).

## Calidad por tienda

| Tienda | Tipo | Confiabilidad |
|--------|------|---------------|
| Coppel, Home Depot, Palacio (y Sears/Sanborns) | `vtex` | **Alta** — precio, precio de lista y EAN directos de la API |
| Amazon, Walmart, Sam's, Liverpool, Suburbia | `jsonld` | *Best-effort* — depende del JSON-LD de la página; el descuento propio solo sale si publican precio de lista |

> VTEX es donde el detector funciona de verdad. Para las `jsonld` debes poner en
> `stores.yaml` las URLs concretas a vigilar y, con el tiempo, afinar selectores
> (el HTML cambia seguido).

## Estructura

```
config/stores.yaml          tiendas, categorías/URLs y umbral
src/brightdata.py           fetch vía Bright Data Web Unlocker
src/adapters/vtex.py        adaptador VTEX (sólido)
src/adapters/jsonld.py      adaptador best-effort por JSON-LD
src/detect.py               descuento propio + cruce por EAN
src/report.py               genera data/results.json y data/report.md
src/main.py                 orquestador
.github/workflows/price-check.yml   cron horario + Issue
```

## Probar sin Bright Data (local)

```bash
pip install -r requirements.txt
python -m src.main --dry-run
```

Usa fixtures (`tests/fixtures/`) y genera `data/report.md` para ver el formato
del reporte y validar la lógica de detección.

## Puesta en marcha en GitHub

1. Crea un repo y sube esta carpeta.
2. Consigue en Bright Data un **API token** y una **zona Web Unlocker**.
3. En el repo: *Settings → Secrets and variables → Actions → New secret*:
   - `BRIGHTDATA_API_TOKEN`
   - `BRIGHTDATA_ZONE`
4. Edita `config/stores.yaml`: ajusta las categorías VTEX y agrega URLs en las
   tiendas `jsonld`.
5. Pestaña **Actions → Run workflow** para probar a mano. Después corre solo
   cada hora y, si encuentra algo, abre un Issue.

## Notas / límites honestos

- **Falsos positivos**: precios señuelo, productos distintos con el mismo EAN
  mal capturado, accesorios baratos junto al producto caro. Revisa el link antes
  de comprar.
- **Costo**: cada página/endpoint consume cuota de Bright Data. Controla la
  frecuencia del cron y `max_per_category`.
- **Mantenimiento**: las tiendas `jsonld` rompen cuando cambian su HTML. Las
  `vtex` son mucho más estables.
- Úsalo solo para **lectura pública** de catálogos y a un ritmo razonable.
