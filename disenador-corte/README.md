# Diseñador de plantillas de corte (CO2 / LightBurn)

SPA autónoma (un solo `disenador.html`, sin dependencias externas) para diseñar
plantillas paramétricas y exportarlas a **SVG y DXF** (capas `CORTE` rojo /
`GRABADO` azul) listas para importar en LightBurn. Medidas en **mm 1:1**.

## URL pública

**https://www.paracarpinteros.com/disenador-corte**

## Cómo está desplegado (NO es `git pull`)

Igual que el FE Converter: el HTML vive como **`ir.attachment` público** en Odoo y
una `website.page` lo embebe por iframe (fetch → Blob → iframe, para que renderice
inline y no se descargue).

| Pieza | id | Notas |
|---|---|---|
| `ir.attachment` `disenador_corte.html` | **40488** | `public=True`, `mimetype=text/html`. Es el que se sirve. |
| `ir.ui.view` `website.disenador-corte` | **7325** | qweb wrapper con el iframe + script blob. `website_id=3`. |
| `website.page` `/disenador-corte` | **72** | `is_published=True`. |

### Redeploy tras editar `disenador.html`

Subir el HTML al adjunto 40488 por XML-RPC (la página y la vista no cambian):

```python
import base64, xmlrpc.client, os, sys
sys.path.insert(0,'scripts'); from _env import load_project_env; load_project_env()
uid=xmlrpc.client.ServerProxy(os.environ['ODOO_URL']+'/xmlrpc/2/common').authenticate(
    os.environ['ODOO_DB'],os.environ['ODOO_USERNAME'],os.environ['ODOO_API_KEY'],{})
m=xmlrpc.client.ServerProxy(os.environ['ODOO_URL']+'/xmlrpc/2/object')
html=open('disenador-corte/disenador.html','rb').read()
m.execute_kw(os.environ['ODOO_DB'],uid,os.environ['ODOO_API_KEY'],'ir.attachment','write',
    [[40488],{'datas':base64.b64encode(html).decode()}])
```

Tras subir, **Ctrl+Shift+R** (el iframe puede cachear).

## Arquitectura interna

- **Modelo de geometría neutro** (mm, Y-arriba estilo CAD): entidades
  `circle` / `line` / `arc`. De ese modelo salen **igual** el SVG y el DXF, sin
  desfases.
- `TEMPLATES` = registro de plantillas. Cada una declara `params[]` y un
  `build(p)` que devuelve las entidades. Agregar una plantilla = agregar una
  entrada al objeto.
- Export: `buildSVG()` (flip Y con `matrix(1,0,0,-1,0,H)`, `width/height` en mm) y
  `buildDXF()` (R12 ASCII, entidades `CIRCLE`/`LINE`/`ARC`, tabla de capas).

## Plantillas actuales

1. **Rueda de carrete** — aro exterior + cubo central + N radios + agujero.
   (La barra metálica de la pieza real va aparte: el CO2 no corta metal.)
2. **Anillo / junta** — Ø exterior e interior.
3. **Panel rectangular** — esquinas redondeadas + grilla de agujeros.
