---
name: deploy-fe-signer
description: Desplegar el fe-signer (firma FE + sub-módulo buzon-rx) al VPS — push local + git pull y rebuild Docker en /opt/paracarpinteros-odoo/fe-signer, con test de firma. Usar tras cambiar código PHP del firmador o de buzon-rx (recepción FE / OAuth Gmail).
---

# Deploy del fe-signer

El fe-signer (PHP + Apache + CRLibre) corre en el VPS en `/opt/paracarpinteros-odoo/fe-signer/` dentro del clon del monorepo. Firma XML de FE (`/sign`) y aloja el sub-módulo **buzon-rx** (recepción de FE recibidas vía Gmail polling). Se despliega **solo por `git pull`** — no hay CI/CD.

> **OJO:** esto **NO** cubre el *FE Converter* (el conversor HTML/JS de emisión). Ese vive dentro de Odoo como attachment 37459 y se sube con la skill **deploy-fe-converter**. El fe-signer solo *firma*.

## Flujo

1. **Commit local** (español, sin prefijos). 
2. **Push a main** — requiere **"si push"** del usuario. Coordinar con la sesión VPS antes.
3. **En el VPS**:
   ```bash
   ssh root@66.94.99.220 'cd /opt/paracarpinteros-odoo/fe-signer && git pull && docker compose up -d --build && docker compose logs --tail=30 fe-signer'
   ```

## Verificación

```bash
# Test de firma (P12 + XML en base64 → /sign):
P12_B64=$(base64 -w0 /ruta/certificado.p12)
XML_B64=$(base64 -w0 factura_sin_firma.xml)
curl -s -X POST https://panel.paracarpinteros.com/sign \
  -H "X-API-Key: $SIGNER_API_KEY" \
  -d "{\"xmlBase64\":\"$XML_B64\",\"p12Base64\":\"$P12_B64\",\"pin\":\"1234\",\"tipoDoc\":\"01\"}"
```

## Gotchas buzon-rx (OAuth Gmail)

- El refresh token vive en **SQLite cifrado** (`buzon.db`, tabla `oauth_tokens` id=1), no en `.env`. La clave se deriva de `SIGNER_API_KEY` + `OAUTH_ENC_KEY_DERIVE` — si esos env cambian, el descifrado falla.
- Si la app OAuth está en **"Testing"** en Google Cloud, Google revoca el refresh token cada 7 días → `invalid_grant`. Fix: publicar a "In production" **y** re-autorizar en `https://panel.paracarpinteros.com/buzon-rx/oauth-start` (logueado en envios@) — publicar no des-caduca el token ya emitido.
- El volumen `./storage` guarda `buzon.db`: no borrarlo en el `--build`.
