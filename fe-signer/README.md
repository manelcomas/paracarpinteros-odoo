# fe-signer — Servicio de firma XAdES-EPES Hacienda CR

Microservicio PHP en Docker que firma XMLs de Factura Electrónica usando
[CRLibre/API_Hacienda](https://github.com/CRLibre/API_Hacienda) (librería probada en producción CR).

Reemplaza la firma JS del conversor que tenía bugs sutiles de canonicalización.

## Endpoints

### `POST /sign`

Firma un XML.

**Headers:**
- `Content-Type: application/json`
- `X-API-Key: <secret>`

**Body:**
```json
{
  "xmlBase64": "PD94bWwg...",
  "p12Base64": "MIIRiQIBAz...",
  "pin": "1234",
  "tipoDoc": "01"
}
```

`tipoDoc`: `01` factura, `02` nota débito, `03` nota crédito, `04` tiquete, `05`-`07` mensaje receptor.

**Respuesta OK (200):**
```json
{
  "signedXmlBase64": "PD94bWwg...",
  "signer": "CRLibre",
  "version": "v1"
}
```

**Errores:**
- `400` body inválido
- `401` API key incorrecta o ausente
- `500` firma falló (ver `detail`)

### `GET /health`

Verifica que CRLibre + OpenSSL + API key estén OK. Sin auth.

## Despliegue en VPS Contabo

```bash
# Subir esta carpeta al VPS
scp -r fe-signer/ root@66.94.99.220:/opt/paracarpinteros-odoo/

# SSH al VPS
ssh root@66.94.99.220
cd /opt/paracarpinteros-odoo/fe-signer

# Generar API key
echo "SIGNER_API_KEY=$(openssl rand -hex 32)" > .env
cat .env  # ANOTAR la key, hace falta en el conversor

# Build + run
docker compose up -d --build

# Verificar
sleep 5
curl -s http://localhost:8089/health | python3 -m json.tool
# Esperado: {"status": "ok", "crlibre": true, "openssl": true, ...}
```

## Reverse proxy Nginx

Añadir al server block de `panel.paracarpinteros.com`:

```bash
nano /etc/nginx/sites-available/panel.paracarpinteros.com
# pegar el contenido de nginx-snippet.conf dentro del server { }
nginx -t
systemctl reload nginx
```

Verificar acceso público:
```bash
curl -s https://panel.paracarpinteros.com/sign-health | python3 -m json.tool
```

## Test rápido de firma

```bash
# Cargar .p12 + XML de prueba
P12_B64=$(base64 -w0 /ruta/a/certificado.p12)
XML_B64=$(base64 -w0 factura_sin_firma.xml)
API_KEY=$(grep SIGNER_API_KEY .env | cut -d= -f2)

curl -s -X POST https://panel.paracarpinteros.com/sign \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d "{\"xmlBase64\":\"$XML_B64\",\"p12Base64\":\"$P12_B64\",\"pin\":\"1234\",\"tipoDoc\":\"01\"}" \
  | python3 -m json.tool | head -5
```

## Seguridad

- `.p12` solo existe en disco durante el request (tempnam + unlink en finally)
- Auth via API key (no autenticación de usuario, asumimos que el conversor JS es el único cliente)
- Solo expuesto en `127.0.0.1:8089` del VPS — solo nginx puede llegar
- TLS terminado en nginx (Cloudflare Origin Cert ya configurado)
- CORS abierto para que paracarpinteros.com pueda llamar

## Logs

```bash
docker compose logs -f fe-signer
```

Errores de firma quedan en error_log de Apache (visibles con docker logs).

## Actualización de CRLibre

```bash
ssh root@66.94.99.220
cd /opt/paracarpinteros-odoo/fe-signer
docker compose down
docker compose up -d --build  # rebuilds con git clone fresco de CRLibre
```
