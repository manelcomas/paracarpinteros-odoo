# correos-cr-bridge

Microservicio que conecta Odoo Online con el Web Service de Correos de Costa Rica (Pymexpress).

## Qué hace

Cada 5 minutos revisa Odoo Online buscando pickings (albaranes) validados con destino CR que aún no tienen guía. Para cada uno:

1. Llama a `ccrGenerarGuia` → obtiene número de guía
2. Llama a `ccrRegistroEnvio` → recibe PDF de etiqueta en Base64
3. Adjunta el PDF al picking en Odoo
4. Actualiza `carrier_tracking_ref` con el número de guía
5. Publica mensaje en el chatter del picking

## Requisitos

- VPS Ubuntu 22.04 con Docker
- Acceso SSH con clave pública
- Credenciales activas de Correos CR (ambiente pruebas o producción)
- API Key de Odoo Online (Preferencias → Cuenta Segura → Nueva clave API)

## Despliegue rápido

```bash
# En el VPS, como root
curl -fsSL https://get.docker.com | sh
git clone https://github.com/manelcomas/paracarpinteros-odoo.git
cd paracarpinteros-odoo/correos-cr-bridge
cp .env.example .env
nano .env   # rellenar credenciales reales
docker compose up -d
docker compose logs -f
```

## Endpoints

Todos excepto `/health` requieren header `X-API-Token: <API_TOKEN del .env>`.

- `GET /health` — liveness (sin auth)
- `GET /status` — último run, estadísticas
- `POST /process-now` — dispara una pasada manual
- `GET /test-correos` — prueba token + lista provincias
- `GET /test-odoo` — prueba autenticación XML-RPC + lista pickings pendientes

## Seguridad

- UFW permite solo 22, 80, 443
- fail2ban protege SSH
- Login SSH solo por clave (script `deploy/harden-ssh.sh`)
- API Token para endpoints sensibles
- Bridge escucha solo en 127.0.0.1 (nginx hace proxy)
