# FE Converter — Régimen Simplificado → Factura Electrónica Hacienda CR

Herramienta web que busca facturas en Odoo, arma el XML de Factura Electrónica
(Hacienda CR v4.4), lo firma vía `fe-signer` (`/sign`) y lo envía a Hacienda.

## ⚠️ Dónde vive en producción (NO es un deploy normal)

**No corre en el VPS ni se despliega con `git pull`.** Vive **dentro de Odoo**
(`www.paracarpinteros.com`) como un `ir.attachment`, y el panel
(`panel.paracarpinteros.com`) lo embebe por iframe:

| Pieza | ID en Odoo |
|---|---|
| Página website `/fe-converter` | `website.page` id **64** |
| Vista wrapper (solo el iframe) | `ir.ui.view` id **7307** (`website.fe-converter`) |
| **HTML/JS real del conversor** | `ir.attachment` id **37459** (`fe_converter_v22.html`) |

El wrapper 7307 carga el iframe desde `/web/content/37459`. Los
`fe_converter_BACKUP_*` en `ir.attachment` son backups históricos.

Este archivo `fe_converter.html` es **la copia versionada** del attachment 37459
(en Odoo no había control de versiones = drift). Es la fuente de verdad: editá
acá y volvé a subir.

## Cómo desplegar un cambio (subir a Odoo por XML-RPC)

```python
import sys, base64, xmlrpc.client, os
sys.path.insert(0, 'scripts'); from _env import load_project_env; load_project_env()
URL=os.environ['ODOO_URL']; DB=os.environ['ODOO_DB']
USER=os.environ['ODOO_USERNAME']; KEY=os.environ['ODOO_API_KEY']
uid=xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/common',allow_none=True).authenticate(DB,USER,KEY,{})
M=xmlrpc.client.ServerProxy(f'{URL}/xmlrpc/2/object',allow_none=True)
# 1) Backup del attachment actual ANTES de sobrescribir
cur=M.execute_kw(DB,uid,KEY,'ir.attachment','read',[[37459]],{'fields':['datas']})[0]
M.execute_kw(DB,uid,KEY,'ir.attachment','create',[{'name':'fe_converter_BACKUP_AAAAMMDD.html','datas':cur['datas'],'mimetype':'text/html'}])
# 2) Subir esta copia corregida
html=open('fe-signer/fe-converter/fe_converter.html','rb').read()
M.execute_kw(DB,uid,KEY,'ir.attachment','write',[[37459],{'datas':base64.b64encode(html).decode()}])
```

Tras subir: en el navegador **Ctrl+Shift+R** (el iframe cachea `/web/content/37459`).

## Notas de impuestos (Hacienda CR)

- Código de tarifa IVA: `0%→01, 1%→02, 2%→03, 4%→04, 13%→08` (helper `codigoTarifaIVA`).
- El `<TotalDesgloseImpuesto>` (resumen) **debe** llevar el mismo `CodigoTarifaIVA`
  que las líneas. Hardcodearlo a `08` rompe las facturas con tarifa reducida
  (p.ej. UCR 2% → tarifa 03) con rechazo Hacienda **-488**.

## Botón "Anular" en el Historial (v32, 2026-06-06)

En el Historial, cada factura **Aceptada** (tipoDoc 01/04) tiene un botón **🚫**
(`anularFE()`) que emite en **1 clic** una Nota de Crédito que la anula:

- **NC espejo**: transforma el XML **firmado** de la factura original (`row.datas`)
  cambiando solo cabecera (root → `NotaCreditoElectronica`, nueva Clave/Consecutivo
  NC serie 003, nueva `FechaEmision`), quitando la `<ds:Signature>` y añadiendo
  `<InformacionReferencia>` (TipoDocIR original, Numero = clave original, Código 01
  "Anula"). **`DetalleServicio` y `ResumenFactura` quedan idénticos** → cero rechazos
  -509 (CABYS) / -488 (tarifa).
- **Doble confirmación**: casilla "Confirmo…" + **contraseña**. La contraseña se
  valida por **hash SHA-256** guardado en Odoo (`ir.config_parameter`
  `fe.anulacion_confirm_hash`), nunca en claro (el HTML del conversor es público).
  **Primer uso**: el modal pide definirla 2 veces (poné la misma de entrar).
- Reutiliza `sign.php` (tipoDoc 03), el Worker (`/token` `/submit` `/status`),
  `saveConsecAfterSuccess('003', …)` para subir el consecutivo, y sube a Odoo
  `FE_<clave>.xml` + `_respuesta_hacienda.xml` (aparece en el propio Historial).
- El `.p12` y el PIN se piden en el modal (caen a los del Paso de firma si ya
  estaban cargados).

## Historial de fixes (2026-06-02)

- **Resumen con tarifa real:** `TotalDesgloseImpuesto` ya no hardcodea `08`;
  deriva el código de la tarifa de las líneas. Arregla rechazo -488 en facturas
  de UCR (régimen 2%, Ley 9635 Art. 11.4).
- **Anti doble-click:** `loadInvoice()` tiene guard de re-entrada
  (`window._loadInvoiceBusy`) para que un doble click no abra la factura 2 veces.
