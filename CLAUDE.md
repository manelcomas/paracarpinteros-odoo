# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Monorepo del negocio Paracarpinteros (carpintería CR, Gabriela Brenes Solano). Una sola persona (Manel) desarrolla esto y suele tener **dos sesiones Claude en paralelo**: una local en WSL y otra en el VPS por SSH. Cuidado con sincronización — leer la sección "Multi-session workflow" antes de actuar.

Contiene **un módulo Odoo + tres microservicios independientes**, deployados a **dos targets distintos**:

| Pieza | Lenguaje | Target de deploy | Cómo se despliega |
|---|---|---|---|
| `delivery_correos_cr/` | Python (Odoo addon) | Odoo.sh (paracarpinteros.odoo.com) | `git push origin main` → Odoo.sh reconstruye. Luego en Odoo: **Apps → Update list → Install**. |
| `correos-cr-bridge/` | Python (FastAPI) | VPS Contabo `66.94.99.220` | SSH al VPS, `git pull && docker compose up -d --build` en `/opt/paracarpinteros-odoo/correos-cr-bridge/` |
| `calculadora/` | Python (HTTP server + HTML SPA) | Local del usuario + opcionalmente VPS | `docker compose up -d` en `calculadora/` |
| `fe-signer/` | PHP (Apache + CRLibre) | VPS Contabo | `git pull && docker compose up -d --build` en `/opt/paracarpinteros-odoo/fe-signer/` |

El panel web (`https://panel.paracarpinteros.com`) es **frontend que vive sólo en el VPS** y no está en este repo todavía. Llama a varios endpoints del bridge y del fe-signer. Si los cambios afectan al panel, hay que coordinarlos con la sesión Claude del VPS.

## Critical context for safe edits

### 1. El módulo `delivery_correos_cr` NO está instalado en producción

Está aquí por historia y para staging, pero el Odoo Online actual usa **Studio fields** para la geografía CR de los partners:

| Campo del módulo (este repo) | Campo Studio real en producción |
|---|---|
| `res.partner.correos_cr_provincia_code` | `state_id` (estándar, valor tipo `[1110, "San José (CR)"]`) |
| `res.partner.correos_cr_canton_code` | `x_studio_canton_cr` (Many2one → `x_canton_cr`, valor `[3, "Desamparados"]`) |
| `res.partner.correos_cr_distrito_code` | `x_studio_distrito_cr` (Many2one → `x_distrito_cr`) |
| `res.partner.correos_cr_zip` | `zip` (estándar) |
| — | `x_studio_senas` (text con las señas exactas) |

**Por eso `correos-cr-bridge/app/odoo_client.py:read_partner()` lee los Studio fields**, no los del módulo. Cualquier referencia a `correos_cr_*` en el bridge contra Odoo Online va a fallar con `KeyError`. Verificá siempre con `fields_get` antes de añadir campos nuevos al `read`.

### 2. El bridge corre con `WORKER_AUTO=0` por defecto

El polling automático de pickings está desactivado en producción. La generación de guías Pymexpress se dispara **solo desde el panel** (`POST /api/picking/{id}/generar`). El worker auto (`processor._process_one_locked`) existe y funciona, pero no se ejecuta a menos que `WORKER_AUTO=1` en el `.env`.

### 3. Estado del bridge en SQLite, no en Odoo

`correos-cr-bridge/data/panel.sqlite` guarda:
- `line_prepared` — checkboxes "preparado" por línea de picking
- `envio_manual` — guías Tavo/Dual generadas fuera de Pymexpress
- `entrega_mano` — entregas que no pasaron por courier

El archivo **vive en un volumen Docker** (`./data:/app/data`). Antes del 25 de mayo 2026, no estaba en volumen y se perdía con cada `--build`. Si trabajás con esta DB, asegurate que `data/` exista en el host antes de subir el container.

### 4. `correos-cr-bridge` y `delivery_correos_cr` cada uno tiene su `correos_cr_client.py`

Son **dos clientes SOAP distintos para el mismo WS de Correos**:
- `correos-cr-bridge/app/correos_client.py` — usado por el bridge (microservicio)
- `delivery_correos_cr/models/correos_cr_client.py` — usado por el módulo Odoo (no instalado en prod)

Cambios en el WS hay que reflejarlos en ambos. El bridge es el que se usa en producción.

### 5. Correos CR está en modo `production`

El `.env` real del bridge tiene `CORREOS_ENV=production` (el `.env.example` muestra `test`). **Scripts locales que llamen a `correos_client.py` pegan a Pymexpress real**. Para pruebas, exportar `CORREOS_ENV=test` antes del comando. Las guías generadas no cuestan dinero hasta que se entregan físicamente, pero generan ruido en el sistema de Correos.

## Common commands

### Develop locally with Python scripts (`scripts/`)

```bash
# Los scripts leen automáticamente el .env raíz (vía scripts/_env.py)
python3 scripts/crear_producto_emf9030.py --image /ruta/foto.jpg

# Test rápido de conexión a Odoo
python3 -c "
import sys, os; sys.path.insert(0, 'scripts')
from _env import load_project_env; load_project_env()
import xmlrpc.client
c = xmlrpc.client.ServerProxy(os.environ['ODOO_URL']+'/xmlrpc/2/common', allow_none=True)
print('uid:', c.authenticate(os.environ['ODOO_DB'], os.environ['ODOO_USERNAME'], os.environ['ODOO_API_KEY'], {}))
"
```

### Bridge (`correos-cr-bridge/`)

```bash
# Local: el código no se ejecuta fuera de Docker porque requiere zeep/pydantic-settings.
# Para testear sintaxis solamente:
python3 -m py_compile correos-cr-bridge/app/*.py

# En el VPS:
cd /opt/paracarpinteros-odoo/correos-cr-bridge
docker compose up -d --build
docker compose logs -f bridge

# Probar endpoints (requieren X-API-Token o sesión panel):
curl -s https://panel.paracarpinteros.com/health
curl -s -H "X-API-Token: $TOKEN" https://panel.paracarpinteros.com/test-odoo | jq
curl -s -H "X-API-Token: $TOKEN" https://panel.paracarpinteros.com/test-correos | jq
```

### Calculadora (`calculadora/`)

```bash
cd calculadora
docker compose up -d
# Sirve en http://127.0.0.1:8001
```

Para regenerar el `data/pedidos.db` desde cero: borrar `calculadora/data/pedidos.db` (gitignorado).

### Odoo module (`delivery_correos_cr/`)

```bash
# No hay deploy manual. Odoo.sh detecta el push y reconstruye.
git push origin main   # requiere "si push" para destrabar el classifier

# Despues en Odoo Online:
# Apps → Update list → buscar "Correos CR" → Install/Upgrade
```

### fe-signer (`fe-signer/`)

```bash
# En el VPS:
cd /opt/paracarpinteros-odoo/fe-signer
docker compose up -d --build
docker compose logs -f fe-signer

# Test de firma:
P12_B64=$(base64 -w0 /ruta/certificado.p12)
XML_B64=$(base64 -w0 factura_sin_firma.xml)
curl -s -X POST https://panel.paracarpinteros.com/sign \
  -H "X-API-Key: $SIGNER_API_KEY" \
  -d "{\"xmlBase64\":\"$XML_B64\",\"p12Base64\":\"$P12_B64\",\"pin\":\"1234\",\"tipoDoc\":\"01\"}"
```

### Sub-modules del fe-signer

- `fe-signer/buzon-rx/` — Recepción de FE recibidas desde Hacienda vía Gmail polling. Estados: pending/accepted/rejected/partial. Maneja Mensaje Receptor (MR-05/06/07).
- `fe-signer/alibaba-rx/` — Mismo patrón pero para parsear pedidos de Alibaba que llegan por Gmail.

Ambos comparten el patrón: `oauth-start.php` → `oauth-callback.php` → `poll.php` (cron) → `list.php` (lectura por panel) → `mr.php` o equivalente (acción).

## The `.env` baúl pattern

El proyecto tiene **un `.env` raíz** que centraliza credenciales para los scripts en `scripts/`:

- `/.env` — baúl (chmod 600, gitignored). Estructura en `.env.example`.
- `/scripts/_env.py` — cargador sin deps externas. Cualquier script en `scripts/` hace `from _env import load_project_env; load_project_env()`.

Cada servicio Docker tiene **su propio `.env`** dentro de su carpeta (`correos-cr-bridge/.env`, `fe-signer/.env`), porque vive dentro de su container y se monta con `env_file:` en su `docker-compose.yml`. No se intenta unificar — el bridge `.env` puede tener configuración que el baúl raíz no necesita y viceversa.

## Multi-session workflow

Manel suele tener una sesión Claude local (este repo) y otra Claude por SSH en el VPS al mismo tiempo. **Riesgos a evitar:**

1. **Editar código directamente en el VPS** — pasó histórico que el VPS tenía `/ocr/tavo`, alibaba-rx y otras cosas sin commitear. Causa drift y pérdida de cambios. **Regla: el VPS solo hace `git pull`**, nunca edita.
2. **Pushear cambios mientras la otra sesión edita** — antes de `git push`, preguntar al usuario si la otra sesión ha hecho cambios en el VPS sin commitear.
3. **Coordinar vía memoria** — el directorio `~/.claude/projects/-home-manel-proyectos-paracarpinteros-odoo/memory/` tiene `MEMORY.md` y notas que ambas sesiones pueden leer. Apuntar ahí cualquier hallazgo crítico (p.ej., qué está en producción y no en git).
4. **Push a `main` requiere confirmación explícita** del usuario con la frase "si push" — el clasificador del harness lo exige.

## Deployment targets summary

| Servicio | Donde corre | Cómo se entera de un cambio | Estado tras push |
|---|---|---|---|
| `delivery_correos_cr` | Odoo.sh staging/prod | Auto al push a main | Hay que ir a UI Odoo: Apps → Update list |
| `correos-cr-bridge` | VPS Contabo Docker | NO automatico | SSH + `git pull && docker compose up -d --build` |
| `fe-signer` | VPS Contabo Docker | NO automatico | SSH + `git pull && docker compose up -d --build` |
| `calculadora` | Local del usuario | NO automatico (es local) | `docker compose up -d` |

**No hay CI/CD que despliegue al VPS**. El único workflow GitHub Actions es `.github/workflows/uptime.yml` que solo monitorea `https://panel.paracarpinteros.com/health` cada 5 minutos y abre un issue si cae.

## Conventions

- **Lengua**: comentarios, mensajes de commit y strings de UI en **español**. Identificadores de código en español (no en inglés).
- **Commits**: una sola línea de scope + descripción, sin prefijos `feat:`/`fix:`. Ejemplos: `correos-cr-bridge: lock por picking`, `fe-signer/buzon-rx: emitir headers CORS`. Sin "Co-Authored-By: Claude" (el classifier lo rechaza).
- **No PRs**: solo dev, commits directos a `main`. Las branches solo se usan para Odoo.sh staging.
- **Permisos del `.env`**: siempre `chmod 600`. El gitignore ya cubre `.env`, `*.bak-*`.
