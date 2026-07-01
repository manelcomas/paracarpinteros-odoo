---
name: deploy-fe-converter
description: Subir el FE Converter (conversor de factura electrónica de emisión) al ir.attachment 37459 de Odoo por XML-RPC, con backup previo. NO es git pull ni VPS — vive dentro de Odoo. Usar tras editar fe-signer/fe-converter/fe_converter.html.
---

# Deploy del FE Converter (attachment 37459 en Odoo)

El conversor de **factura electrónica de emisión** vive DENTRO de Odoo como `ir.attachment` **37459** (`fe_converter_v22.html`), servido en la página website `/fe-converter` (un wrapper con iframe a `/web/content/37459`), y embebido por el panel. **No es un deploy normal**: ni VPS ni git pull.

La fuente de verdad versionada es [`fe-signer/fe-converter/fe_converter.html`](fe-signer/fe-converter/fe_converter.html). Editá ahí y subí.

## Cómo desplegar

```bash
# 1) Dry-run: compara tamaños local vs remoto, no toca nada
.venv/bin/python scripts/deploy_fe_converter.py

# 2) Subir de verdad (hace BACKUP del 37459 como fe_converter_BACKUP_<fecha>.html y sobrescribe)
.venv/bin/python scripts/deploy_fe_converter.py --apply
```

Tras subir: **Ctrl+Shift+R** en el navegador (el iframe cachea `/web/content/37459`).

## Gotchas fiscales (rechazos Hacienda)

- **Tarifa IVA en el resumen:** `<TotalDesgloseImpuesto>` debe llevar el **mismo `CodigoTarifaIVA` que las líneas**. Hardcodear `08` (13%) rompe facturas con tarifa reducida — UCR paga 2% (tarifa `03`) → rechazo **-488**.
- **CAByS:** el código de cada línea sale de `product.product.x_cabys_code`. Si no existe en el catálogo BCCR → rechazo **-400**. Validar contra `https://api.hacienda.go.cr/fe/cabys?codigo=XXX` (vacío = no existe).
- La respuesta de Hacienda de cada FE queda como adjunto `FE_<clave>_respuesta_hacienda.xml` en el chatter del `account.move` — ahí está el motivo exacto de un rechazo.

## Verificación

Abrir `https://www.paracarpinteros.com/fe-converter` (o el iframe del panel), Ctrl+Shift+R, y confirmar que el cambio aparece. Los `fe_converter_BACKUP_*` en `ir.attachment` son la red de seguridad para revertir.
